# Business Dashboard — Banco Atlântico
## Dynatrace Notebooks/Dashboards — DQL Queries

> Crie um novo Dashboard no Dynatrace e adicione os tiles abaixo.
> Cada query usa `fetch bizevents` — os eventos gerados pelos serviços aparecem aqui.

---

### TILE 1 — Volume Financeiro em Tempo Real (1h)
**Tipo:** Line chart | **Timeframe:** last 1h

```dql
fetch bizevents
| filter event.type startsWith "transaction." and status == "success"
| summarize
    total_transacoes = count(),
    volume_eur       = sum(toDouble(valor_eur)),
    by: bin(timestamp, 1m)
| fields timestamp, total_transacoes, volume_eur
```

---

### TILE 2 — Volume por Canal (Pizza / Bar chart)
**Tipo:** Bar chart | **Timeframe:** last 30m

```dql
fetch bizevents
| filter event.type startsWith "transaction."
| summarize
    count  = count(),
    volume = sum(toDouble(valor_eur)),
    by: canal
| sort volume desc
| fields canal, count, volume
```

---

### TILE 3 — Taxa de Sucesso por Canal (KPI Table)
**Tipo:** Table | **Timeframe:** last 1h

```dql
fetch bizevents
| filter event.type startsWith "transaction."
| summarize
    sucesso = countIf(status == "success"),
    total   = count(),
    volume  = sum(toDouble(valor_eur)),
    by: canal
| fieldsAdd taxa_sucesso_pct = round(sucesso * 100.0 / total, 1)
| sort taxa_sucesso_pct asc
| fields canal, total, sucesso, taxa_sucesso_pct, volume
```

---

### TILE 4 — Detecção de Anomalia — Taxa de Erro ao Longo do Tempo
**Tipo:** Line chart (habilitar Anomaly Detection no tile) | **Timeframe:** last 2h

```dql
fetch bizevents
| filter event.type startsWith "transaction."
| summarize
    erros = countIf(status == "error"),
    total = count(),
    by: bin(timestamp, 5m)
| fieldsAdd taxa_erro_pct = round(erros * 100.0 / total, 2)
| fields timestamp, taxa_erro_pct, erros, total
```

---

### TILE 5 — Top Erros por Código
**Tipo:** Table | **Timeframe:** last 1h

```dql
fetch bizevents
| filter event.type startsWith "transaction." and status == "error"
| summarize count = count(), by: error_code, canal
| sort count desc
| limit 15
| fields error_code, canal, count
```

---

### TILE 6 — Fraudes Bloqueadas vs Permitidas (últimas 24h)
**Tipo:** Bar chart stacked | **Timeframe:** last 24h

```dql
fetch bizevents
| filter event.type == "fraud.check"
| summarize
    bloqueadas = countIf(status == "blocked"),
    permitidas = countIf(status == "allowed"),
    total      = count(),
    by: bin(timestamp, 30m)
| fieldsAdd taxa_bloqueio_pct = round(bloqueadas * 100.0 / total, 1)
| fields timestamp, bloqueadas, permitidas, taxa_bloqueio_pct
```

---

### TILE 7 — Distribuição do Score de Fraude
**Tipo:** Histogram | **Timeframe:** last 1h

```dql
fetch bizevents
| filter event.type == "fraud.check"
| fields score, status, canal, valor_eur
| sort score desc
```

---

### TILE 8 — Tentativas de Login Suspeitas por País (Possível Brute Force)
**Tipo:** Heatmap / Table | **Timeframe:** last 30m | ⚠️ Alertar se > 20 falhas/min

```dql
fetch bizevents
| filter event.type == "login.attempt" and status == "failure"
| summarize falhas = count(), by: bin(timestamp, 1m), pais_origem
| sort falhas desc
| fields timestamp, pais_origem, falhas
```

---

### TILE 9 — Correlação BizEvent ↔ Trace Distribuído
**Tipo:** Table | **Timeframe:** last 1h
> Clicar no trace_id abre o Distributed Trace completo no Dynatrace APM

```dql
fetch bizevents
| filter event.type startsWith "transaction."
| filter isNotNull(trace_id)
| fields timestamp, event.type, canal, valor_eur, status, error_code, trace_id
| sort timestamp desc
| limit 50
```

---

### TILE 10 — KPIs de Negócio (Single Value tiles)
**Tipo:** Single Value | **Timeframe:** last 1h

**Volume Total (€):**
```dql
fetch bizevents
| filter event.type startsWith "transaction." and status == "success"
| summarize volume = sum(toDouble(valor_eur))
```

**Taxa de Sucesso (%):**
```dql
fetch bizevents
| filter event.type startsWith "transaction."
| summarize s = countIf(status == "success"), t = count()
| fieldsAdd taxa = round(s * 100.0 / t, 1)
| fields taxa
```

**Fraudes Bloqueadas:**
```dql
fetch bizevents
| filter event.type == "fraud.check" and status == "blocked"
| summarize count = count()
```
