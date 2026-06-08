"""
Simulador bancário português realista — Dynatrace BizEvents
============================================================
- Volume alto (50-100 eventos/s)
- Padrões diurnos (manhã, almoço, tarde, noite)
- Cenários de incidentes (outages, fraude em massa, slowdowns)
- Múltiplos perfis de cliente
- Envia em LOTES (mais eficiente)

Uso:
    pip install -r requirements.txt
    # edita .env com o teu token
    python banco_simulador.py
"""

import requests
import time
import random
import uuid
import threading
import os
from pathlib import Path
from datetime import datetime, timezone
import sys

# Carrega .env
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).resolve().parent / ".env"
    load_dotenv(env_path)
except ImportError:
    print("⚠️  Falta python-dotenv. Roda: pip install -r requirements.txt")
    sys.exit(1)

# ============================================
#  CONFIGURAÇÃO (lida de .env)
# ============================================
DT_TENANT = (os.getenv("DT_TENANT") or "").rstrip("/")
DT_TOKEN  = os.getenv("DT_TOKEN") or ""
BASE_RATE_PER_SECOND = int(os.getenv("EVENTS_PER_SECOND", "80"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "50"))
# ============================================

DT_TENANT = DT_TENANT.rstrip("/")
ENDPOINT = f"{DT_TENANT}/api/v2/bizevents/ingest"
HEADERS = {
    "Authorization": f"Api-Token {DT_TOKEN}",
    "Content-Type": "application/json"   # JSON flat (não CloudEvents)
}

# ---------- DOMAIN MODEL ----------

CANAIS = {
    "mbway":              0.32,
    "multibanco":         0.22,
    "cartao_debito":      0.18,
    "cartao_credito":     0.10,
    "transferencia_sepa": 0.06,
    "app_mobile":         0.07,
    "homebanking":        0.05,
}

LATENCIA_MS = {
    "mbway":              (40, 150),
    "multibanco":         (60, 250),
    "cartao_debito":      (50, 250),
    "cartao_credito":     (60, 350),
    "transferencia_sepa": (200, 1500),
    "app_mobile":         (150, 900),
    "homebanking":        (100, 500),
}

VALORES_EUR = {
    "mbway":              (1, 1000),
    "multibanco":         (5, 500),
    "cartao_debito":      (5, 300),
    "cartao_credito":     (10, 2000),
    "transferencia_sepa": (50, 20000),
    "app_mobile":         (1, 1500),
    "homebanking":        (10, 10000),
}

TAXA_ERRO_BASE = {
    "app_mobile":         0.025,
    "cartao_credito":     0.018,
    "homebanking":        0.012,
    "transferencia_sepa": 0.010,
    "multibanco":         0.008,
    "cartao_debito":      0.006,
    "mbway":              0.005,
}

SERVICOS_POR_CANAL = {
    "mbway":              ["enviar_dinheiro", "pedir_dinheiro", "dividir_conta", "pagamento_qr"],
    "multibanco":         ["pagamento_servicos", "pagamento_referencia", "levantamento", "consulta_saldo"],
    "cartao_debito":      ["pagamento_loja", "pagamento_online", "levantamento_atm"],
    "cartao_credito":     ["pagamento_loja", "pagamento_online", "pagamento_recorrente", "cashback"],
    "transferencia_sepa": ["transferencia_normal", "transferencia_urgente", "pagamento_salario"],
    "app_mobile":         ["consulta_saldo", "transferencia", "extrato", "investimento", "credito"],
    "homebanking":        ["transferencia", "pagamento_servicos", "extrato", "investimento"],
}

ERROR_CODES = {
    "TIMEOUT_SIBS":             "Timeout no gateway SIBS",
    "MBWAY_NAO_REGISTADO":      "Telemóvel não registado no MB WAY",
    "SALDO_INSUFICIENTE":       "Saldo insuficiente para a operação",
    "LIMITE_DIARIO_EXCEDIDO":   "Limite diário ultrapassado",
    "AUTORIZACAO_NEGADA":       "Autorização negada pela rede",
    "CARTAO_BLOQUEADO":         "Cartão bloqueado por segurança",
    "PIN_INCORRECTO":           "PIN incorrecto",
    "IBAN_INVALIDO":            "Formato de IBAN inválido",
    "GATEWAY_INDISPONIVEL":     "Gateway de pagamento indisponível",
    "FRAUDE_DETECTADA":         "Padrão suspeito detectado",
}

REGRAS_FRAUDE = ["VELOCIDADE_ANORMAL", "VALOR_ATIPICO", "GEOLOCALIZACAO_ESTRANGEIRO",
                 "DISPOSITIVO_NOVO", "HORARIO_INCOMUM", "BENEFICIARIO_SUSPEITO"]

BANCOS_PT = ["0035", "0033", "0010", "0007", "0036", "0018", "0193", "0269"]

# Perfis de cliente
PERFIS_CLIENTE = {
    "premium":   {"weight": 0.05, "valor_mult": 5.0, "canais_pref": ["transferencia_sepa", "homebanking", "cartao_credito"]},
    "empresa":   {"weight": 0.08, "valor_mult": 8.0, "canais_pref": ["transferencia_sepa", "homebanking", "multibanco"]},
    "normal":    {"weight": 0.60, "valor_mult": 1.0, "canais_pref": ["mbway", "cartao_debito", "multibanco", "app_mobile"]},
    "jovem":     {"weight": 0.20, "valor_mult": 0.5, "canais_pref": ["mbway", "app_mobile", "cartao_debito"]},
    "senior":    {"weight": 0.07, "valor_mult": 0.7, "canais_pref": ["multibanco", "homebanking", "cartao_debito"]},
}

# ---------- ESTADO GLOBAL ----------

class Estado:
    def __init__(self):
        self.incidente_ativo = None   # ex: {"canal": "mbway", "tipo": "outage", "fim": timestamp}
        self.ataque_fraude   = False
        self.ataque_login    = False
        self.contador        = 0
        self.lock            = threading.Lock()

estado = Estado()

# ---------- HELPERS ----------

def agora_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def iban_pt():
    banco = random.choice(BANCOS_PT)
    return f"PT50{banco}{random.randint(0,9999):04d}{random.randint(0,99999999999):011d}{random.randint(0,99):02d}"

def telefone_pt():
    return f"+3519{random.choice(['1','2','3','6'])}{random.randint(1000000, 9999999)}"

def escolher_perfil():
    r, cum = random.random(), 0.0
    for nome, dados in PERFIS_CLIENTE.items():
        cum += dados["weight"]
        if r < cum:
            return nome, dados
    return "normal", PERFIS_CLIENTE["normal"]

def escolher_canal(perfil_dados):
    # 70% chance de usar canal preferido do perfil
    if random.random() < 0.70:
        return random.choice(perfil_dados["canais_pref"])
    r, cum = random.random(), 0.0
    for canal, prob in CANAIS.items():
        cum += prob
        if r < cum:
            return canal
    return "mbway"

def fator_hora_dia():
    """Padrão diurno: pico de manhã, almoço, fim de tarde"""
    h = datetime.now().hour
    if 6 <= h < 9:    return 0.4   # manhã cedo
    if 9 <= h < 12:   return 1.0   # comercial
    if 12 <= h < 14:  return 1.4   # pico de almoço
    if 14 <= h < 18:  return 1.1   # tarde
    if 18 <= h < 22:  return 1.3   # pós-trabalho
    if 22 <= h < 24:  return 0.5   # noite
    return 0.2                     # madrugada (0-6h)

# ---------- INCIDENTES ----------

def maybe_disparar_incidente():
    """A cada 60s tem ~5% chance de incidente"""
    with estado.lock:
        if estado.contador % 60 == 0 and not estado.incidente_ativo:
            if random.random() < 0.05:
                tipo = random.choice(["outage", "slowdown"])
                canal_afetado = random.choice(list(CANAIS.keys()))
                duracao = random.randint(120, 360)
                estado.incidente_ativo = {
                    "canal": canal_afetado,
                    "tipo": tipo,
                    "fim": time.time() + duracao
                }
                print(f"\n🔥 INCIDENTE: {tipo} no canal {canal_afetado} por {duracao}s")
        if estado.incidente_ativo and time.time() > estado.incidente_ativo["fim"]:
            print(f"\n✅ Incidente resolvido em {estado.incidente_ativo['canal']}")
            estado.incidente_ativo = None

        if estado.contador % 90 == 0:
            if random.random() < 0.10:
                estado.ataque_fraude = not estado.ataque_fraude
                print(f"\n🛡️  Onda de fraude: {'INICIOU' if estado.ataque_fraude else 'PAROU'}")
            if random.random() < 0.10:
                estado.ataque_login = not estado.ataque_login
                print(f"\n🚨 Ataque de credenciais: {'INICIOU' if estado.ataque_login else 'PAROU'}")

# ---------- GERADORES DE EVENTOS ----------

def evento_transacao():
    perfil_nome, perfil = escolher_perfil()
    canal = escolher_canal(perfil)
    lat_min, lat_max = LATENCIA_MS[canal]
    val_min, val_max = VALORES_EUR[canal]

    taxa_erro = TAXA_ERRO_BASE[canal]
    latencia_mult = 1.0

    # Aplica efeito de incidente
    if estado.incidente_ativo and estado.incidente_ativo["canal"] == canal:
        if estado.incidente_ativo["tipo"] == "outage":
            taxa_erro = 0.60   # 60% de erros durante outage
        elif estado.incidente_ativo["tipo"] == "slowdown":
            latencia_mult = 4.0

    is_error = random.random() < taxa_erro
    latencia = int(random.uniform(lat_min, lat_max) * latencia_mult)
    if is_error:
        latencia = int(latencia * random.uniform(1.5, 3.0))

    valor = round(random.uniform(val_min, val_max) * perfil["valor_mult"], 2)
    servico = random.choice(SERVICOS_POR_CANAL[canal])

    # JSON FLAT — sem nesting "data."
    evt = {
        "event.provider":   "banco.core",
        "event.type":       f"transaction.{canal}",
        "timestamp":        agora_iso(),
        "transaction_id":   str(uuid.uuid4()),
        "canal":            canal,
        "servico":          servico,
        "status":           "error" if is_error else "success",
        "valor_eur":        valor,
        "latencia_ms":      latencia,
        "iban_origem":      iban_pt(),
        "iban_destino":     iban_pt(),
        "perfil_cliente":   perfil_nome,
        "moeda":            "EUR",
    }
    if canal == "mbway":
        evt["telefone_destino"] = telefone_pt()
    if canal == "multibanco":
        evt["entidade"] = f"{random.randint(10000, 99999)}"
        evt["referencia"] = f"{random.randint(100000000, 999999999)}"
    if is_error:
        evt["error_code"] = random.choice(list(ERROR_CODES.keys()))
        evt["error_message"] = ERROR_CODES[evt["error_code"]]
    return evt


def evento_fraude():
    canal = random.choices(
        ["mbway", "transferencia_sepa", "cartao_credito", "cartao_debito", "app_mobile"],
        weights=[35, 15, 25, 15, 10])[0]
    prob_blocked = 0.90 if estado.ataque_fraude else 0.75
    blocked = random.random() < prob_blocked
    valor_tentado = round(random.uniform(500, 50000), 2)
    return {
        "event.provider":    "banco.fraude",
        "event.type":        "fraud.check",
        "timestamp":         agora_iso(),
        "fraud_id":          str(uuid.uuid4()),
        "canal":             canal,
        "status":            "blocked" if blocked else "allowed",
        "score":             random.randint(70, 100) if blocked else random.randint(0, 40),
        "valor_tentado_eur": valor_tentado,
        "regra":             random.choice(REGRAS_FRAUDE),
        "iban_origem":       iban_pt(),
        "moeda":             "EUR",
    }


def evento_auth():
    prob_failed = 0.92 if estado.ataque_login else 0.15
    failed = random.random() < prob_failed
    pais = "PT"
    if failed:
        pais = random.choices(["PT", "ES", "BR", "GB", "RU", "CN", "NG"],
                              weights=[40, 15, 10, 10, 10, 10, 5])[0]
    return {
        "event.provider":  "banco.auth",
        "event.type":      "login.attempt",
        "timestamp":       agora_iso(),
        "session_id":      str(uuid.uuid4()),
        "status":          "failed" if failed else "success",
        "utilizador":      f"user{random.randint(1, 5000)}",
        "ip_origem":       f"{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}",
        "metodo":          random.choice(["pin", "biometria", "chave_movel_digital", "token_sms"]),
        "pais_origem":     pais,
        "motivo":          "credencial_invalida" if failed else "ok",
    }

# ---------- ENVIO EM LOTES ----------

def enviar_lote(eventos):
    if not eventos:
        return 0
    try:
        # BizEvents aceita array de eventos JSON
        r = requests.post(ENDPOINT, headers=HEADERS, json=eventos, timeout=15)
        if r.status_code not in (200, 201, 202, 204):
            print(f"\n❌ HTTP {r.status_code}: {r.text[:300]}")
            return 0
        return len(eventos)
    except Exception as e:
        print(f"\n❌ Rede: {e}")
        return 0

# ---------- LOOP PRINCIPAL ----------

def main():
    if not DT_TENANT or not DT_TOKEN or "XXXXXX" in DT_TOKEN or "COLA_O_TEU" in DT_TOKEN:
        print("⚠️  Configura o .env primeiro!")
        print(f"    Edita o ficheiro: {Path(__file__).resolve().parent / '.env'}")
        print("    Define DT_TENANT e DT_TOKEN")
        sys.exit(1)

    if not DT_TOKEN.startswith("dt0c01."):
        print(f"⚠️  DT_TOKEN parece inválido: deve começar com 'dt0c01.'")
        sys.exit(1)

    print(f"🇵🇹 Simulador banco PT (turbo) → {DT_TENANT}")
    print(f"📊 Base: {BASE_RATE_PER_SECOND} eventos/s · lotes de {BATCH_SIZE}")
    print(f"💡 Variação por hora do dia + incidentes aleatórios\n")

    total, erros = 0, 0
    ultimo_print = time.time()
    eventos_segundo = 0

    while True:
        estado.contador += 1
        maybe_disparar_incidente()

        # Taxa real desta iteração
        rate = int(BASE_RATE_PER_SECOND * fator_hora_dia())
        if estado.ataque_login: rate = int(rate * 1.3)
        if estado.ataque_fraude: rate = int(rate * 1.1)

        # Gera todos os eventos do segundo
        lote = []
        for _ in range(rate):
            r = random.random()
            if r < 0.78:
                lote.append(evento_transacao())
            elif r < 0.93:
                lote.append(evento_auth())
            else:
                lote.append(evento_fraude())

        # Envia em chunks
        for i in range(0, len(lote), BATCH_SIZE):
            chunk = lote[i:i+BATCH_SIZE]
            enviados = enviar_lote(chunk)
            total += enviados
            erros += (len(chunk) - enviados)
            eventos_segundo += enviados

        # Status a cada segundo
        if time.time() - ultimo_print >= 1.0:
            flags = ""
            if estado.incidente_ativo: flags += f" 🔥{estado.incidente_ativo['canal']}"
            if estado.ataque_fraude:   flags += " 🛡️FRAUD"
            if estado.ataque_login:    flags += " 🚨LOGIN"
            print(f"\r📤 Total: {total:>8}  ❌ {erros:>4}  ⚡ {eventos_segundo}/s{flags}        ",
                  end="", flush=True)
            eventos_segundo = 0
            ultimo_print = time.time()
        time.sleep(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Encerrado.")