# T9 — Natural-language query: high-severity indicators by threat actor

Natural-language query translated server-side into a constrained filter. Targets
a **CTI / threat-hunting** question: high-severity indicators attributed to a
particular threat actor or campaign. The server host is redacted to
`<test-server>`.

> Requires an LLM provider configured on the server. Without one → `503`.

## Command

```bash
scripts/api_client.py --url http://<test-server>:8001 --username admin \
  query "high severity indicators attributed to the Lazarus group" \
  --type normalized --max 25
```

## Response (shape)

```json
{
  "dataset": "normalized",
  "count": <n>,
  "interpreted_filter": {
    "dataset": "normalized",
    "source": null,
    "search": "Lazarus",
    "limit": 25,
    "column_filters": { "severity": "high", "actor": "Lazarus" }
  },
  "results": [
    { "indicator": "...", "actor": "Lazarus", "severity": "high", "title": "..." }
  ]
}
```

The interpreted filter is returned alongside the rows so the analyst can confirm
the actor/severity translation before trusting the result set. The recorded live
response is captured in `developer_notes/` (not committed).
