# Operations Dashboard — Golden Signals + APM
## Dynatrace Notebooks/Dashboards — DQL Queries

> Estes tiles usam `fetch spans` e `fetch metrics` — dados gerados via OpenTelemetry.
> Os serviços aparecem no Service Map: banco-api-gateway → banco-transaction-service → banco-fraud-service

---

## ── GOLDEN SIGNAL 1: LATÊNCIA ────────────────────────────────────────────────

### TILE 1 — Latência P50/P95/P99 por Serviço (Line chart)
**Timeframe:** last 30m | Distingue se há degradação por serviço específico

```dql
fetch spans
| filter service.name startsWith "banco-"
| summarize
    p50_ms = percentile(duration, 50) / 1000000,
    p95_ms = percentile(duration, 95) / 1000000,
    p99_ms = percentile(duration, 99) / 1000000,
    by: bin(timestamp, 1m), service.name
| fields timestamp, service.name, p50_ms, p95_ms, p99_ms
```

### TILE 2 — Top 20 Traces Mais Lentos
**Tipo:** Table | **Timeframe:** last 1h

```dql
fetch spans
| filter service.name == "banco-api-gateway"
| filter isRootSpan == true
| sort duration desc
| limit 20
| fields timestamp, trace.id, span.name, duration / 1000000 as latencia_ms, http.url, status.code
```

---

## ── GOLDEN SIGNAL 2: ERROS ───────────────────────────────────────────────────

### TILE 3 — Taxa de Erros por Serviço (%)
**Tipo:** Line chart | **Timeframe:** last 30m | Boa referência de SLO: < 1%

```dql
fetch spans
| filter service.name startsWith "banco-"
| summarize
    erros = countIf(status.code == "ERROR" or otel.status_code == "ERROR"),
    total = count(),
    by: bin(timestamp, 1m), service.name
| fieldsAdd taxa_erro_pct = round(erros * 100.0 / total, 2)
| fields timestamp, service.name, taxa_erro_pct, erros, total
```

### TILE 4 — Distribuição de Erros por Operação
**Tipo:** Table | **Timeframe:** last 1h

```dql
fetch spans
| filter service.name startsWith "banco-"
| filter status.code == "ERROR" or otel.status_code == "ERROR"
| summarize count = count(), by: service.name, span.name
| sort count desc
| limit 20
```

---

## ── GOLDEN SIGNAL 3: THROUGHPUT ─────────────────────────────────────────────

### TILE 5 — Requisições por Segundo por Serviço
**Tipo:** Line chart | **Timeframe:** last 30m

```dql
fetch spans
| filter service.name startsWith "banco-"
| filter isRootSpan == true
| summarize req_por_min = count(), by: bin(timestamp, 1m), service.name
| fieldsAdd req_por_seg = round(req_por_min / 60.0, 1)
| fields timestamp, service.name, req_por_seg
```

### TILE 6 — Throughput por Canal de Transação (métricas OTel)
**Tipo:** Line chart | **Timeframe:** last 30m

```dql
fetch metrics
| metricSelector: transaction.count:splitBy("canal"):sum:auto
| fields timestamp, canal, count
```

---

## ── GOLDEN SIGNAL 4: SATURATION ─────────────────────────────────────────────

### TILE 7 — CPU dos Containers (%)
**Tipo:** Line chart | **Timeframe:** last 30m

```dql
fetch metrics
| metricSelector: builtin:containers.cpu.usagePercent:splitBy("dt.entity.container_group_instance"):avg:auto
| filter dt.entity.container_group_instance contains "banco-"
```

### TILE 8 — Memória dos Containers
**Tipo:** Line chart | **Timeframe:** last 30m

```dql
fetch metrics
| metricSelector: builtin:containers.memory.usagePercent:splitBy("dt.entity.container_group_instance"):avg:auto
| filter dt.entity.container_group_instance contains "banco-"
```

---

## ── DISTRIBUTED TRACING ──────────────────────────────────────────────────────

### TILE 9 — Service Flow (Mapa de Dependências)
**Tipo:** Table → use o Service Map visual no Dynatrace para visualização gráfica

```dql
fetch spans
| filter service.name startsWith "banco-"
| filter isNotNull(peer.service)
| summarize
    chamadas        = count(),
    latencia_avg_ms = avg(duration) / 1000000,
    erros           = countIf(status.code == "ERROR"),
    by: service.name, peer.service
| sort chamadas desc
| fields service.name, peer.service, chamadas, latencia_avg_ms, erros
```

### TILE 10 — Análise de Canal por Latência (OTel span attributes)
**Tipo:** Table | **Timeframe:** last 1h

```dql
fetch spans
| filter service.name == "banco-transaction-service"
| filter isNotNull(transaction.canal)
| summarize
    count   = count(),
    p95_ms  = percentile(duration, 95) / 1000000,
    avg_ms  = avg(duration) / 1000000,
    erros   = countIf(status.code == "ERROR"),
    by: transaction.canal
| fieldsAdd error_rate = round(erros * 100.0 / count, 1)
| sort p95_ms desc
| fields transaction.canal, count, avg_ms, p95_ms, error_rate
```

### TILE 11 — Scores de Fraude Elevados Correlacionados com Lentidão
**Tipo:** Scatter (use Notebooks) | **Timeframe:** last 1h

```dql
fetch spans
| filter service.name == "banco-fraud-service"
| filter isNotNull(fraud.score)
| fields timestamp, trace.id, fraud.score, duration / 1000000 as latencia_ms, fraud.status
| sort fraud.score desc
| limit 100
```

---

## ── SLO / ALERTAS RECOMENDADOS ───────────────────────────────────────────────

```
SLO 1 — Disponibilidade Gateway
  Target: 99.5%
  Métrica: (1 - error_rate) onde service.name == "banco-api-gateway"

SLO 2 — Latência P95 de Transações
  Target: < 2000ms
  Métrica: percentile(duration, 95) onde service.name == "banco-transaction-service"

SLO 3 — Taxa de Sucesso de Transações
  Target: > 97%
  Fonte: BizEvents → taxa_sucesso_pct

Alertas sugeridos:
  • Error rate > 5% por 2 minutos → Critical
  • P99 latency > 3s por 1 minuto → Warning
  • Fraud blocked rate > 20% → Warning (possível ataque)
  • Login failures > 50/min de um único país → Critical (brute force)
```
