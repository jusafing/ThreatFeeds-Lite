# T11 — Field search from normalized: `indicator_type=ipv4`

Deterministic exact-column filter (no LLM required) added in issue_local_02,
applied to the **normalized** store. The repeatable `--field NAME=VALUE` flag
maps to `?field=NAME=VALUE` on `GET /api/normalizer/entries` and filters
normalized rows on an exact column match. The server host is redacted to
`<test-server>`; substitute your own `--url`.

Filter columns are validated server-side against the normalized schema
(`_allowed_columns()`); unknown or non-whitelisted names are silently dropped, so
the flag cannot be used to inject arbitrary SQL.

## Command

```bash
scripts/api_client.py --url http://<test-server>:8001 \
  get-normalized --field indicator_type=ipv4 --max 25
```

## Response (shape)

`GET /api/normalizer/entries?field=indicator_type=ipv4&limit=25` returns a JSON
array of normalized rows whose `indicator_type` column equals `ipv4` exactly:

```json
[
  {
    "id": 17,
    "source_entry_id": 103,
    "indicator": "...",
    "indicator_type": "ipv4",
    "threat_type": "...",
    "severity": "...",
    "confidence": 0.0,
    "normalized_at": "..."
  }
]
```

Multiple `--field` flags AND together. The recorded server response from a live
run is captured in `developer_notes/` (not committed).
