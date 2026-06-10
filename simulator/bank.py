"""
Banco Atlântico — Traffic Simulator v2
Gera tráfego realista ATRAVÉS da API (não directamente para Dynatrace).
Cada operação cria um trace distribuído real: Gateway → Service → Fraud.
"""
import os
import random
import time
import threading
from datetime import datetime, timezone

import requests

API_URL = os.getenv("API_GATEWAY_URL", "http://localhost:8000")
RATE    = float(os.getenv("EVENTS_PER_SECOND", "10"))

# ─── Perfis de tráfego (peso relativo) ───────────────────────────────────────
CANAIS = [
    {"canal": "mbway",          "peso": 32, "min": 5,    "max": 500  },
    {"canal": "multibanco",     "peso": 22, "min": 10,   "max": 200  },
    {"canal": "sepa",           "peso": 8,  "min": 50,   "max": 8000 },
    {"canal": "cartao_debito",  "peso": 18, "min": 5,    "max": 300  },
    {"canal": "cartao_credito", "peso": 10, "min": 10,   "max": 1200 },
    {"canal": "app_mobile",     "peso": 10, "min": 5,    "max": 250  },
]
TOTAL_PESO = sum(c["peso"] for c in CANAIS)

BANK_CODES = ["0010", "0035", "0036", "0018", "0045"]
AUTH_METHODS = ["PIN", "biometrico", "chave_digital", "token_sms"]

# ─── Gestão de incidentes (simula anomalias reais) ───────────────────────────
_incident = {"active": False, "tipo": None, "canal": None, "fim": 0}
_incident_lock = threading.Lock()


def _maybe_trigger_incident():
    with _incident_lock:
        if _incident["active"] and time.time() < _incident["fim"]:
            return
        if random.random() < 0.04:  # 4% chance por ciclo de avaliação
            tipos = ["outage", "slowdown", "login_attack"]
            tipo  = random.choice(tipos)
            canal = random.choice(CANAIS)["canal"]
            dur   = random.randint(90, 300)
            _incident.update(active=True, tipo=tipo, canal=canal, fim=time.time() + dur)
            print(f"\n🚨 INCIDENTE: {tipo.upper()} em {canal} por {dur}s\n")
        else:
            _incident["active"] = False


def _get_incident():
    with _incident_lock:
        if _incident["active"] and time.time() < _incident["fim"]:
            return _incident.copy()
    return None


# ─── Funções de envio ─────────────────────────────────────────────────────────
def _iban_pt():
    codigo = random.choice(BANK_CODES)
    resto  = "".join(str(random.randint(0, 9)) for _ in range(19))
    return f"PT50{codigo}{resto}"


def _escolher_canal():
    r = random.uniform(0, TOTAL_PESO)
    for c in CANAIS:
        r -= c["peso"]
        if r <= 0:
            return c
    return CANAIS[0]


def enviar_transacao():
    c   = _escolher_canal()
    inc = _get_incident()

    canal = c["canal"]
    valor = round(random.uniform(c["min"], c["max"]), 2)

    # Durante outage, força transações no canal afectado (para gerar erros visíveis)
    if inc and inc["tipo"] == "outage" and random.random() < 0.6:
        canal = inc["canal"]

    try:
        r = requests.post(
            f"{API_URL}/api/transaction",
            json={"canal": canal, "valor_eur": valor, "iban_origem": _iban_pt()},
            timeout=20,
        )
        return r.status_code, r.json().get("status", "?"), canal, valor
    except Exception as e:
        return 0, "network_error", canal, valor


def enviar_login():
    inc = _get_incident()
    # Durante login_attack: taxa de falha muito mais alta
    extra_rate = 0.70 if (inc and inc["tipo"] == "login_attack") else 0.0

    try:
        r = requests.post(
            f"{API_URL}/api/auth/login",
            json={"metodo": random.choice(AUTH_METHODS)},
            timeout=10,
        )
        return r.status_code, r.json().get("status", "?")
    except Exception as e:
        return 0, "network_error"


def enviar_fraude():
    valor = round(random.uniform(500, 15000), 2)
    canal = random.choice(CANAIS)["canal"]
    try:
        r = requests.post(
            f"{API_URL}/api/fraud/check",
            json={"canal": canal, "valor_eur": valor, "iban_destino": _iban_pt()},
            timeout=10,
        )
        return r.status_code, r.json().get("status", "?")
    except Exception as e:
        return 0, "network_error"


# ─── Loop principal ───────────────────────────────────────────────────────────
def main():
    print(f"""
╔══════════════════════════════════════════════════════╗
║     Banco Atlântico — Traffic Simulator v2          ║
║  Gerando traces distribuídos reais no Dynatrace     ║
╠══════════════════════════════════════════════════════╣
║  API Gateway : {API_URL:<37}║
║  Taxa base   : {RATE:<5.0f} op/s                              ║
╚══════════════════════════════════════════════════════╝
    """)

    # Espera o gateway arrancar
    for _ in range(30):
        try:
            r = requests.get(f"{API_URL}/healthz", timeout=3)
            if r.ok:
                print("✅ Gateway disponível. Iniciando geração de tráfego...\n")
                break
        except Exception:
            pass
        print("⏳ Aguardando API Gateway...")
        time.sleep(5)

    # Heurística de hora do dia (multiplicador de carga)
    def hora_factor():
        h = datetime.now(timezone.utc).hour
        if 8 <= h <= 11 or 18 <= h <= 20:
            return 1.4   # pico
        if 0 <= h <= 5:
            return 0.3   # noite
        return 1.0

    sent = errors = 0
    t_last_report  = time.time()
    t_last_incident = time.time()
    delay = 1.0 / max(RATE, 1)

    while True:
        t0 = time.time()

        # Avalia incidentes a cada 60s
        if time.time() - t_last_incident > 60:
            _maybe_trigger_incident()
            t_last_incident = time.time()

        # Ajusta taxa pela hora do dia
        effective_delay = delay / hora_factor()

        roll = random.random()
        if roll < 0.72:
            code, status, canal, valor = enviar_transacao()
            if status in ("error", "blocked", "network_error"):
                errors += 1
        elif roll < 0.90:
            code, status = enviar_login()
            if status in ("failure", "network_error"):
                errors += 1
            canal, valor = "auth", 0
        else:
            code, status = enviar_fraude()
            canal, valor = "fraud", 0
            if status == "network_error":
                errors += 1

        sent += 1

        # Relatório a cada 10s
        if time.time() - t_last_report >= 10:
            inc = _get_incident()
            inc_str = f" | 🚨 {inc['tipo'].upper()} em {inc['canal']}" if inc else ""
            print(
                f"[{datetime.now().strftime('%H:%M:%S')}] "
                f"Enviados: {sent:>6} | Erros: {errors:>4} | "
                f"Taxa: {RATE * hora_factor():.0f}/s{inc_str}"
            )
            t_last_report = time.time()

        # Controlo de ritmo
        elapsed = time.time() - t0
        sleep   = effective_delay - elapsed
        if sleep > 0:
            time.sleep(sleep)


if __name__ == "__main__":
    main()
