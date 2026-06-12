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

| Variável | Descrição |
| --- | --- |
| `DT_TENANT` | URL da tenant (ex: `https://abc12345.live.dynatrace.com`) |
| `DT_TOKEN` | API Token (ver scopes abaixo) |
| `DT_RUM_SCRIPT_SRC` | URL do snippet RUM (opcional) |

### 2. Configurar RUM (Opcional mas Recomendado)

1. No Dynatrace: **Settings → Web & Mobile → Applications → + New Application**
2. Nome: `Banco Atlântico Demo` | Tipo: `Custom application`
3. Em **Instrumentation**, copie o URL do script JavaScript
4. Cole no `.env`: `DT_RUM_SCRIPT_SRC=https://js-cdn.dynatrace.com/jstag/...`

> O OneAgent auto-injectaria RUM se instalado no host. Com Docker local sem OneAgent,
> a injeção manual do snippet é a abordagem correcta e equivalente em funcionalidades
> (Core Web Vitals, AJAX tracking, User sessions, Custom errors).

### 3. Iniciar a Stack

```bash
docker compose up -d

# Verificar saúde
docker compose ps
curl http://localhost:8000/healthz
```

**Portas expostas:**

| Serviço            | URL                         |
|--------------------|-----------------------------|
| Frontend           | http://localhost:3000       |
| API Gateway        | http://localhost:8000       |
| Auth Service       | http://localhost:8001       |
| Transaction Service| http://localhost:8002       |
| Fraud Service      | http://localhost:8003       |
| OTel Collector     | localhost:4317 (gRPC/OTLP)  |

### 4. Iniciar Simulador de Tráfego de Alta Frequência (Opcional)

```bash
docker compose --profile simulator up -d simulator
```

O simulador (`simulator/bank.py`) chama a API real em loop — cada operação gera
um **trace distribuído completo** com 3-4 spans visível no Dynatrace.

### 5. Abrir o Dynatrace

- **Service Map**: Applications & Microservices → Services → banco-api-gateway
- **Distributed Traces**: Applications & Microservices → Distributed Traces
- **BizEvents**: Business Analytics → Business Events
- **RUM**: Digital Experience → Web

---

## Frontend — Funcionalidades

O frontend em `http://localhost:3000` é uma app bancária de demo com:

| Funcionalidade | Descrição |
| --- | --- |
| **Saldo real** | Começa em €12.435,80 — cada transferência manual bem-sucedida debita o valor |
| **Bloqueio por saldo** | Se valor > saldo disponível, transação rejeitada com `INSUFFICIENT_FUNDS` |
| **↺ Recarregar saldo** | Repõe os €12.435,80 sem refrescar a página |
| **Canais de pagamento** | MB WAY, Multibanco, SEPA, Cartão Débito/Crédito |
| **✅ Forçar Sucesso / ❌ Forçar Falha** | Botões no ecrã de login para testar auth sem aleatoriedade |
| **🚨 Simular Fraude** | Gera transação SEPA de €10k-15k com `force_fraud: true` — sempre bloqueada |
| **⚡ Modo Automático** | Gera tráfego contínuo (1-40 op/s) sem afetar o saldo da conta |
| **📋 Histórico** | Últimas 50 transações com trace_id clicável para o Dynatrace |
| **Simulação de erros RUM** | JS Error, DB Timeout (504), HTTP 500 — visíveis em Frontend → Errors |

---

## Arquitectura de Observabilidade

### OpenTelemetry + Dynatrace BizEvents

| Camada         | Tecnologia                 | O que monitora                              |
|----------------|----------------------------|---------------------------------------------|
| Infraestrutura | OneAgent (host)            | CPU, RAM, rede, disco, containers Docker    |
| Código/APM     | OpenTelemetry SDK (Python) | Traces distribuídos, métricas, logs         |
| Pipeline       | OTel Collector             | Recebe OTel → encaminha para Dynatrace OTLP |
| Frontend (UX)  | Dynatrace RUM (JS snippet) | Core Web Vitals, AJAX, erros JS, sessions   |
| Negócio        | BizEvents API REST         | Transações, fraudes, logins, KPIs           |

### Fluxo de um Trace Distribuído

```
POST /api/transaction (Frontend)
  └── [banco-api-gateway]          gateway.transaction.process       ~5ms
        └── [banco-transaction-service]  transaction.process        ~150ms
              ├── [banco-fraud-service]  fraud.evaluate             ~10ms
              │     └── BizEvent: banco.fraude (score, regras, status)
              └── BizEvent: banco.core (canal, valor, latencia, fraud_score)
```

O `trace_id` é incluído nos BizEvents — nos dashboards pode clicar num evento
financeiro e saltar directamente para o trace APM completo.

### BizEvents — Provedores e Campos

#### `banco.auth`
| Campo | Descrição |
| --- | --- |
| `status` | `success` / `failure` |
| `metodo_autenticacao` | `PIN`, `biometrico`, `chave_digital`, `token_sms` |
| `user_id` | Identificador do utilizador |
| `pais_origem` | Código de país ISO (ex: `PT`, `BR`, `DE`) |
| `session_id` | UUID da sessão |
| `trace_id` | ID do trace OTel associado |

#### `banco.core`
| Campo | Descrição |
| --- | --- |
| `status` | `success` / `error` / `blocked` |
| `canal` | `mbway`, `multibanco`, `sepa`, `cartao_debito`, `cartao_credito`, `app_mobile` |
| `valor_eur` | Montante da transação |
| `latencia_ms` | Latência total em ms |
| `fraud_score` | Score de risco retornado pelo Fraud Service |
| `error_code` | `TIMEOUT`, `INSUFFICIENT_FUNDS`, `INVALID_IBAN`, `NETWORK_ERROR`, `SERVICE_UNAVAILABLE`, `FRAUD_DETECTED` |
| `transaction_id` | UUID da transação |
| `trace_id` | ID do trace OTel |

#### `banco.fraude`
| Campo | Descrição |
| --- | --- |
| `status` | `blocked` / `allowed` |
| `score` | Pontuação acumulada (0-100+) |
| `regras_ativas` | Regras disparadas separadas por vírgula, ex: `velocity_check,value_anomaly` ou `none` |
| `canal` | Canal da transação |
| `valor_eur` | Montante verificado |
| `perfil_cliente` | `normal`, `premium`, `enterprise`, `youth`, `senior` |
| `fraud_id` | UUID desta avaliação |
| `transaction_id` | UUID da transação associada |

### Motor de Fraude — Regras e Thresholds

Cada transação acumula pontos por regras probabilísticas. Threshold de bloqueio: **60 pontos** (80 para perfil `enterprise`).

| Regra | Pontos | Probabilidade |
| --- | --- | --- |
| `velocity_check` | +20 | 4% |
| `value_anomaly` | +25 (valor >5k€) / +15 (>2k€) | sempre se aplicar |
| `geolocation_mismatch` | +20 | 5% |
| `device_fingerprint` | +15 | 4% |
| `beneficiary_watchlist` | +35 | 2% |
| `time_pattern` | +10 | 6% |
| `ip_reputation` | +20 | 3% |

O botão **🚨 Simular Fraude** força `score=80` com as regras `velocity_check + value_anomaly + beneficiary_watchlist` — garante sempre um bloqueio.

---

## Dashboards

Ficheiros JSON prontos a importar em **Dynatrace → Dashboards → Import**:

| Ficheiro | Secções |
| --- | --- |
| `dashboards/observabilidade.json` | KPIs negócio 24h, receita por canal/perfil, volume vs perda, últimas transações com erro, análise de fraude |
| `dashboards/tecnico.json` | Golden Signals, saúde por canal, latência percentis, erros por código, drill-down transações, fraud score por canal, auth por método |
| `dashboards/seguranca-resiliencia.json` | Brute force detection, acessos externos por país, latência anómala, erros infra, fraude avançada com combinações de regras |

---

## Scopes do Token Dynatrace

Crie o token em: **Settings → Access Tokens → Generate new token**

| Scope | Para quê |
| --- | --- |
| `bizevents.ingest` | BizEvents directos dos serviços |
| `openTelemetryTrace.ingest` | Traces via OTel Collector |
| `metrics.ingest` | Métricas customizadas via OTel |
| `logs.ingest` | Logs via OTel |
