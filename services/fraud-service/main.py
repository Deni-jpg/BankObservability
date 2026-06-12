"""
Fraud Service — Banco Atlântico
"""
import os
import uuid
import random
import logging
import traceback as tb_module
from datetime import datetime, timezone
from flask import Flask, request, jsonify
import requests as http
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

class FraudBlockedException(RuntimeError):
    """Motor de fraude bloqueou a transação — score excedeu o threshold."""

SERVICE_NAME = "banco-fraud-service"
DT_TENANT    = (os.getenv("DT_TENANT") or "").rstrip("/")
DT_TOKEN     = os.getenv("DT_TOKEN") or ""

tracer = trace.get_tracer(SERVICE_NAME)
logger = logging.getLogger(SERVICE_NAME)

RULES = {
    "velocity_check":        {"weight": 20, "prob": 0.04, "desc": "Demasiadas transações num curto período"},
    "value_anomaly":         {"weight": 25, "prob": 0.03, "desc": "Valor acima do padrão histórico do cliente"},
    "geolocation_mismatch":  {"weight": 20, "prob": 0.05, "desc": "País de origem inconsistente com perfil"},
    "device_fingerprint":    {"weight": 15, "prob": 0.04, "desc": "Dispositivo desconhecido ou alterado"},
    "beneficiary_watchlist": {"weight": 35, "prob": 0.02, "desc": "IBAN destinatário em lista de risco"},
    "time_pattern":          {"weight": 10, "prob": 0.06, "desc": "Horário fora do padrão habitual"},
    "ip_reputation":         {"weight": 20, "prob": 0.03, "desc": "IP associado a atividade suspeita"},
}
BLOCK_THRESHOLD = 60

app = Flask(__name__)


def _send_bizevent(event: dict):
    if not DT_TENANT or not DT_TOKEN:
        return
    try:
        http.post(
            f"{DT_TENANT}/api/v2/bizevents/ingest",
            headers={"Authorization": f"Api-Token {DT_TOKEN}", "Content-Type": "application/json"},
            json=[event], timeout=5,
        )
    except Exception:
        pass


def _apply_code_context(span, exc: BaseException):
    """
    Usa o traceback real para preencher os atributos code.* e marcar o span
    como ERROR. O filepath vem do WORKDIR do container (/banco-atlantico/fraud-service)
    para que seja legível e identificável sem ambiguidade no Dynatrace.
    """
    frames = tb_module.extract_tb(exc.__traceback__)
    if frames:
        frame = frames[-1]
        span.set_attribute("code.filepath",  frame.filename)   # /banco-atlantico/fraud-service/main.py
        span.set_attribute("code.lineno",    frame.lineno)
        span.set_attribute("code.function",  frame.name)
        if frame.line:
            span.set_attribute("code.source_line", frame.line.strip())

    span.set_status(Status(StatusCode.ERROR, f"{type(exc).__name__}: {exc}"))
    span.record_exception(exc)


@app.route("/healthz")
def health():
    return jsonify({"status": "ok", "service": SERVICE_NAME})


@app.route("/check", methods=["POST"])
def check():
    body   = request.get_json(force=True) or {}
    tx_id  = body.get("transaction_id", str(uuid.uuid4()))
    canal  = body.get("canal", "unknown")
    valor  = float(body.get("valor_eur", 0))
    perfil = body.get("perfil_cliente", "normal")

    force_fraud = body.get("force_fraud", False)
    score, triggered = 0, []

    if force_fraud:
        triggered = ["velocity_check", "value_anomaly", "beneficiary_watchlist"]
        score = 80
    else:
        if valor > 5000:
            score += 30
            triggered.append("value_anomaly")
        elif valor > 2000:
            score += 15

        for rule, cfg in RULES.items():
            if rule not in triggered and random.random() < cfg["prob"]:
                score += cfg["weight"]
                triggered.append(rule)

    threshold = BLOCK_THRESHOLD + (20 if perfil == "enterprise" else 0)
    is_blocked = force_fraud or score >= threshold
    status     = "blocked" if is_blocked else "allowed"

    span      = trace.get_current_span()
    span.set_attribute("fraud.score",          score)
    span.set_attribute("fraud.threshold",      threshold)
    span.set_attribute("fraud.status",         status)
    span.set_attribute("fraud.rules_count",    len(triggered))
    span.set_attribute("fraud.transaction_id", tx_id)

    trace_id     = format(span.get_span_context().trace_id, "032x")
    risk_level   = "CRITICAL" if score >= 85 else ("HIGH" if score >= 60 else "MEDIUM" if score >= 30 else "LOW")
    rule_descs   = [RULES[r]["desc"] for r in triggered if r in RULES]

    try:
        if is_blocked:
            raise FraudBlockedException(
                f"score={score}/{threshold} risk={risk_level} "
                f"canal={canal} valor={valor:.2f}€ perfil={perfil} "
                f"rules=[{', '.join(triggered)}]"
            )

    except FraudBlockedException as exc:
        _apply_code_context(span, exc)
        logger.warning(
            "fraud_blocked — score=%d/%d risco=%s canal=%s valor=%.2f motivos: %s",
            score, threshold, risk_level, canal, valor,
            " | ".join(rule_descs) if rule_descs else "threshold excedido",
            extra={
                "fraud.score":             score,
                "fraud.threshold":         threshold,
                "fraud.risk_level":        risk_level,
                "fraud.rules_triggered":   ",".join(triggered),
                "fraud.rules_count":       len(triggered),
                "fraud.canal":             canal,
                "fraud.valor_eur":         valor,
                "fraud.perfil_cliente":    perfil,
                "fraud.transaction_id":    tx_id,
                "fraud.rule_descriptions": " | ".join(rule_descs),
                "error.recommended_action": "block_transaction_and_alert_security_team",
            },
        )

    if not is_blocked:
        logger.info(
            "fraud_allowed score=%d/%d risco=%s canal=%s valor=%.2f",
            score, threshold, risk_level, canal, valor,
            extra={
                "fraud.score":          score,
                "fraud.threshold":      threshold,
                "fraud.risk_level":     risk_level,
                "fraud.canal":          canal,
                "fraud.valor_eur":      valor,
                "fraud.transaction_id": tx_id,
            },
        )

    _send_bizevent({
        "event.provider": "banco.fraude",
        "event.type":     "fraud.check",
        "timestamp":      datetime.now(timezone.utc).isoformat(),
        "fraud_id":       str(uuid.uuid4()),
        "transaction_id": tx_id,
        "canal": canal, "valor_eur": valor, "perfil_cliente": perfil,
        "status": status, "score": score,
        "regras_ativas": ",".join(triggered) if triggered else "none",
        "trace_id": trace_id,
    })

    return jsonify({
        "transaction_id": tx_id, "status": status,
        "score": score, "rules_triggered": triggered, "threshold": threshold,
    })


if __name__ == "__main__":
    print("🛡️  Fraud Service → http://0.0.0.0:8003")
    app.run(host="0.0.0.0", port=8003, debug=False)
