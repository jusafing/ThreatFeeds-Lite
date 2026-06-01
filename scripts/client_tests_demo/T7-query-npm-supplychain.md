# T7 — Natural-language query: npm supply-chain compromise

Natural-language query (`scripts/api_client.py query`) translated server-side by
the LLM into a constrained, whitelisted filter and run against the local DB. The
server host is redacted to `<test-server>`; substitute your own `--url`. This
example targets the **supply-chain** scenario a VM/SCA analyst cares about:
malicious or compromised packages in the npm registry.

> Requires an LLM provider configured on the server (the same one used by
> smart-mode normalization). Without one the endpoint returns `503`.

## Command

```bash
scripts/api_client.py --url http://<test-server>:8001 --username admin \
  query "supply-chain compromise or malicious packages in the npm registry" \
  --type raw --max 20
```

## Response (shape)

```json
{
  "dataset": "raw",
  "count": <n>,
  "interpreted_filter": {
    "dataset": "raw",
    "source": null,
    "search": "npm",
    "limit": 20,
    "column_filters": {}
  },
  "results": [
    { "indicator": "...", "title": "...", "description": "...", "tags": "..." }
  ]
}
```

`interpreted_filter` shows exactly how the question was translated, so the
analyst can verify the LLM's interpretation. The recorded server response from a
live run is captured in `developer_notes/` (not committed).
