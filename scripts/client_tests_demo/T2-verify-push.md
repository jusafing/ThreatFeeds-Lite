# T2 — Verify the 10 events were pushed

Read the pushed feed **`Received Feed 1780271842`** back from the test server and confirmed
all **10** events are present (including the 2 npm-seeded events). This file
also records the initial failure encountered on the first push attempt and the
fix applied, per the T2 instruction to *"find reason and potential fix"*.

## Command executed

```bash
scripts/api_client.py --url http://<test-server>:8001 get-raw "Received Feed 1780271842" --max 50
```

## API response (`GET /api/viewer/entries?source=...`) — 10 events

```json
[
    {
        "id": 10,
        "indicator": "https://bad-8491.example.test/payload",
        "indicator_type": "url",
        "threat_type": "phishing",
        "severity": "medium",
        "confidence": 50.0,
        "source": "Received Feed 1780271842",
        "source_url": "https://feed.example.test/phishing/52969",
        "title": "Simulated phishing indicator #10",
        "description": "Auto-generated simulated event #10 describing a phishing observation of type url for API client ingestion testing.",
        "tags": "lateral-movement, downloader",
        "tlp": null,
        "published_at": null,
        "first_seen": null,
        "last_seen": null,
        "ingested_at": "2026-05-31T23:57:23.101952+00:00",
        "cve_id": null,
        "cvss_score": null,
        "cvss_vector": null,
        "affected_product": null,
        "affected_vendor": null,
        "patch_available": null,
        "mitre_attack_id": null,
        "malware_family": null,
        "campaign": "PaleHarbor",
        "actor": "APT-Quokka",
        "country": null,
        "autonomous_system": null,
        "port": null,
        "protocol": null,
        "geo_lat": null,
        "geo_lon": null,
        "ingest_mode": "push",
        "raw": null,
        "normalized": 0,
        "dedup_key": "9e7eb405e167e7986535374f21b442da97da6a1d7421b0ed8832d78d122a2270"
    },
    {
        "id": 9,
        "indicator": "576965fcd9b0cc838ac84e3a4aa9b29a623fd4777d13858d1a2ab376f4fc0c15",
        "indicator_type": "file-hash-sha256",
        "threat_type": "phishing",
        "severity": "medium",
        "confidence": 64.0,
        "source": "Received Feed 1780271842",
        "source_url": "https://feed.example.test/phishing/44943",
        "title": "Simulated phishing indicator #9",
        "description": "Auto-generated simulated event #9 describing a phishing observation of type file-hash-sha256 for API client ingestion testing.",
        "tags": "loader, supply-chain, infostealer",
        "tlp": null,
        "published_at": null,
        "first_seen": null,
        "last_seen": null,
        "ingested_at": "2026-05-31T23:57:23.090485+00:00",
        "cve_id": null,
        "cvss_score": null,
        "cvss_vector": null,
        "affected_product": null,
        "affected_vendor": null,
        "patch_available": null,
        "mitre_attack_id": null,
        "malware_family": null,
        "campaign": "DimEcho",
        "actor": "Spider-Wasp",
        "country": null,
        "autonomous_system": null,
        "port": null,
        "protocol": null,
        "geo_lat": null,
        "geo_lon": null,
        "ingest_mode": "push",
        "raw": null,
        "normalized": 0,
        "dedup_key": "7b11e7446fb5238e2344efe7816e53b0bd794bd065524de6f36d7b51474fea79"
    },
    {
        "id": 8,
        "indicator": "d0fd3d2c02810215e6051b57c001c799f581f6404e539e8ea53b676e414b857b",
        "indicator_type": "file-hash-sha256",
        "threat_type": "phishing",
        "severity": "high",
        "confidence": 76.0,
        "source": "Received Feed 1780271842",
        "source_url": "https://feed.example.test/phishing/93801",
        "title": "Simulated phishing indicator #8",
        "description": "Auto-generated simulated event #8 describing a phishing observation of type file-hash-sha256 for API client ingestion testing.",
        "tags": "backdoor, supply-chain, lateral-movement",
        "tlp": null,
        "published_at": null,
        "first_seen": null,
        "last_seen": null,
        "ingested_at": "2026-05-31T23:57:23.079710+00:00",
        "cve_id": null,
        "cvss_score": null,
        "cvss_vector": null,
        "affected_product": null,
        "affected_vendor": null,
        "patch_available": null,
        "mitre_attack_id": null,
        "malware_family": null,
        "campaign": "PaleHarbor",
        "actor": "Spider-Wasp",
        "country": null,
        "autonomous_system": null,
        "port": null,
        "protocol": null,
        "geo_lat": null,
        "geo_lon": null,
        "ingest_mode": "push",
        "raw": null,
        "normalized": 0,
        "dedup_key": "9f88ca0385f172c62827af4d59464fac9520ea9fcba32ab67c65bd29355882b5"
    },
    {
        "id": 7,
        "indicator": "ae1e0909bbb80f541d8508d741318a64715035e52e5c5dd7c4317400e3086baf",
        "indicator_type": "file-hash-sha256",
        "threat_type": "malware",
        "severity": "critical",
        "confidence": 64.0,
        "source": "Received Feed 1780271842",
        "source_url": "https://feed.example.test/malware/82645",
        "title": "Simulated malware indicator #7",
        "description": "Auto-generated simulated event #7 describing a malware observation of type file-hash-sha256 for API client ingestion testing.",
        "tags": "loader, infostealer",
        "tlp": null,
        "published_at": null,
        "first_seen": null,
        "last_seen": null,
        "ingested_at": "2026-05-31T23:57:23.069251+00:00",
        "cve_id": null,
        "cvss_score": null,
        "cvss_vector": null,
        "affected_product": null,
        "affected_vendor": null,
        "patch_available": null,
        "mitre_attack_id": null,
        "malware_family": null,
        "campaign": "PaleHarbor",
        "actor": "Velvet-Mole",
        "country": null,
        "autonomous_system": null,
        "port": null,
        "protocol": null,
        "geo_lat": null,
        "geo_lon": null,
        "ingest_mode": "push",
        "raw": null,
        "normalized": 0,
        "dedup_key": "cb9294f4f93bf76f0e1f21a8969ff8e977afec9407713194b160e2cd5ec1bc00"
    },
    {
        "id": 6,
        "indicator": "login-504.example.test",
        "indicator_type": "domain-name",
        "threat_type": "phishing",
        "severity": "medium",
        "confidence": 66.0,
        "source": "Received Feed 1780271842",
        "source_url": "https://feed.example.test/phishing/68484",
        "title": "Simulated phishing indicator #6",
        "description": "Auto-generated simulated event #6 describing a phishing observation of type domain-name for API client ingestion testing.",
        "tags": "lateral-movement",
        "tlp": null,
        "published_at": null,
        "first_seen": null,
        "last_seen": null,
        "ingested_at": "2026-05-31T23:57:23.057893+00:00",
        "cve_id": null,
        "cvss_score": null,
        "cvss_vector": null,
        "affected_product": null,
        "affected_vendor": null,
        "patch_available": null,
        "mitre_attack_id": null,
        "malware_family": null,
        "campaign": "SilentQuill",
        "actor": "Iron-Heron",
        "country": null,
        "autonomous_system": null,
        "port": null,
        "protocol": null,
        "geo_lat": null,
        "geo_lon": null,
        "ingest_mode": "push",
        "raw": null,
        "normalized": 0,
        "dedup_key": "8224afdc1129d976dd2f1892aee4fa4946468a09f6b32838660e2e7485dfb8ef"
    },
    {
        "id": 5,
        "indicator": "api-669.example.test",
        "indicator_type": "domain-name",
        "threat_type": "phishing",
        "severity": "medium",
        "confidence": 92.0,
        "source": "Received Feed 1780271842",
        "source_url": "https://feed.example.test/phishing/86845",
        "title": "Simulated phishing indicator #5",
        "description": "Auto-generated simulated event #5 describing a phishing observation of type domain-name for API client ingestion testing.",
        "tags": "loader, backdoor",
        "tlp": null,
        "published_at": null,
        "first_seen": null,
        "last_seen": null,
        "ingested_at": "2026-05-31T23:57:23.047355+00:00",
        "cve_id": null,
        "cvss_score": null,
        "cvss_vector": null,
        "affected_product": null,
        "affected_vendor": null,
        "patch_available": null,
        "mitre_attack_id": null,
        "malware_family": null,
        "campaign": "PaleHarbor",
        "actor": "Iron-Heron",
        "country": null,
        "autonomous_system": null,
        "port": null,
        "protocol": null,
        "geo_lat": null,
        "geo_lon": null,
        "ingest_mode": "push",
        "raw": null,
        "normalized": 0,
        "dedup_key": "181f9521b1523a8acd4cd661559eefd9e50cedc06ffa0d8684b354653a7b422e"
    },
    {
        "id": 4,
        "indicator": "https://bad-7523.example.test/payload",
        "indicator_type": "url",
        "threat_type": "exploit",
        "severity": "low",
        "confidence": 81.0,
        "source": "Received Feed 1780271842",
        "source_url": "https://feed.example.test/exploit/86676",
        "title": "Simulated exploit indicator #4",
        "description": "Auto-generated simulated event #4 describing a exploit observation of type url for API client ingestion testing.",
        "tags": "lateral-movement, backdoor, loader",
        "tlp": null,
        "published_at": null,
        "first_seen": null,
        "last_seen": null,
        "ingested_at": "2026-05-31T23:57:23.036969+00:00",
        "cve_id": null,
        "cvss_score": null,
        "cvss_vector": null,
        "affected_product": null,
        "affected_vendor": null,
        "patch_available": null,
        "mitre_attack_id": null,
        "malware_family": null,
        "campaign": "RustyGate",
        "actor": "Static-Lynx",
        "country": null,
        "autonomous_system": null,
        "port": null,
        "protocol": null,
        "geo_lat": null,
        "geo_lon": null,
        "ingest_mode": "push",
        "raw": null,
        "normalized": 0,
        "dedup_key": "ffc223e582f95be3b08e6c49e00f35bd1a62540319ba5939eb6591640736ac7b"
    },
    {
        "id": 3,
        "indicator": "151.155.5.58",
        "indicator_type": "ipv4-addr",
        "threat_type": "c2",
        "severity": "high",
        "confidence": 70.0,
        "source": "Received Feed 1780271842",
        "source_url": "https://feed.example.test/c2/48043",
        "title": "Simulated c2 indicator #3",
        "description": "Auto-generated simulated event #3 describing a c2 observation of type ipv4-addr for API client ingestion testing.",
        "tags": "spearphishing, credential-theft",
        "tlp": null,
        "published_at": null,
        "first_seen": null,
        "last_seen": null,
        "ingested_at": "2026-05-31T23:57:23.026979+00:00",
        "cve_id": null,
        "cvss_score": null,
        "cvss_vector": null,
        "affected_product": null,
        "affected_vendor": null,
        "patch_available": null,
        "mitre_attack_id": null,
        "malware_family": null,
        "campaign": "DimEcho",
        "actor": "Spider-Wasp",
        "country": null,
        "autonomous_system": null,
        "port": null,
        "protocol": null,
        "geo_lat": null,
        "geo_lon": null,
        "ingest_mode": "push",
        "raw": null,
        "normalized": 0,
        "dedup_key": "89cc37747a4436c5ea1571cb2bf6e5f6ddf4a32c7f14f1e73779bd18f900ae0f"
    },
    {
        "id": 2,
        "indicator": "pkg:npm/ua-parser-js@0.7.29",
        "indicator_type": "url",
        "threat_type": "supply-chain",
        "severity": "critical",
        "confidence": 95.0,
        "source": "Received Feed 1780271842",
        "source_url": "https://example.test/advisories/npm-ua-parser-js",
        "title": "Compromised npm vulnerable package ua-parser-js drops cryptominer",
        "description": "A hijacked release of the popular npm package ua-parser-js installed a password stealer and a cryptominer. Listed among known vulnerable npm packages affecting downstream products.",
        "tags": "supply-chain, npm, vulnerable-package, cryptominer",
        "tlp": null,
        "published_at": null,
        "first_seen": null,
        "last_seen": null,
        "ingested_at": "2026-05-31T23:57:23.015873+00:00",
        "cve_id": null,
        "cvss_score": null,
        "cvss_vector": null,
        "affected_product": null,
        "affected_vendor": null,
        "patch_available": null,
        "mitre_attack_id": null,
        "malware_family": null,
        "campaign": "RustyGate",
        "actor": "Iron-Heron",
        "country": null,
        "autonomous_system": null,
        "port": null,
        "protocol": null,
        "geo_lat": null,
        "geo_lon": null,
        "ingest_mode": "push",
        "raw": null,
        "normalized": 0,
        "dedup_key": "c3691aab8f795d162b9baa1df571346ac568d65cc1e5191742c70437f46e3249"
    },
    {
        "id": 1,
        "indicator": "pkg:npm/event-stream@3.3.6",
        "indicator_type": "url",
        "threat_type": "supply-chain",
        "severity": "high",
        "confidence": 90.0,
        "source": "Received Feed 1780271842",
        "source_url": "https://example.test/advisories/npm-event-stream",
        "title": "Malicious npm vulnerable package: event-stream supply-chain compromise",
        "description": "The npm package event-stream@3.3.6 shipped a malicious transitive dependency (flatmap-stream) that exfiltrated wallet credentials. Flagged as a vulnerable npm package in the software supply chain.",
        "tags": "supply-chain, npm, vulnerable-package, infostealer",
        "tlp": null,
        "published_at": null,
        "first_seen": null,
        "last_seen": null,
        "ingested_at": "2026-05-31T23:57:23.004923+00:00",
        "cve_id": null,
        "cvss_score": null,
        "cvss_vector": null,
        "affected_product": null,
        "affected_vendor": null,
        "patch_available": null,
        "mitre_attack_id": null,
        "malware_family": null,
        "campaign": "GlassFerry",
        "actor": "Static-Lynx",
        "country": null,
        "autonomous_system": null,
        "port": null,
        "protocol": null,
        "geo_lat": null,
        "geo_lon": null,
        "ingest_mode": "push",
        "raw": null,
        "normalized": 0,
        "dedup_key": "50adb454699a23a2faca132d47a269ec32881a34fe9a927522ce8e250cbf0aa9"
    }
]
```

## Verification

`list-feeds` before vs. after the push confirms a new feed with exactly 10 rows:

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

`"Received Feed 1780271842"` shows `count: 10`. ✅

## Issue found on first attempt, and the fix

The **first** push attempt returned `inserted: 0, discarded: 10` with an empty
`errors` list:

```json
{"inserted": 0, "skipped": 10, "errors": [], "total_read": 10, "duplicates": 0, "discarded": 10}
```

**Reason.** The generated events carried `tags` as a JSON **list**
(`["npm", ...]`). `insert_entry` binds core-column values directly to sqlite3,
which cannot bind a `list`/`dict` parameter. The `INSERT` raised, was caught by
the generic exception handler in `backend/db/manager.py`, and the row was
counted as `discarded` — but the user-facing `errors` list stayed empty, so the
failure looked silent. Isolation probes confirmed it:

```text
tags as list   -> {"inserted": 0, "discarded": 1}   # fails
tags as string -> {"inserted": 1, "discarded": 0}   # ok
no tags        -> {"inserted": 1, "discarded": 0}   # ok
```

Feed parsers already stringify tags (e.g. `rss_pull.py` joins them with `", "`),
but the generic push listener did not, so realistic list-valued JSON was lost.

**Fix (two parts):**
1. **Durable code fix** — `insert_entry` now JSON-encodes any non-scalar
   (`list`/`dict`) core-column value at the storage boundary, so pushed payloads
   are never silently discarded. Covered by a new regression test
   (`test_insert_coerces_list_and_dict_core_fields`).
2. **Test-data alignment** — `gen_events.py` emits `tags` as a comma-separated
   string by default (matching the dominant real-data convention), so the live
   run against the already-deployed server succeeds. (`--list-tags` reproduces
   the original failing shape for demonstration.)

After the fix, re-running the push returned `inserted: 10` (above).
