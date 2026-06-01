# T8 — Natural-language query: critical 2026 CVEs by vendor

Natural-language query translated server-side into a constrained filter. Targets
a **vulnerability-management** question: critical CVEs disclosed in 2026,
grouped/filtered by the affected vendor or product. The server host is redacted
to `<test-server>`.

> Requires an LLM provider configured on the server. Without one → `503`.

## Command

```bash
scripts/api_client.py --url http://<test-server>:8001 --username admin \
  query "critical CVEs from 2026 affecting nginx" --type normalized --max 25
```

## Response (shape)

```json
{
  "dataset": "normalized",
  "count": <n>,
  "interpreted_filter": {
    "dataset": "normalized",
    "source": null,
    "search": "CVE nginx",
    "limit": 25,
    "column_filters": { "severity": "critical" }
  },
  "results": [
    { "indicator": "CVE-2026-...", "severity": "critical", "title": "..." }
  ]
}
```

For the normalized dataset, the LLM may emit `column_filters` keyed by known
normalized columns (e.g. `severity`). These are applied as an in-process
post-filter; a column the row does not have is ignored, not treated as an empty
match. The recorded live response is captured in `developer_notes/`
(not committed).
