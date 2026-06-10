"""
Servidor Flask da app bancária — pronto para Docker.
- Em Docker: lê env vars do container (docker-compose passa do .env do host)
- Fora de Docker: lê do .env da pasta pai (modo dev local)
"""

import os
import sys
from pathlib import Path

# Tenta carregar .env da pasta pai (modo dev local)
# Se já houver env vars do Docker, usa essas
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass

from flask import Flask, request, send_from_directory, jsonify
import requests

DT_TENANT = (os.getenv("DT_TENANT") or "").rstrip("/")
DT_TOKEN  = os.getenv("DT_TOKEN") or ""
PORT      = int(os.getenv("FLASK_PORT", "5000"))

if not DT_TENANT or "XXXXXX" in DT_TOKEN or "COLA_O_TEU" in DT_TOKEN:
    print("⚠️  DT_TENANT ou DT_TOKEN não configurados!")
    print(f"    DT_TENANT: {DT_TENANT or '(vazio)'}")
    print(f"    DT_TOKEN: {'(definido)' if DT_TOKEN and 'XXXX' not in DT_TOKEN else '(em falta)'}")
    sys.exit(1)

app = Flask(__name__, static_folder=".", static_url_path="")

@app.route("/")
def index():
    return send_from_directory(".", "index.html")

@app.route("/api/ingest", methods=["POST"])
def ingest():
    """Recebe evento(s) da UI e repassa pro Dynatrace"""
    eventos = request.get_json(force=True)
    if not isinstance(eventos, list):
        eventos = [eventos]

    url = f"{DT_TENANT}/api/v2/bizevents/ingest"
    headers = {
        "Authorization": f"Api-Token {DT_TOKEN}",
        "Content-Type": "application/json"
    }
    try:
        r = requests.post(url, headers=headers, json=eventos, timeout=10)
        return jsonify({"status": r.status_code, "sent": len(eventos)}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/healthz")
def health():
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    print(f"\n🏦 Banco Atlântico Demo → http://localhost:{PORT}")
    print(f"📡 Eventos vão para: {DT_TENANT}\n")
    app.run(host="0.0.0.0", port=PORT, debug=False)