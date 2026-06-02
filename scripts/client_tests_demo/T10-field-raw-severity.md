# T10 — Field search from raw: `severity=critical`

Deterministic exact-column filter (no LLM required) added in issue_local_02. The
repeatable `--field NAME=VALUE` flag maps to `?field=NAME=VALUE` on
`GET /api/viewer/entries` and filters the raw store on an exact column match. The
server host is redacted to `<test-server>`; substitute your own `--url`.

Filter columns are validated server-side against the raw schema
(`FILTERABLE_COLUMNS`); unknown or non-whitelisted names are silently dropped, so
the flag cannot be used to inject arbitrary SQL.

## Command

```bash
scripts/api_client.py --url http://<test-server>:8001 \
  get-raw --field severity=critical --max 25
```

## Response (shape)

`GET /api/viewer/entries?field=severity=critical&limit=25` returns a JSON array
of raw rows whose `severity` column equals `critical` exactly:

```json
[
  {
    "id": 42,
    "indicator": "...",
    "indicator_type": "...",
    "threat_type": "...",
    "severity": "critical",
    "source": "...",
    "title": "...",
    "tags": "..."
  }
]
```

Multiple `--field` flags AND together (e.g.
`--field severity=critical --field indicator_type=url`). The recorded server
response from a live run is captured in `developer_notes/` (not committed).
