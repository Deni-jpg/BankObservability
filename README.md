# Banco Atlântico — Full-Stack Observability Lab
## Dynatrace Demo · OpenTelemetry · RUM · Business Events

```
Browser (RUM) ──────────────────────────────────────────────┐
      │                                                      │ User session
      ▼                                                      │ Core Web Vitals
 ┌──────────┐      ┌─────────────┐      ┌────────────────┐  │ Custom Actions
 │ Frontend │─────▶│ API Gateway │─────▶│Transaction Svc │  │
 │  nginx   │      │  :8000      │  ┌──▶│    :8002       │  │
 │  :3000   │      │  OTel+Flask │  │   │  OTel+Flask    │  │
 └──────────┘      └─────────────┘  │   └───────┬────────┘  │
                         │          │           │            │
                         ▼          │           ▼            │
                  ┌────────────┐   │  ┌──────────────────┐  │
                  │  Auth Svc  │   │  │   Fraud Svc      │  │
                  │   :8001    │   │  │     :8003        │  │
                  │ OTel+Flask │   │  │   OTel+Flask     │  │
                  └────────────┘   │  └──────────────────┘  │
                         │         │           │             │
                         └─────────┴───────────┘            │
                                   │                         │
                        ┌──────────▼──────────┐             │
                        │    OTel Collector   │             │
                        │  gRPC :4317         │             │
                        │  HTTP :4318         │             │
                        └──────────┬──────────┘             │
                                   │ OTLP/HTTPS             │
                                   ▼                         │
                        ┌──────────────────────┐            │
                        │      Dynatrace        │◀───────────┘
                        │  Traces · Metrics     │  RUM Agent
                        │  BizEvents · Logs     │
                        └──────────────────────┘
```

---

## Pré-requisitos

- Docker Desktop (com Compose V2)
- Conta Dynatrace (trial em dynatrace.com/trial)

---

## Setup em 5 Passos

### 1. Configurar Credenciais

```bash
cp .env.example .env
```

Edite o `.env` com os dados da sua Tenant:
- `DT_TENANT` → URL da tenant (ex: `https://abc12345.live.dynatrace.com`)
- `DT_TOKEN` → API Token com os scopes:
  - `bizevents.ingest`
  - `openTelemetryTrace.ingest`
  - `metrics.ingest`
  - `logs.ingest`

### 2. Configurar RUM (Opcional mas Recomendado)

1. No Dynatrace: **Settings → Web & Mobile → Applications → + New Application**
2. Nome: `Banco Atlântico Demo` | Tipo: `Custom application`
3. Em **Instrumentation**, copie o URL do script JavaScript
4. Cole no `.env`: `DT_RUM_SCRIPT_SRC=https://js-cdn.dynatrace.com/...`

> **Por que injeção manual?**
> O OneAgent auto-injectaria RUM se instalado no host. Numa demo local com Docker
> sem OneAgent no host, a injeção manual do snippet é a abordagem correcta e
> equivalente em funcionalidades (Core Web Vitals, AJAX tracking, User sessions).

### 3. Iniciar a Stack

```bash
# Stack principal (frontend + 4 serviços + OTel Collector)
docker compose up -d

# Verificar saúde
docker compose ps
curl http://localhost:8000/healthz
```

**Portas expostas:**
| Serviço        | URL                          |
|----------------|------------------------------|
| Frontend       | http://localhost:3000        |
| API Gateway    | http://localhost:8000        |
| OTel Collector | localhost:4317 (gRPC/OTLP)  |

### 4. Iniciar Simulador de Tráfego (Opcional)

```bash
# Gera 15 op/s de tráfego realista com incidentes simulados
docker compose --profile simulator up -d simulator
```

O simulador chama a API real → cada operação cria um **trace distribuído completo**
visível no Dynatrace com 3-4 spans encadeados.

### 5. Abrir o Dynatrace

- **Service Map**: Applications & Microservices → Services → banco-api-gateway
- **Distributed Traces**: Applications & Microservices → Distributed Traces
- **BizEvents**: Business Analytics → Business Events
- **RUM**: Digital Experience → Web

---

## Arquitectura de Observabilidade

### Hybrid OneAgent + OpenTelemetry

| Camada          | Tecnologia                    | O que monitora                              |
|-----------------|-------------------------------|---------------------------------------------|
| Infraestrutura  | OneAgent (host)               | CPU, RAM, rede, disco, containers Docker    |
| Código/APM      | OpenTelemetry SDK (Python)    | Traces distribuídos, métricas, logs         |
| Pipeline        | OTel Collector                | Recebe OTel → encaminha para Dynatrace OTLP |
| Frontend (UX)   | Dynatrace RUM (JS snippet)    | Core Web Vitals, AJAX, erros JS, sessions   |
| Negócio         | BizEvents API + OTel attrs    | Transações, fraudes, logins, KPIs           |

### Fluxo de um Trace Distribuído

```
GET /api/transaction (Frontend)
  └── [banco-api-gateway]  gateway.transaction.process         ~5ms
        └── [banco-transaction-service] transaction.process   ~150ms
              ├── [banco-fraud-service] fraud.evaluate        ~10ms
              └── BizEvent: transaction.mbway (com trace_id)
```

O `trace_id` é incluído no BizEvent → no dashboard de negócio pode clicar
num evento financeiro e saltar directamente para o trace APM completo.

### Business Events — Abordagem Dual

Os serviços usam **duas camadas** de BizEvents:

1. **OTel Span Attributes** (`transaction.*`, `fraud.*`, `auth.*`) →
   Dynatrace extrai automaticamente via Business Analytics OTel integration
2. **Direct API** (`POST /api/v2/bizevents/ingest`) →
   Garantia de entrega mesmo com degradação do Collector

---

## Dashboards

Os ficheiros em `dashboards/` contêm todas as queries DQL prontas a usar:

- [`dashboards/business-dashboard.md`](dashboards/business-dashboard.md) →
  Volume financeiro, taxa de sucesso, fraudes, logins suspeitos
- [`dashboards/operations-dashboard.md`](dashboards/operations-dashboard.md) →
  Golden Signals (Latência, Erros, Throughput, Saturation), SLOs

---

## Scopes do Token Dynatrace

Crie o token em: **Settings → Access Tokens → Generate new token**

| Scope                       | Para quê                              |
|-----------------------------|---------------------------------------|
| `bizevents.ingest`          | BizEvents directos dos serviços       |
| `openTelemetryTrace.ingest` | Traces via OTel Collector             |
| `metrics.ingest`            | Métricas customizadas via OTel        |
| `logs.ingest`               | Logs via OTel (opcional)              |
