#!/usr/bin/env python3
"""
Random simulated threat-event generator for the API client test plan (T1).

Emits a JSON array of randomized threat-feed events on stdout, shaped to the
raw schema's recognized core fields (indicator, indicator_type, threat_type,
severity, confidence, source_url, title, description, tags, actor, campaign).
The payload is meant to be piped straight into the API client's `send`:

    python scripts/client_tests_demo/gen_events.py \
      | scripts/api_client.py --url http://HOST:8001 send

Six events are deterministically seeded so the search tests return meaningful
hits: two carry "npm vulnerable package" content (T6), two carry "CVE-2026"
content (T7), and two carry "supply chain attack" content (T8). A fixed --seed
makes the run reproducible for the recorded markdown artifacts; pass --seed 0
(or any value) to vary the random filler events.

Dependency-free: Python standard library only.
"""
from __future__ import annotations

import argparse
import json
import random
import sys

_SEVERITIES = ["low", "medium", "high", "critical"]
_INDICATOR_TYPES = ["ipv4-addr", "domain-name", "url", "file-hash-sha256"]
_THREAT_TYPES = ["malware", "phishing", "c2", "exploit", "ransomware"]
_ACTORS = ["APT-Quokka", "Spider-Wasp", "Velvet-Mole", "Iron-Heron", "Static-Lynx"]
_CAMPAIGNS = ["SilentQuill", "RustyGate", "PaleHarbor", "GlassFerry", "DimEcho"]
_TAG_POOL = [
    "credential-theft", "supply-chain", "loader", "infostealer",
    "backdoor", "downloader", "spearphishing", "lateral-movement",
]

# Deterministic npm-vulnerable-package events seeded for the T6 search test.
_NPM_SEED_EVENTS = [
    {
        "indicator": "pkg:npm/event-stream@3.3.6",
        "indicator_type": "url",
        "threat_type": "supply-chain",
        "severity": "high",
        "confidence": 90,
        "source_url": "https://example.test/advisories/npm-event-stream",
        "title": "Malicious npm vulnerable package: event-stream supply-chain compromise",
        "description": (
            "The npm package event-stream@3.3.6 shipped a malicious transitive "
            "dependency (flatmap-stream) that exfiltrated wallet credentials. "
            "Flagged as a vulnerable npm package in the software supply chain."
        ),
        "tags": ["supply-chain", "npm", "vulnerable-package", "infostealer"],
        "actor": "Static-Lynx",
        "campaign": "GlassFerry",
    },
    {
        "indicator": "pkg:npm/ua-parser-js@0.7.29",
        "indicator_type": "url",
        "threat_type": "supply-chain",
        "severity": "critical",
        "confidence": 95,
        "source_url": "https://example.test/advisories/npm-ua-parser-js",
        "title": "Compromised npm vulnerable package ua-parser-js drops cryptominer",
        "description": (
            "A hijacked release of the popular npm package ua-parser-js installed "
            "a password stealer and a cryptominer. Listed among known vulnerable "
            "npm packages affecting downstream products."
        ),
        "tags": ["supply-chain", "npm", "vulnerable-package", "cryptominer"],
        "actor": "Iron-Heron",
        "campaign": "RustyGate",
    },
]

# Deterministic CVE-2026 events seeded for the T7 search test. The literal
# string "CVE-2026" appears in the title/description so a ?search=CVE-2026
# full-text query matches.
_CVE_2026_SEED_EVENTS = [
    {
        "indicator": "203.0.113.77",
        "indicator_type": "ipv4-addr",
        "threat_type": "exploit",
        "severity": "critical",
        "confidence": 92,
        "source_url": "https://example.test/advisories/cve-2026-10101",
        "title": "Active exploitation of CVE-2026-10101 in edge VPN appliances",
        "description": (
            "CVE-2026-10101 is a pre-auth remote code execution flaw affecting "
            "edge VPN products. The 2026 advisory reports in-the-wild exploitation "
            "by actors deploying web shells from this host."
        ),
        "tags": ["exploit", "rce", "cve-2026"],
        "actor": "Velvet-Mole",
        "campaign": "PaleHarbor",
    },
    {
        "indicator": "cve-2026-20255.example.test",
        "indicator_type": "domain-name",
        "threat_type": "exploit",
        "severity": "high",
        "confidence": 88,
        "source_url": "https://example.test/advisories/cve-2026-20255",
        "title": "CVE-2026-20255 deserialization bug weaponized in mass scans",
        "description": (
            "A CVE-2026-20255 insecure-deserialization vulnerability disclosed in "
            "2026 is being probed at scale. Affected products should patch to the "
            "fixed 2026 release immediately."
        ),
        "tags": ["exploit", "deserialization", "cve-2026"],
        "actor": "Spider-Wasp",
        "campaign": "DimEcho",
    },
]

# Deterministic supply-chain-attack events seeded for the T8 search test. The
# literal phrase "supply chain attack" (space form) appears so a
# ?search=supply chain full-text query matches.
_SUPPLY_CHAIN_SEED_EVENTS = [
    {
        "indicator": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8091a2b3c4d5e6f7081920a1b2c3d4",
        "indicator_type": "file-hash-sha256",
        "threat_type": "supply-chain",
        "severity": "critical",
        "confidence": 94,
        "source_url": "https://example.test/advisories/build-pipeline-supply-chain-attack",
        "title": "CI/CD supply chain attack trojanizes signed build artifacts",
        "description": (
            "A supply chain attack compromised a vendor's CI/CD pipeline and "
            "injected a backdoor into signed installers. This software supply "
            "chain attack affected thousands of downstream products."
        ),
        "tags": ["supply-chain", "backdoor", "ci-cd"],
        "actor": "Static-Lynx",
        "campaign": "SilentQuill",
    },
    {
        "indicator": "https://updates.bad-vendor.example.test/patch",
        "indicator_type": "url",
        "threat_type": "supply-chain",
        "severity": "high",
        "confidence": 90,
        "source_url": "https://example.test/advisories/update-server-supply-chain-attack",
        "title": "Update-server supply chain attack pushes malicious patches",
        "description": (
            "Threat actors hijacked a software update server in a supply chain "
            "attack, distributing malware-laced updates. Classic supply chain "
            "attack pattern targeting the trusted distribution channel."
        ),
        "tags": ["supply-chain", "loader", "update-hijack"],
        "actor": "Iron-Heron",
        "campaign": "GlassFerry",
    },
]

# Ordered seed set: npm (T6), CVE-2026 (T7), supply-chain (T8). Placed before
# the random fillers so a count >= 6 always includes every search theme.
_SEED_EVENTS = (
    _NPM_SEED_EVENTS + _CVE_2026_SEED_EVENTS + _SUPPLY_CHAIN_SEED_EVENTS
)


def _rand_indicator(rng: random.Random, itype: str) -> str:
    if itype == "ipv4-addr":
        return ".".join(str(rng.randint(1, 254)) for _ in range(4))
    if itype == "domain-name":
        return f"{rng.choice(['mail', 'cdn', 'login', 'api'])}-{rng.randint(100, 999)}.example.test"
    if itype == "url":
        return f"https://bad-{rng.randint(1000, 9999)}.example.test/payload"
    return "".join(rng.choice("0123456789abcdef") for _ in range(64))


def _rand_event(rng: random.Random, idx: int) -> dict:
    itype = rng.choice(_INDICATOR_TYPES)
    ttype = rng.choice(_THREAT_TYPES)
    return {
        "indicator": _rand_indicator(rng, itype),
        "indicator_type": itype,
        "threat_type": ttype,
        "severity": rng.choice(_SEVERITIES),
        "confidence": rng.randint(40, 99),
        "source_url": f"https://feed.example.test/{ttype}/{rng.randint(10000, 99999)}",
        "title": f"Simulated {ttype} indicator #{idx}",
        "description": (
            f"Auto-generated simulated event #{idx} describing a {ttype} "
            f"observation of type {itype} for API client ingestion testing."
        ),
        "tags": rng.sample(_TAG_POOL, k=rng.randint(1, 3)),
        "actor": rng.choice(_ACTORS),
        "campaign": rng.choice(_CAMPAIGNS),
    }


def generate(count: int, seed: int, list_tags: bool = False) -> list[dict]:
    """
    Return ``count`` events: the themed seed events first, then random fillers.

    The first six events are deterministic search seeds (2 npm, 2 CVE-2026, 2
    supply-chain) so the T6/T7/T8 searches return hits; the remainder are random
    fillers. By default ``tags`` is emitted as a comma-separated string, matching
    how feed parsers store tags (e.g. rss_pull joins them) and the dominant shape
    of real data. Pass ``list_tags=True`` to emit ``tags`` as a JSON list instead
    — this reproduces the T2 finding where a list-valued field is silently
    discarded by a server build that does not coerce non-scalar values.
    """
    rng = random.Random(seed)
    events = [dict(e) for e in _SEED_EVENTS[:count]]
    for i in range(len(events), count):
        events.append(_rand_event(rng, i + 1))
    if not list_tags:
        for e in events:
            if isinstance(e.get("tags"), list):
                e["tags"] = ", ".join(e["tags"])
    return events


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1] if __doc__ else None)
    parser.add_argument("--count", type=int, default=10, help="number of events (default: 10)")
    parser.add_argument("--seed", type=int, default=57, help="RNG seed (default: 57)")
    parser.add_argument(
        "--list-tags", action="store_true",
        help="emit tags as a JSON list (reproduces the T2 silent-discard finding)",
    )
    args = parser.parse_args(argv)
    json.dump(generate(args.count, args.seed, args.list_tags), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
