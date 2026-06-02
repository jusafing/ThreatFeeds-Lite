"""
Normalizer engine — maps raw ingest entries to the canonical normalized schema.

Auto mode: uses synonym groups to detect which raw field maps to which canonical field.
Manual mode: uses operator-defined source→field mappings from normalizer-config.yaml.

Canonical names are sourced from config/feed-fields.yaml (prompts-021E-pre). The
engine no longer maintains a private namespace divergent from the rest of the
system; raw IP / domain / hash synonyms collapse into ``indicator`` with
``indicator_type`` auto-emitted based on which synonym matched.
"""
from __future__ import annotations

import logging
from fnmatch import fnmatchcase
from functools import lru_cache
from typing import Any

from backend.db.manager import DATA_DIR, mark_normalized, query_entries
from backend.normalizer.config import load_normalizer_config
from backend.normalizer.consolidated import get_active_consolidated
from backend.normalizer.db import insert_normalized
from backend.normalizer.mappings import get_active_version
from backend.normalizer.proposals import get_proposal
from backend.normalizer.run_history import record_run

logger = logging.getLogger(__name__)

# ── Synonym groups ─────────────────────────────────────────────────────────────
# Each group: (canonical_name, set_of_synonyms_including_canonical)
# Canonicals are aligned with config/feed-fields.yaml (021E-pre).
# Priority: first match wins within a group.

_SYNONYM_GROUPS: list[tuple[str, set[str]]] = [
    ("indicator", {
        "indicator",
        # ip
        "ip", "ip_address", "ip_addr", "ipv4", "ipv6",
        "src_ip", "dst_ip", "source_ip", "destination_ip",
        # domain
        "domain", "hostname", "fqdn", "host", "domain_name", "site",
        # hash
        "hash", "md5", "sha1", "sha256", "sha512", "file_hash", "checksum",
        # url
        "url",
    }),
    ("indicator_type", {
        "indicator_type", "ioc_type", "type_of_indicator",
    }),
    ("threat_type", {
        "threat_type", "category", "kind", "type",
    }),
    ("severity", {
        "severity", "risk", "priority", "threat_level", "criticality",
    }),
    ("confidence", {
        "confidence", "confidence_score", "reliability",
    }),
    ("source", {
        "source", "source_name", "feed", "feed_name", "provider",
    }),
    ("source_url", {
        "source_url", "reference", "url_reference", "ref_url",
    }),
    ("title", {
        "title", "name", "headline",
    }),
    ("description", {
        "description", "summary", "desc",
    }),
    ("tags", {
        "tags", "labels", "categories",
    }),
    ("tlp", {
        "tlp", "traffic_light_protocol",
    }),
    ("published_at", {
        "published_at", "published", "publisheddate", "pub_date",
        "release_date", "date", "datetime", "event_time", "time",
        "created_at",
    }),
    ("first_seen", {
        "first_seen", "firstseen",
    }),
    ("last_seen", {
        "last_seen", "lastseen", "updated", "updated_at",
        "last_modified", "modified",
    }),
    ("cve_id", {
        "cve_id", "cve", "vulnerability", "vuln_id", "cve_number",
    }),
    ("cvss_score", {
        "cvss_score", "cvss", "risk_score", "threat_score", "score",
    }),
    ("cvss_vector", {
        "cvss_vector", "vector_string",
    }),
    ("affected_product", {
        "affected_product", "product", "product_name",
    }),
    ("affected_vendor", {
        "affected_vendor", "vendor", "vendor_name",
    }),
    ("patch_available", {
        "patch_available", "has_patch", "fixed",
    }),
    ("mitre_attack_id", {
        "mitre_attack_id", "attack_id", "technique_id",
    }),
    ("malware_family", {
        "malware_family", "malware", "family",
    }),
    ("campaign", {
        "campaign", "campaign_name",
    }),
    ("actor", {
        "actor", "threat_actor", "attacker", "group", "apt", "adversary",
        "attribution",
    }),
    ("country", {
        "country", "country_code", "geo_country", "location", "region",
    }),
    ("port", {
        "port", "dst_port", "src_port", "destination_port", "source_port",
    }),
]


# ── indicator_type emission map ────────────────────────────────────────────────
# When one of these *type-revealing* raw field names matches the `indicator`
# canonical, the engine ALSO emits the indicator_type. Raw `indicator` itself
# is intentionally absent — its type cannot be inferred from the field name.
_INDICATOR_TYPE_FROM_SYNONYM: dict[str, str] = {
    "ip": "ip", "ip_address": "ip", "ip_addr": "ip",
    "ipv4": "ip", "ipv6": "ip",
    "src_ip": "ip", "dst_ip": "ip",
    "source_ip": "ip", "destination_ip": "ip",
    "domain": "domain", "hostname": "domain", "fqdn": "domain",
    "host": "domain", "domain_name": "domain", "site": "domain",
    "md5": "hash_md5",
    "sha1": "hash_sha1",
    "sha256": "hash_sha256",
    "sha512": "hash_sha512",
    "hash": "hash", "file_hash": "hash", "checksum": "hash",
    "url": "url",
}


def _build_reverse_map() -> dict[str, str]:
    """Build {synonym → canonical} lookup."""
    reverse: dict[str, str] = {}
    for canonical, synonyms in _SYNONYM_GROUPS:
        for s in synonyms:
            reverse.setdefault(s.lower(), canonical)
    return reverse


_REVERSE_MAP = _build_reverse_map()


# ── Wildcard synonyms (prompts-021C, rewritten for 021E-pre canonicals) ────────
# fnmatch-style patterns matched against the lower-cased raw field name AFTER
# the exact reverse-map lookup has missed. Precedence is decided by
# `_specificity` (lower = more specific): 0=exact-no-glob, 1=anchored-glob
# (wildcards only at one end), 2=free-glob (wildcards in middle or both ends).
# On equal specificity the original table order wins ⇒ first-canonical-wins.
#
# Each canonical referenced here MUST also exist in _SYNONYM_GROUPS — that
# invariant is enforced by test_normalizer_synonyms.py. Deliberately NO
# wildcards target `severity`: that synonym group is reserved for pure
# severity-level terms; numeric scoring routes to `cvss_score` instead.

_WILDCARD_PATTERNS: list[tuple[str, str]] = [
    # cve_id
    ("*cve*id*", "cve_id"),
    ("vulnerability.id", "cve_id"),
    ("cve.id", "cve_id"),
    ("vuln*", "cve_id"),
    # indicator (IP-flavoured raw fields)
    ("*src*ip*", "indicator"),
    ("*dst*ip*", "indicator"),
    ("*source*addr*", "indicator"),
    ("*dest*addr*", "indicator"),
    # indicator (domain-flavoured raw fields)
    ("*host*name*", "indicator"),
    ("*fqdn*", "indicator"),
    ("*domain*name*", "indicator"),
    # indicator (hash-flavoured raw fields)
    ("*md5*", "indicator"),
    ("*sha1*", "indicator"),
    ("*sha256*", "indicator"),
    ("*sha512*", "indicator"),
    ("*file*hash*", "indicator"),
    # actor
    ("*threat*actor*", "actor"),
    ("*apt*name*", "actor"),
    ("*group*name*", "actor"),
    ("*adversary*", "actor"),
    # published_at
    ("*published*", "published_at"),
    ("*event*time*", "published_at"),
    ("*created*at*", "published_at"),
    # first_seen / last_seen
    ("*first*seen*", "first_seen"),
    ("*last*seen*", "last_seen"),
    ("*updated*at*", "last_seen"),
    # country
    ("*country*code*", "country"),
    ("*geo*country*", "country"),
    # port
    ("*src*port*", "port"),
    ("*dst*port*", "port"),
]


# Wildcards that, when they match, should ALSO emit an indicator_type.
# Key: wildcard-pattern, Value: indicator_type to emit.
# Restricted to patterns whose match unambiguously reveals the type.
_WILDCARD_INDICATOR_TYPE: dict[str, str] = {
    "*src*ip*": "ip",
    "*dst*ip*": "ip",
    "*source*addr*": "ip",
    "*dest*addr*": "ip",
    "*host*name*": "domain",
    "*fqdn*": "domain",
    "*domain*name*": "domain",
    "*md5*": "hash_md5",
    "*sha1*": "hash_sha1",
    "*sha256*": "hash_sha256",
    "*sha512*": "hash_sha512",
    "*file*hash*": "hash",
}


def _specificity(pattern: str) -> int:
    """Rank a pattern: 0=exact, 1=anchored-glob, 2=free-glob.

    Anchored-glob means wildcards (`*` or `?`) appear at exactly one end
    of the pattern and nowhere in the middle.
    """
    if "*" not in pattern and "?" not in pattern:
        return 0
    starts = pattern[0] in "*?"
    ends = pattern[-1] in "*?"
    inner = any(c in "*?" for c in pattern[1:-1])
    if not inner and (starts ^ ends):
        return 1
    return 2


# Precompile with stable sort: (specificity ASC, original-index ASC).
_WILDCARD_COMPILED: list[tuple[str, str]] = sorted(
    list(enumerate(_WILDCARD_PATTERNS)),
    key=lambda item: (_specificity(item[1][0]), item[0]),
)
_WILDCARD_COMPILED = [pc for _, pc in _WILDCARD_COMPILED]


@lru_cache(maxsize=1024)
def _resolve_canonical(raw_field_lower: str) -> tuple[str | None, str | None]:
    """Auto-mode field resolver.

    Returns (canonical, indicator_type_hint).
    `indicator_type_hint` is non-None ONLY when the matched synonym/wildcard
    reveals the type (e.g. "md5" → "hash_md5", "src_ip" → "ip"). Raw key
    "indicator" itself returns (None) as a hint.
    """
    exact = _REVERSE_MAP.get(raw_field_lower)
    if exact is not None:
        hint = _INDICATOR_TYPE_FROM_SYNONYM.get(raw_field_lower) if exact == "indicator" else None
        return exact, hint
    for pattern, canonical in _WILDCARD_COMPILED:
        if fnmatchcase(raw_field_lower, pattern):
            hint = _WILDCARD_INDICATOR_TYPE.get(pattern) if canonical == "indicator" else None
            return canonical, hint
    return None, None


def map_entry_auto(raw: dict[str, Any], source_name: str) -> dict[str, Any]:
    """
    Map a raw entry dict to canonical fields using synonym groups.
    Fields not matching any synonym are placed in extra_norm.

    Side-effect: when an IP/domain/hash synonym matches `indicator`, the
    matching indicator_type is auto-emitted (unless the raw entry already
    supplies one).
    """
    result: dict[str, Any] = {"source_name": source_name}
    extra: dict[str, Any] = {}

    for key, value in raw.items():
        if value is None or value == "":
            continue
        canonical, type_hint = _resolve_canonical(key.lower())
        if canonical and canonical not in result:
            result[canonical] = value
            if (
                canonical == "indicator"
                and type_hint is not None
                and "indicator_type" not in result
            ):
                result["indicator_type"] = type_hint
        else:
            extra[key] = value

    import json
    if extra:
        result["extra_norm"] = json.dumps(extra)

    return result


def map_entry_manual(
    raw: dict[str, Any],
    source_name: str,
    mappings: dict[str, str],
) -> dict[str, Any]:
    """
    Map a raw entry dict using explicit field mappings.
    mappings: {raw_field → canonical_field}
    """
    result: dict[str, Any] = {"source_name": source_name}
    extra: dict[str, Any] = {}

    for key, value in raw.items():
        if value is None or value == "":
            continue
        canonical = mappings.get(key)
        if canonical and canonical not in result:
            result[canonical] = value
        else:
            extra[key] = value

    import json
    if extra:
        result["extra_norm"] = json.dumps(extra)

    return result


async def run_normalizer(trigger: str = "manual") -> dict[str, Any]:
    """
    Background job: reads un-normalized entries from all source DBs,
    maps them to canonical schema, writes to normalized.db, and marks originals.

    Returns a summary dict.

    prompts-021F: per source, the active mapping_version (if any) is the
    authoritative source of truth for ``mode='manual'``:
      * If an active version exists, its mapping is used and its id is
        threaded into the normalized row's ``mapping_version_id`` column.
      * If no active version exists, fall back to yaml
        ``manual_mappings[source]`` (legacy / pre-migration path); rows are
        written with ``mapping_version_id=NULL``.
      * For ``mode='auto'`` the active version is NOT consulted — auto-resolved
        rows are not produced by any specific mapping. They carry
        ``mapping_version_id=NULL``.

    prompts-032 Phase E: ``mode='smart'`` applies the single active CONSOLIDATED
    mapping (``consolidated.py``) to entries from EVERY source — one global
    ``{raw_field: canonical}`` dict, mapped by raw-field name. When no
    consolidated version is active the run falls back to ``auto`` and returns a
    non-null ``warning`` so the UI can surface the Q5 banner. Smart-mode rows
    carry ``mapping_version_id=NULL`` (the consolidated store has its own
    versioning, distinct from the per-source ``mapping_versions`` FK).
    """
    cfg = load_normalizer_config()
    if not cfg.get("enabled", True):
        logger.info("Normalizer disabled; skipping run")
        return {"status": "disabled", "processed": 0, "inserted": 0, "errors": 0}

    mode: str = cfg.get("mode", "auto")
    manual_mappings: dict[str, dict[str, str]] = cfg.get("manual_mappings") or {}

    # Smart mode: resolve the single active consolidated mapping up-front.
    # Absent → fall back to auto and signal a warning for the UI banner (Q5).
    consolidated_map: dict[str, str] | None = None
    warning: str | None = None
    # prompts-039: provenance for the run-history row (smart applies only).
    run_proposal_id: int | None = None
    run_proposal_name: str | None = None
    run_sources: list[str] = []
    if mode == "smart":
        try:
            active = await get_active_consolidated()
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("active consolidated lookup failed: %s", exc)
            active = None
        if active is not None:
            consolidated_map = active.get("mapping") or {}
            run_sources = active.get("sources") or []
            run_proposal_id = active.get("proposal_id")
            if run_proposal_id is not None:
                try:
                    prop = await get_proposal(int(run_proposal_id))
                except Exception as exc:  # pragma: no cover — defensive
                    logger.warning("proposal lookup failed: %s", exc)
                    prop = None
                if prop is not None:
                    run_proposal_name = prop.get("proposal_name")
        else:
            warning = (
                "smart mode is selected but no consolidated mapping is active; "
                "falling back to auto"
            )
            logger.warning(warning)

    # Collect un-normalized entries from all source DBs
    raw_entries = await query_entries(
        source_name=None, limit=10000, filters={"normalized": 0}
    )

    processed = inserted = errors = 0
    by_source: dict[str, list[int]] = {}

    # Per-run cache of (mapping_version_id, mapping_dict) keyed by source name.
    # Sentinel (None, None) means "looked up, no active version exists".
    active_cache: dict[str, tuple[int | None, dict[str, str] | None]] = {}

    async def _resolve_active(src: str) -> tuple[int | None, dict[str, str] | None]:
        if src in active_cache:
            return active_cache[src]
        try:
            row = await get_active_version(src)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning(
                "mapping_versions lookup failed for source %s: %s", src, exc,
            )
            row = None
        if row is None:
            active_cache[src] = (None, None)
        else:
            active_cache[src] = (int(row["id"]), row.get("mapping") or {})
        return active_cache[src]

    for entry in raw_entries:
        source_name = entry.get("source", "unknown")
        entry_id = entry.get("id")

        try:
            mapping_version_id: int | None = None
            if mode == "manual":
                active_id, active_map = await _resolve_active(source_name)
                if active_map is not None:
                    norm = map_entry_manual(entry, source_name, active_map)
                    mapping_version_id = active_id
                elif source_name in manual_mappings:
                    # Legacy path: yaml-only config without a mapping_version row.
                    norm = map_entry_manual(
                        entry, source_name, manual_mappings[source_name]
                    )
                else:
                    norm = map_entry_auto(entry, source_name)
            elif mode == "smart":
                # Active consolidated mapping applies to all feeds; otherwise
                # fall back to auto (warning already recorded above).
                if consolidated_map is not None:
                    norm = map_entry_manual(entry, source_name, consolidated_map)
                else:
                    norm = map_entry_auto(entry, source_name)
            else:
                norm = map_entry_auto(entry, source_name)

            if entry_id is not None:
                norm["source_entry_id"] = entry_id
            if mapping_version_id is not None:
                norm["mapping_version_id"] = mapping_version_id

            ok = await insert_normalized(norm)
            if ok:
                inserted += 1
            processed += 1

            if entry_id is not None:
                by_source.setdefault(source_name, []).append(entry_id)
        except Exception as exc:
            logger.error("Normalizer error for entry %s: %s", entry_id, exc)
            errors += 1

    # Mark original entries as normalized
    for src, ids in by_source.items():
        await mark_normalized(src, ids)

    logger.info(
        "Normalizer run complete: mode=%s processed=%d inserted=%d errors=%d",
        mode, processed, inserted, errors,
    )
    # prompts-039: record the run (best-effort; never break a run on a
    # history-write failure).
    try:
        await record_run(
            trigger=trigger,
            mode=mode,
            status="ok",
            processed=processed,
            inserted=inserted,
            errors=errors,
            proposal_id=run_proposal_id,
            proposal_name=run_proposal_name,
            sources=run_sources,
            warning=warning,
        )
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("run-history record failed: %s", exc)
    # issue_local_006: evaluate realtime watchers against the normalized dataset
    # now that new rows have been indexed. Best-effort; never break a run.
    if inserted:
        try:
            from backend.watchers.engine import run_watchers
            await run_watchers("normalize", {"normalized"})
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("watcher evaluation after normalize failed: %s", exc)
    return {
        "status": "ok",
        "mode": mode,
        "processed": processed,
        "inserted": inserted,
        "errors": errors,
        "warning": warning,
    }
