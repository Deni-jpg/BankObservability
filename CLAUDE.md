# BankObservability — Banco Atlântico

Demo de observabilidade full-stack com Dynatrace. Simula um banco português com
5 microsserviços Python, tráfego sintético, distributed tracing via OpenTelemetry,
RUM no browser e business events.

## Arquitetura

```
Browser (RUM)
    │  window.dtrum → ruxitagentjs → Dynatrace RUM
    │
    └─► nginx:3000 (frontend)
            │  proxy /api/* e /dynaTraceMonitor
            ▼
        api-gateway:8000  (Flask + OTel auto-instrument)
            ├─► auth-service:8001
            ├─► transaction-service:8002 ──► fraud-service:8003
            └── (todos exportam OTLP para…)
                    ▼
            otel-collector:4317/4318
                    │  OTLP/HTTP
                    ▼
            Dynatrace  https://rio76974.live.dynatrace.com
```

## Stack

| Camada         | Tecnologia                                      |
|----------------|-------------------------------------------------|
| Frontend       | HTML/JS puro, nginx 1.27-alpine, envsubst       |
| Backend        | Python 3.12, Flask, opentelemetry-instrument    |
| Tracing        | OpenTelemetry → OTel Collector → Dynatrace OTLP |
| RUM            | Dynatrace ruxitagentjs (agente JS injetado)     |
| Business Events| POST `/api/v2/bizevents/ingest` direto do Python |
| Container      | Docker Compose (sem Kubernetes)                 |

## Configuração (.env)

```env
DT_TENANT=https://rio76974.live.dynatrace.com
DT_TOKEN=dt0c01.O42Q7...          # API token com ingest scope
DT_RUM_SCRIPT_SRC=https://rio76974.live.dynatrace.com/ruxitagentjs_ICA15789NPQRTUVXfqrux_10339260603164134.js
EVENTS_PER_SECOND=15
```

**O nome do ficheiro RUM (`ruxitagentjs_...js`)** obtém-se em:
Settings → Collect and capture → Real User Monitoring → RUM monitoring code filename

## Comandos rápidos

```bash
# Subir tudo
docker compose up -d

# Rebuild só o frontend (após alterar index.html ou .env)
docker compose up -d --build frontend

# Subir com simulador de tráfego automático
docker compose --profile simulator up -d

# Ver logs de um serviço
docker compose logs -f transaction-service

# Parar
docker compose down
```

## Como o RUM funciona

O `frontend/Dockerfile` usa `envsubst` no startup do nginx para substituir
`${DT_RUM_SCRIPT_SRC}` e `${DT_TENANT}` no `index.html` antes de servir o ficheiro.
O script JS do agente Dynatrace é carregado dinamicamente se a variável estiver preenchida.

O JavaScript expõe `window.dtrum` com os helpers encapsulados no objeto `rum`:
- `rum.identify(email)` — associa utilizador à sessão RUM
- `rum.enter(name, type)` / `rum.leave(id)` — marca ações no Session Replay
- `rum.error(name, msg)` — reporta erros custom
- `rum.sessionProp(props)` — envia propriedades da sessão
- `rum.page(name)` — nomeia a "página" numa SPA

Os campos `data-dtrum-input-type="sensitive"` e a classe `.dt-mask` mascaram
dados sensíveis (IBAN, senha) no Session Replay.

## Serviços e portas

| Serviço             | Porta | Rota principal       |
|---------------------|-------|----------------------|
| Frontend (nginx)    | 3000  | http://localhost:3000 |
| API Gateway         | 8000  | /api/, /healthz      |
| Auth Service        | 8001  | /auth/login, /healthz |
| Transaction Service | 8002  | /process, /healthz   |
| Fraud Service       | 8003  | /check, /healthz     |
| OTel Collector      | 4317  | gRPC OTLP            |
| OTel Collector      | 4318  | HTTP OTLP            |

## Padrões importantes

**Não usar OneAgent** — o projeto usa OTLP puro via OTel Collector,
não o agente binário Dynatrace. O RUM é injetado manualmente via script JS.

**envsubst no Dockerfile do frontend** — qualquer nova variável de ambiente
que queiras expor no HTML precisa de ser adicionada ao CMD do Dockerfile:
```
envsubst '${DT_RUM_SCRIPT_SRC} ${DT_TENANT} ${NOVA_VAR}' < ...
```

**Business events** — enviados diretamente do Python via `_send_bizevent()`
em cada serviço. Não passam pelo OTel Collector.

**Erros realistas** — os serviços lançam exceções Python com nome descritivo
(`FraudDetectedException`, `TransactionFailedException`) para que apareçam
com o nome correto na aba Exceptions do Dynatrace.

**Login de demo** — qualquer utilizador/senha funciona; o auth-service tem
25% de taxa de falha simulada (`AUTH_FAILURE_RATE=0.25`).

## Dynatrace — onde ver os dados

| O que ver                    | Onde no Dynatrace                                    |
|------------------------------|------------------------------------------------------|
| Distributed traces           | Applications & Microservices → Distributed Tracing   |
| Sessões RUM / Session Replay | Digital Experience → Web → (aplicação detetada)     |
| Business Events              | Business Analytics                                   |
| Logs estruturados            | Logs                                                 |
| Métricas de serviço          | Services → banco-*                                   |
