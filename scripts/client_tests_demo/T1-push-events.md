# T1 — Push 10 random simulated events

Pushed 10 randomly generated simulated threat events (2 deterministically
seeded with *npm vulnerable package* content for T6) to the listener endpoint
of the test server `http://<test-server>:8001`. Events are produced by
`gen_events.py` and piped into the API client's `send` command. The server
indexes the payload as a new `Received Feed <epoch>` feed — here
**`Received Feed 1780271842`**.

Auth is disabled on the test server, so no credentials are passed.

## Command executed

```bash
python scripts/client_tests_demo/gen_events.py \
  | scripts/api_client.py --url http://<test-server>:8001 send
```

## API response (`POST /api/ingest/listener`)

```json
{
    "inserted": 10,
    "skipped": 0,
    "errors": [],
    "total_read": 10,
    "duplicates": 0,
    "discarded": 0
}
```

Result: **10 inserted, 0 skipped, 0 discarded** — all events accepted.
