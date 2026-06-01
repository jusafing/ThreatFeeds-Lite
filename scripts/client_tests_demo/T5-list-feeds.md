# T5 — List the feeds available in the API

Listed the feeds available on the test server `http://<test-server>:8001` with their
per-source entry counts. The API has no dedicated "feeds" endpoint; the closest
is the per-source summary (`/api/viewer/summary`), now surfaced by the client's
new `list-feeds` command (added for this test plan). The `__total__` row is the
grand total across all feeds.

## Command executed

```bash
scripts/api_client.py --url http://<test-server>:8001 list-feeds
```

## API response (`GET /api/viewer/summary`)

```json
[
    {
        "source": "Received Feed 1780271644",
        "count": 0
    },
    {
        "source": "Received Feed 1780271684",
        "count": 2
    },
    {
        "source": "Received Feed 1780271842",
        "count": 10
    },
    {
        "source": "bleeping_computer",
        "count": 15
    },
    {
        "source": "cert_cc_vuln_notes",
        "count": 15
    },
    {
        "source": "cert_eu",
        "count": 10
    },
    {
        "source": "cis_msisac",
        "count": 50
    },
    {
        "source": "cisa_advisories",
        "count": 30
    },
    {
        "source": "dfir_report",
        "count": 10
    },
    {
        "source": "jpcert",
        "count": 20
    },
    {
        "source": "ncsc_uk",
        "count": 20
    },
    {
        "source": "security_affairs",
        "count": 10
    },
    {
        "source": "the_hacker_news",
        "count": 50
    },
    {
        "source": "welivesecurity",
        "count": 100
    },
    {
        "source": "__total__",
        "count": 342
    }
]
```

Includes the feed pushed in T1, **`Received Feed 1780271842`** with `count: 10`. ✅
