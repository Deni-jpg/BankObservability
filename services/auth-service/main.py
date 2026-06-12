"""
Auth Service — Banco Atlântico
"""
import os
import uuid
import random
import logging
from datetime import datetime, timezone
from flask import Flask, request, jsonify
import requests as http
from opentelemetry import trace

SERVICE_NAME = "banco-auth-service"
DT_TENANT    = (os.getenv("DT_TENANT") or "").rstrip("/")
DT_TOKEN     = os.getenv("DT_TOKEN") or ""

tracer = trace.get_tracer(SERVICE_NAME)
logger = logging.getLogger(SERVICE_NAME)

AUTH_METHODS = ["PIN", "biometrico", "chave_digital", "token_sms"]
COUNTRIES = {
    "success": ["PT", "PT", "PT", "PT", "BR", "ES", "DE"],
    "failure": ["PT", "BR", "US", "NG", "CN", "RO", "UA"],
}

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


@app.route("/healthz")
def health():
    return jsonify({"status": "ok", "service": SERVICE_NAME})


@app.route("/login", methods=["POST"])
def login():
    body     = request.get_json(force=True) or {}
    session_id = str(uuid.uuid4())
    metodo   = body.get("metodo") or random.choice(AUTH_METHODS)
    user_id  = body.get("user_id") or f"USR-{random.randint(1000, 9999)}"

    failure_rate = float(os.getenv("AUTH_FAILURE_RATE", "0.25"))
    if body.get("force_success"):
        is_success = True
    elif body.get("force_fail"):
        is_success = False
    else:
        is_success = random.random() > failure_rate
    status       = "success" if is_success else "failure"
    pais         = random.choice(COUNTRIES[status])

    span = trace.get_current_span()
    span.set_attribute("auth.method",         metodo)
    span.set_attribute("auth.status",         status)
    span.set_attribute("auth.user_id",        user_id)
    span.set_attribute("auth.country_origin", pais)
    span.set_attribute("auth.session_id",     session_id)

    trace_id = format(span.get_span_context().trace_id, "032x")

    # Países considerados de alto risco para logins falhados
    HIGH_RISK_COUNTRIES = {"NG", "CN", "RO", "UA", "RU"}
    is_high_risk_country = pais in HIGH_RISK_COUNTRIES

    if not is_success:
        span.set_attribute("error", True)
        logger.warning(
            "login_failed — método=%s origem=%s risco=%s user=%s",
            metodo, pais,
            "HIGH" if is_high_risk_country else "NORMAL",
            user_id,
            extra={
                "auth.user_id":              user_id,
                "auth.method":               metodo,
                "auth.country_origin":       pais,
                "auth.session_id":           session_id,
                "auth.failure_reason":       "INVALID_CREDENTIALS",
                "auth.high_risk_country":    str(is_high_risk_country),
                "auth.risk_level":           "HIGH" if is_high_risk_country else "NORMAL",
                "error.recommended_action":  "monitor_for_brute_force" if is_high_risk_country else "standard_retry_policy",
            },
        )
    else:
        logger.info(
            "login_success método=%s origem=%s user=%s",
            metodo, pais, user_id,
            extra={
                "auth.user_id":        user_id,
                "auth.method":         metodo,
                "auth.country_origin": pais,
                "auth.session_id":     session_id,
            },
        )

    _send_bizevent({
        "event.provider": "banco.auth",
        "event.type":     "login.attempt",
        "timestamp":      datetime.now(timezone.utc).isoformat(),
        "session_id":     session_id,
        "status":         status,
        "metodo_autenticacao": metodo,
        "pais_origem":    pais,
        "user_id":        user_id,
        "trace_id":       trace_id,
    })

    if not is_success:
        return jsonify({"session_id": session_id, "status": "failure",
                        "error": "INVALID_CREDENTIALS", "pais": pais, "trace_id": trace_id}), 401

    return jsonify({"session_id": session_id, "status": "success", "user_id": user_id,
                    "metodo": metodo, "token": str(uuid.uuid4()), "trace_id": trace_id}), 200


if __name__ == "__main__":
    print("🔐 Auth Service → http://0.0.0.0:8001")
    app.run(host="0.0.0.0", port=8001, debug=False)
