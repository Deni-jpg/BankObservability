"""
API Gateway — Banco Atlântico
opentelemetry-instrument inicializa OTel automaticamente via env vars.
Nenhum setup manual necessário — código de negócio apenas.
"""
import os
import time
import logging
import traceback as tb_module
from flask import Flask, request, jsonify
import requests as http
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

SERVICE_NAME = "banco-api-gateway"
tracer = trace.get_tracer(SERVICE_NAME)
logger  = logging.getLogger(SERVICE_NAME)

class DatabaseTimeoutError(RuntimeError):
    """Timeout na ligação à base de dados de transações."""

class PaymentProcessingError(RuntimeError):
    """Falha crítica no motor de processamento de pagamentos."""

AUTH_URL        = os.getenv("AUTH_SERVICE_URL",        "http://auth-service:8001")
TRANSACTION_URL = os.getenv("TRANSACTION_SERVICE_URL", "http://transaction-service:8002")
FRAUD_URL       = os.getenv("FRAUD_SERVICE_URL",       "http://fraud-service:8003")

app = Flask(__name__)


@app.after_request
def cors(r):
    r.headers["Access-Control-Allow-Origin"]  = "*"
    r.headers["Access-Control-Allow-Headers"] = "Content-Type"
    r.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return r


@app.route("/healthz")
def health():
    return jsonify({"status": "ok", "service": SERVICE_NAME})


@app.route("/api/auth/login", methods=["POST", "OPTIONS"])
def login():
    if request.method == "OPTIONS":
        return "", 204
    span = trace.get_current_span()
    span.set_attribute("gateway.upstream", "auth-service")
    r = http.post(f"{AUTH_URL}/login", json=request.get_json(force=True), timeout=10)
    return jsonify(r.json()), r.status_code


@app.route("/api/transaction", methods=["POST", "OPTIONS"])
def transaction():
    if request.method == "OPTIONS":
        return "", 204
    body = request.get_json(force=True) or {}
    span = trace.get_current_span()
    span.set_attribute("gateway.upstream",        "transaction-service")
    span.set_attribute("transaction.canal",        body.get("canal", "unknown"))
    span.set_attribute("transaction.valor_eur",    float(body.get("valor_eur", 0)))
    r = http.post(f"{TRANSACTION_URL}/process", json=body, timeout=20)
    return jsonify(r.json()), r.status_code


@app.route("/api/fraud/check", methods=["POST", "OPTIONS"])
def fraud():
    if request.method == "OPTIONS":
        return "", 204
    span = trace.get_current_span()
    span.set_attribute("gateway.upstream", "fraud-service")
    r = http.post(f"{FRAUD_URL}/check", json=request.get_json(force=True), timeout=10)
    return jsonify(r.json()), r.status_code


@app.route("/api/demo/timeout", methods=["POST", "OPTIONS"])
def demo_timeout():
    if request.method == "OPTIONS":
        return "", 204
    span = trace.get_current_span()
    span.set_attribute("demo.type", "timeout_simulation")
    span.set_attribute("db.system", "postgresql")
    span.set_attribute("db.name", "payment_db")
    span.set_attribute("db.statement", "SELECT * FROM transactions WHERE account_id = ? LIMIT 1000")

    time.sleep(2)  # simula latência antes do timeout

    try:
        raise DatabaseTimeoutError(
            "payment-db:5432 — connection pool exhausted (50/50 active). "
            "Query cancelled after 2000ms. Possible deadlock in transactions table."
        )
    except DatabaseTimeoutError as exc:
        frames = tb_module.extract_tb(exc.__traceback__)
        if frames:
            f = frames[-1]
            span.set_attribute("code.filepath", f.filename)
            span.set_attribute("code.lineno",   f.lineno)
            span.set_attribute("code.function", f.name)
        span.set_status(Status(StatusCode.ERROR, str(exc)))
        span.record_exception(exc)
        logger.error(
            "database_timeout — pool exhausted: %s", exc,
            extra={
                "demo.type":           "timeout",
                "db.host":             "payment-db:5432",
                "db.pool.active":      50,
                "db.pool.max":         50,
                "db.query_time_ms":    2000,
                "error.severity":      "HIGH",
                "error.recommended_action": "scale_db_pool_or_add_read_replica",
            },
        )
        return jsonify({"error": "Database Timeout", "code": "DB_CONNECTION_POOL_EXHAUSTED"}), 504


@app.route("/api/demo/crash", methods=["POST", "OPTIONS"])
def demo_crash():
    if request.method == "OPTIONS":
        return "", 204
    span = trace.get_current_span()
    span.set_attribute("demo.type", "crash_simulation")
    try:
        raise PaymentProcessingError(
            "NullPointerException in PaymentProcessor.validateAccount() — "
            "account_id=None after Redis cache invalidation at 04:17 UTC"
        )
    except PaymentProcessingError as exc:
        frames = tb_module.extract_tb(exc.__traceback__)
        if frames:
            f = frames[-1]
            span.set_attribute("code.filepath", f.filename)
            span.set_attribute("code.lineno",   f.lineno)
            span.set_attribute("code.function", f.name)
        span.set_status(Status(StatusCode.ERROR, str(exc)))
        span.record_exception(exc)
        logger.error(
            "demo_crash — PaymentProcessingError: %s", exc,
            extra={"demo.type": "crash", "error.severity": "CRITICAL",
                   "error.component": "PaymentProcessor", "error.method": "validateAccount",
                   "error.root_cause": "Redis cache invalidation — account_id became None"},
        )
        return jsonify({"error": "Internal Server Error", "code": "PAYMENT_PROCESSOR_FAILURE"}), 500


if __name__ == "__main__":
    print("🚪 API Gateway → http://0.0.0.0:8000")
    app.run(host="0.0.0.0", port=8000, debug=False)
