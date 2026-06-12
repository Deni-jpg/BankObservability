"""
Transaction Service — Banco Atlântico
"""
import os
import uuid
import random
import time
import logging
import traceback as tb_module
from datetime import datetime, timezone
from flask import Flask, request, jsonify
import requests as http
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

# ── Exceções de negócio — nomes descritivos aparecem na aba Exceptions do DT ──
class FraudDetectedException(RuntimeError):
    """Transação bloqueada pelo motor de fraude."""

class TransactionFailedException(RuntimeError):
    """Falha técnica ou de negócio ao processar transação."""

# ── Configuração ───────────────────────────────────────────────────────────────
SERVICE_NAME = "banco-transaction-service"
DT_TENANT    = (os.getenv("DT_TENANT") or "").rstrip("/")
DT_TOKEN     = os.getenv("DT_TOKEN") or ""
FRAUD_URL    = os.getenv("FRAUD_SERVICE_URL", "http://fraud-service:8003")

tracer = trace.get_tracer(SERVICE_NAME)
logger = logging.getLogger(SERVICE_NAME)

LATENCY_MS = {
    "mbway":          (40,  180),
    "multibanco":     (90,  350),
    "sepa":           (200, 1800),
    "cartao_debito":  (50,  220),
    "cartao_credito": (50,  220),
    "app_mobile":     (70,  280),
}
ERROR_RATES = {
    "mbway": 0.005, "multibanco": 0.008, "sepa": 0.012,
    "cartao_debito": 0.013, "cartao_credito": 0.016, "app_mobile": 0.007,
}
ERROR_CODES = ["TIMEOUT", "INSUFFICIENT_FUNDS", "INVALID_IBAN", "NETWORK_ERROR", "SERVICE_UNAVAILABLE"]
ERROR_META  = {
    "TIMEOUT":             {"category": "infrastructure", "severity": "HIGH",     "action": "check_upstream_latency"},
    "INSUFFICIENT_FUNDS":  {"category": "business",       "severity": "LOW",      "action": "notify_client"},
    "INVALID_IBAN":        {"category": "validation",     "severity": "MEDIUM",   "action": "verify_input"},
    "NETWORK_ERROR":       {"category": "infrastructure", "severity": "HIGH",     "action": "check_network_connectivity"},
    "SERVICE_UNAVAILABLE": {"category": "infrastructure", "severity": "CRITICAL", "action": "check_service_health"},
    "FRAUD_DETECTED":      {"category": "security",       "severity": "HIGH",     "action": "review_fraud_rules_and_block_iban"},
}
CUSTOMER_PROFILES = ["normal"] * 6 + ["premium", "premium", "enterprise", "youth", "senior"]
BANK_CODES        = ["0010", "0035", "0036", "0018", "0045"]

app = Flask(__name__)


def _iban_pt():
    return f"PT50{random.choice(BANK_CODES)}{''.join(str(random.randint(0,9)) for _ in range(19))}"


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
    como ERROR. O filepath vem do WORKDIR do container (/banco-atlantico/transaction-service)
    para que seja legível e identificável sem ambiguidade no Dynatrace.
    """
    frames = tb_module.extract_tb(exc.__traceback__)
    if frames:
        frame = frames[-1]
        span.set_attribute("code.filepath",  frame.filename)   # /banco-atlantico/transaction-service/main.py
        span.set_attribute("code.lineno",    frame.lineno)
        span.set_attribute("code.function",  frame.name)
        if frame.line:
            span.set_attribute("code.source_line", frame.line.strip())

    span.set_status(Status(StatusCode.ERROR, f"{type(exc).__name__}: {exc}"))
    span.record_exception(exc)


@app.route("/healthz")
def health():
    return jsonify({"status": "ok", "service": SERVICE_NAME})


@app.route("/process", methods=["POST"])
def process():
    body         = request.get_json(force=True) or {}
    canal        = body.get("canal", "mbway")
    valor        = float(body.get("valor_eur", 0.0))
    iban_origem  = body.get("iban_origem")  or _iban_pt()
    iban_destino = body.get("iban_destino") or _iban_pt()
    perfil       = random.choice(CUSTOMER_PROFILES)
    tx_id        = str(uuid.uuid4())

    span = trace.get_current_span()
    span.set_attribute("transaction.id",             tx_id)
    span.set_attribute("transaction.canal",          canal)
    span.set_attribute("transaction.valor_eur",      valor)
    span.set_attribute("transaction.iban_origem",    iban_origem)
    span.set_attribute("transaction.iban_destino",   iban_destino)
    span.set_attribute("transaction.perfil_cliente", perfil)

    min_ms, max_ms = LATENCY_MS.get(canal, (100, 500))
    latencia_ms    = random.randint(min_ms, max_ms)
    time.sleep(latencia_ms / 1000)

    force_fraud = body.get("force_fraud", False)
    fraud_result = {"status": "allowed", "score": 0, "rules_triggered": []}
    try:
        r = http.post(f"{FRAUD_URL}/check", json={
            "transaction_id": tx_id, "canal": canal,
            "valor_eur": valor, "iban_destino": iban_destino, "perfil_cliente": perfil,
            "force_fraud": force_fraud,
        }, timeout=8)
        fraud_result = r.json()
    except Exception as e:
        span.record_exception(e)

    span.set_attribute("fraud.score",  fraud_result.get("score", 0))
    span.set_attribute("fraud.status", fraud_result.get("status", "unknown"))

    # ── Decisões de negócio — cada raise gera um traceback real com ficheiro+linha ──
    status     = "success"
    error_code = None

    try:
        if fraud_result.get("status") == "blocked":
            rules = fraud_result.get("rules_triggered", [])
            score = fraud_result.get("score", 0)
            raise FraudDetectedException(
                f"canal={canal} valor={valor:.2f}€ "
                f"score={score}/60 regras={','.join(rules)} perfil={perfil}"
            )

        if random.random() < ERROR_RATES.get(canal, 0.01):
            error_code = random.choice(ERROR_CODES)
            meta       = ERROR_META.get(error_code, {})
            raise TransactionFailedException(
                f"error_code={error_code} "
                f"category={meta.get('category','unknown')} "
                f"severity={meta.get('severity','?')} "
                f"canal={canal} valor={valor:.2f}€ latencia_ms={latencia_ms}"
            )

    except FraudDetectedException as exc:
        status     = "blocked"
        error_code = "FRAUD_DETECTED"
        _apply_code_context(span, exc)
        logger.warning(
            "transaction_blocked — fraude: valor=%.2f canal=%s score=%d regras=%s",
            valor, canal, fraud_result.get("score", 0),
            ",".join(fraud_result.get("rules_triggered", [])),
            extra={
                "transaction.canal":          canal,
                "transaction.valor_eur":      valor,
                "transaction.perfil_cliente": perfil,
                "transaction.id":             tx_id,
                "fraud.score":                fraud_result.get("score", 0),
                "fraud.rules_triggered":      ",".join(fraud_result.get("rules_triggered", [])),
                "error.category":             "security",
                "error.recommended_action":   ERROR_META["FRAUD_DETECTED"]["action"],
            },
        )

    except TransactionFailedException as exc:
        status = "error"
        meta   = ERROR_META.get(error_code or "", {"category": "unknown", "severity": "MEDIUM", "action": "investigate"})
        _apply_code_context(span, exc)
        logger.error(
            "transaction_failed — %s: error=%s canal=%s valor=%.2f latencia_ms=%d",
            meta["category"], error_code, canal, valor, latencia_ms,
            extra={
                "transaction.canal":          canal,
                "transaction.valor_eur":      valor,
                "transaction.latencia_ms":    latencia_ms,
                "transaction.perfil_cliente": perfil,
                "transaction.id":             tx_id,
                "transaction.iban_destino":   f"****{iban_destino[-4:]}",
                "error.code":                 error_code,
                "error.category":             meta["category"],
                "error.severity":             meta["severity"],
                "error.recommended_action":   meta["action"],
            },
        )

    if status == "success":
        logger.info(
            "transaction_success",
            extra={
                "transaction.canal":          canal,
                "transaction.valor_eur":      valor,
                "transaction.latencia_ms":    latencia_ms,
                "transaction.perfil_cliente": perfil,
                "transaction.fraud_score":    fraud_result.get("score", 0),
                "transaction.id":             tx_id,
            },
        )

    span.set_attribute("transaction.status", status)
    if error_code:
        span.set_attribute("transaction.error_code", error_code)

    trace_id = format(span.get_span_context().trace_id, "032x")

    _send_bizevent({
        "event.provider": "banco.core",
        "event.type":     f"transaction.{canal}",
        "timestamp":      datetime.now(timezone.utc).isoformat(),
        "transaction_id": tx_id,
        "canal": canal, "status": status, "valor_eur": valor,
        "latencia_ms": latencia_ms, "iban_origem": iban_origem,
        "iban_destino": iban_destino, "perfil_cliente": perfil,
        "fraud_score": fraud_result.get("score", 0), "trace_id": trace_id,
        **({"error_code": error_code} if error_code else {}),
    })

    resp = {"transaction_id": tx_id, "status": status, "canal": canal,
            "valor_eur": valor, "latencia_ms": latencia_ms, "trace_id": trace_id}
    if error_code:
        resp["error_code"] = error_code
    return jsonify(resp), 200 if status in ("success", "blocked") else 500


if __name__ == "__main__":
    print("💳 Transaction Service → http://0.0.0.0:8002")
    app.run(host="0.0.0.0", port=8002, debug=False)
