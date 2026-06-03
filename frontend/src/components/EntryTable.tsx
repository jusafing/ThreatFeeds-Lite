import { useState, useEffect, useCallback, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, Entry, EntriesParams, SummaryItem } from '../api/client'
import { Search, RefreshCw, Eye, EyeOff, ChevronDown, ChevronUp } from 'lucide-react'
import { clsx } from 'clsx'

const SEVERITY_CLASS: Record<string, string> = {
  critical: 'severity-critical',
  high:     'severity-high',
  medium:   'severity-medium',
  low:      'severity-low',
  info:     'severity-info',
}

// Per-source colour palette — dark-theme friendly (900/40 bg, 300 text, 700/30 border)
const SOURCE_COLOURS = [
  'bg-brand-900/40  text-brand-300  border border-brand-700/30',
  'bg-green-900/40  text-green-300  border border-green-700/30',
  'bg-purple-900/40 text-purple-300 border border-purple-700/30',
  'bg-amber-900/40  text-amber-300  border border-amber-700/30',
  'bg-rose-900/40   text-rose-300   border border-rose-700/30',
  'bg-sky-900/40    text-sky-300    border border-sky-700/30',
  'bg-teal-900/40   text-teal-300   border border-teal-700/30',
  'bg-orange-900/40 text-orange-300 border border-orange-700/30',
  'bg-indigo-900/40 text-indigo-300 border border-indigo-700/30',
  'bg-pink-900/40   text-pink-300   border border-pink-700/30',
]

function sourceColourClass(name: string): string {
  let h = 0
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) >>> 0
  return SOURCE_COLOURS[h % SOURCE_COLOURS.length]
}

// Known schema columns — shown first in the column picker, in this stable order.
// issue_local_009: `ingested_at` is pinned to the 2nd position (right after the
// `source`/feed column) both in the picker order and the rendered table.
const SCHEMA_COLUMNS = [
  'source', 'ingested_at', 'indicator', 'indicator_type', 'threat_type',
  'severity', 'title', 'tlp', 'published_at', 'cve_id', 'cvss_score',
  'mitre_attack_id', 'actor', 'country', 'ingest_mode',
]

// Hidden from picker — internal/ID columns of no interest.
const HIDDEN_FROM_PICKER = new Set(['id', 'dedup_key', 'normalized', 'extra'])

// Columns always present in the default view, in this order. The remaining
// default slots (up to DEFAULT_VISIBLE_LIMIT) are filled from the server's
// field-presence ranking (fields that actually carry content). See the
// default-columns effect below.
const ALWAYS_VISIBLE = ['source', 'ingested_at']
const DEFAULT_VISIBLE_LIMIT = 15

// Fallback default-visible set used until the field-presence query resolves
// (or when no presence data exists yet).
const DEFAULT_VISIBLE = new Set([
  'source', 'ingested_at', 'indicator', 'indicator_type', 'threat_type', 'severity', 'title',
])

// Auto-collapse per-source extra-column groups when there are MORE than
// this many groups. Keeps the picker compact for multi-source deployments
// without hiding the picker entirely for small ones.
const AUTOEXPAND_THRESHOLD = 2

export default function EntryTable() {
  const [visibleCols, setVisibleCols]     = useState<Set<string>>(DEFAULT_VISIBLE)
  const [showColPicker, setShowColPicker] = useState(false)
  const [search, setSearch]               = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [refreshInterval, setRefreshInterval] = useState(10)
  const [filters, setFilters]             = useState<Partial<EntriesParams>>({})
  // Collapsibility state for the column picker (prompts-021A item 1):
  //   - schemaExpanded controls the "Schema" row.
  //   - expandedGroups holds the set of source names whose group is open.
  //     A source NOT in the set is collapsed. Default-expansion is decided
  //     once per render of the source list (see effect below) based on
  //     AUTOEXPAND_THRESHOLD; user toggles override that default for the
  //     remainder of the session (state is in-memory only).
  const [schemaExpanded, setSchemaExpanded] = useState(true)
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set())
  const [groupsInitialised, setGroupsInitialised] = useState(false)
  // issue_local_009: the default visible columns are derived once from the
  // server's field-presence ranking (fields that actually carry content).
  // Computed a single time so the user's subsequent manual column toggles are
  // not clobbered by the periodic refetch.
  const [defaultsApplied, setDefaultsApplied] = useState(false)

  // Debounce search
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 400)
    return () => clearTimeout(t)
  }, [search])

  const params: EntriesParams = {
    search: debouncedSearch || undefined,
    limit: 500,
    ...filters,
  }

  const { data, isLoading, error, refetch, isFetching } = useQuery<Entry[]>({
    queryKey: ['entries', params],
    queryFn: () => api.getEntries(params),
    refetchInterval: refreshInterval * 1000,
  })

  // Source list for filter dropdown
  const { data: summaryData = [] } = useQuery<SummaryItem[]>({
    queryKey: ['summary'],
    queryFn: () => api.getSummary(),
    staleTime: 30_000,
  })
  const sourcenames = summaryData
    .filter(r => r.source !== '__total__')
    .map(r => r.source)

  // issue_local_009: field names that carry content in the most recent entries,
  // derived server-side on demand when this table opens. Drives the default
  // visible columns so they reflect the latest ingested data of any feed.
  const { data: presenceFields } = useQuery<string[]>({
    queryKey: ['field-presence'],
    queryFn: () => api.getFieldPresence(),
    staleTime: 60_000,
  })

  // Apply the default-visible set ONCE, as soon as field-presence resolves.
  // Always pins source + ingested_at first, then fills up to
  // DEFAULT_VISIBLE_LIMIT from the populated-field ranking. Falls back to the
  // static DEFAULT_VISIBLE when no presence data exists yet.
  useEffect(() => {
    if (defaultsApplied || presenceFields === undefined) return
    const ranked = presenceFields.filter(
      f => !HIDDEN_FROM_PICKER.has(f) && !ALWAYS_VISIBLE.includes(f),
    )
    if (ranked.length === 0) {
      setDefaultsApplied(true)
      return
    }
    const next = new Set<string>(ALWAYS_VISIBLE)
    for (const f of ranked) {
      if (next.size >= DEFAULT_VISIBLE_LIMIT) break
      next.add(f)
    }
    setVisibleCols(next)
    setDefaultsApplied(true)
  }, [presenceFields, defaultsApplied])

  const toggleCol = useCallback((col: string) => {
    setVisibleCols(prev => {
      const next = new Set(prev)
      next.has(col) ? next.delete(col) : next.add(col)
      return next
    })
  }, [])

  const entries = useMemo(() => data ?? [], [data])

  // Build the union of all observed keys from current entries (excluding
  // hidden internal columns). Also compute, for each non-schema ("extra")
  // column, the set of source names that contribute it — used to group the
  // Columns picker per source.
  const { knownColumns, allColumns, extrasBySource } = useMemo(() => {
    const observed = new Set<string>()
    const bySource = new Map<string, Set<string>>()
    for (const e of entries) {
      const src = String((e as Record<string, unknown>).source ?? '')
      for (const k of Object.keys(e ?? {})) {
        if (HIDDEN_FROM_PICKER.has(k)) continue
        observed.add(k)
        if (!SCHEMA_COLUMNS.includes(k) && src) {
          if (!bySource.has(src)) bySource.set(src, new Set())
          bySource.get(src)!.add(k)
        }
      }
    }
    const knownSet = new Set(SCHEMA_COLUMNS)
    const known = SCHEMA_COLUMNS.slice()
    const extras = Array.from(observed)
      .filter(k => !knownSet.has(k))
      .sort()
    // Sort the groups by source name for stable rendering.
    const sortedBySource = new Map(
      Array.from(bySource.entries())
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([src, cols]) => [src, Array.from(cols).sort()] as const),
    )
    return {
      knownColumns: known,
      allColumns: [...known, ...extras],
      extrasBySource: sortedBySource,
    }
  }, [entries])

  const setManyVisible = useCallback((cols: string[], visible: boolean) => {
    setVisibleCols(prev => {
      const next = new Set(prev)
      for (const c of cols) {
        if (visible) next.add(c)
        else next.delete(c)
      }
      return next
    })
  }, [])

  // Default expansion for per-source groups: when the number of source
  // groups exceeds AUTOEXPAND_THRESHOLD, start COLLAPSED. Otherwise keep
  // everything expanded. Initialise once per "first non-empty set of
  // groups observed" to avoid clobbering the user's manual toggles.
  useEffect(() => {
    if (groupsInitialised) return
    if (extrasBySource.size === 0) return
    if (extrasBySource.size <= AUTOEXPAND_THRESHOLD) {
      setExpandedGroups(new Set(extrasBySource.keys()))
    } else {
      setExpandedGroups(new Set())
    }
    setGroupsInitialised(true)
  }, [extrasBySource, groupsInitialised])

  const toggleGroup = useCallback((src: string) => {
    setExpandedGroups(prev => {
      const next = new Set(prev)
      if (next.has(src)) next.delete(src)
      else next.add(src)
      return next
    })
  }, [])

  const expandAllGroups = useCallback(() => {
    setSchemaExpanded(true)
    setExpandedGroups(new Set(extrasBySource.keys()))
  }, [extrasBySource])

  const collapseAllGroups = useCallback(() => {
    setSchemaExpanded(false)
    setExpandedGroups(new Set())
  }, [])

  const cols = allColumns.filter(c => visibleCols.has(c))

  return (
    <div className="card flex flex-col gap-3">
      {/* Controls bar */}
      <div className="flex flex-wrap items-center gap-2">
        {/* Search */}
        <div className="relative flex-1 min-w-[180px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-500" />
          <input
            className="input pl-8"
            placeholder="Search indicators, titles, actors..."
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>

        {/* Source filter — right next to search */}
        <select
          className="input w-auto"
          value={filters.source ?? ''}
          onChange={e => setFilters(f => ({ ...f, source: e.target.value || undefined }))}
        >
          <option value="">All Sources</option>
          {sourcenames.map(s => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>

        {/* Severity filter */}
        <select
          className="input w-auto"
          value={filters.severity ?? ''}
          onChange={e => setFilters(f => ({ ...f, severity: e.target.value || undefined }))}
        >
          <option value="">All Severities</option>
          {['critical','high','medium','low','info'].map(s => (
            <option key={s} value={s}>{s.charAt(0).toUpperCase() + s.slice(1)}</option>
          ))}
        </select>

        <select
          className="input w-auto"
          value={filters.indicator_type ?? ''}
          onChange={e => setFilters(f => ({ ...f, indicator_type: e.target.value || undefined }))}
        >
          <option value="">All Types</option>
          {['ip','domain','url','hash_md5','hash_sha256','cve','email','file'].map(t => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>

        <select
          className="input w-auto"
          value={filters.ingest_mode ?? ''}
          onChange={e => setFilters(f => ({ ...f, ingest_mode: e.target.value || undefined }))}
        >
          <option value="">All Modes</option>
          {['push','api_pull','rss_pull','local_json','remote_json'].map(m => (
            <option key={m} value={m}>{m}</option>
          ))}
        </select>

        {/* Refresh interval */}
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-gray-500">Refresh</span>
          <select
            className="input w-auto text-xs"
            value={refreshInterval}
            onChange={e => setRefreshInterval(Number(e.target.value))}
          >
            {[5,10,30,60,300].map(s => (
              <option key={s} value={s}>{s}s</option>
            ))}
          </select>
        </div>

        {/* Manual refresh */}
        <button className="btn-ghost" onClick={() => refetch()} title="Refresh now">
          <RefreshCw className={clsx('w-3.5 h-3.5', isFetching && 'animate-spin')} />
        </button>

        {/* Column picker toggle */}
        <button className="btn-secondary text-xs" onClick={() => setShowColPicker(v => !v)}>
          <Eye className="w-3.5 h-3.5" />
          Columns
        </button>
      </div>

      {/* Column picker */}
      {showColPicker && (
        <div className="flex flex-col gap-3 p-3 bg-gray-800 rounded-lg border border-gray-700">
          {/* Bulk expand/collapse (only when there are source groups) */}
          {extrasBySource.size > 0 && (
            <div className="flex items-center justify-end gap-2 -mb-1">
              <button
                className="text-[11px] text-gray-500 hover:text-gray-300 underline-offset-2 hover:underline"
                onClick={expandAllGroups}
              >
                Expand all
              </button>
              <span className="text-gray-700 text-[11px]">·</span>
              <button
                className="text-[11px] text-gray-500 hover:text-gray-300 underline-offset-2 hover:underline"
                onClick={collapseAllGroups}
              >
                Collapse all
              </button>
            </div>
          )}

          {/* Schema row (collapsible). Select all / Clear apply ONLY to
              the schema columns — never the per-source extras. */}
          <div className="flex flex-col gap-1.5">
            <div className="flex items-center gap-2 flex-wrap">
              <button
                className="flex items-center gap-1 text-xs text-gray-300 hover:text-gray-100"
                onClick={() => setSchemaExpanded(v => !v)}
                title={schemaExpanded ? 'Collapse schema columns' : 'Expand schema columns'}
              >
                {schemaExpanded
                  ? <ChevronUp className="w-3 h-3" />
                  : <ChevronDown className="w-3 h-3" />}
                <span className="text-xs text-gray-500">Schema</span>
                <span className="text-[11px] text-gray-600">({knownColumns.length} fields)</span>
              </button>
              <button
                className="text-xs text-gray-400 hover:text-gray-200 underline-offset-2 hover:underline"
                onClick={() => setManyVisible(knownColumns, true)}
              >
                Select all
              </button>
              <span className="text-gray-600">·</span>
              <button
                className="text-xs text-gray-400 hover:text-gray-200 underline-offset-2 hover:underline"
                onClick={() => setManyVisible(knownColumns, false)}
              >
                Clear
              </button>
              <span className="text-[11px] text-gray-500">(this group only)</span>
            </div>
            {schemaExpanded && (
              <div className="flex flex-wrap gap-2">
                {knownColumns.map(col => (
                  <button
                    key={col}
                    onClick={() => toggleCol(col)}
                    className={clsx(
                      'flex items-center gap-1 px-2 py-1 rounded text-xs font-mono transition-colors',
                      visibleCols.has(col)
                        ? 'bg-brand-700/40 text-brand-300 border border-brand-700/50'
                        : 'bg-gray-700 text-gray-400 border border-gray-600 hover:text-gray-200',
                    )}
                  >
                    {visibleCols.has(col) ? <Eye className="w-3 h-3" /> : <EyeOff className="w-3 h-3" />}
                    {col}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Per-source groups for observed (extra) columns. Each group is
              independently collapsible; Select all / Clear inside a group
              affect ONLY that group's columns (see setManyVisible call
              sites — cols is the per-source array). */}
          {extrasBySource.size > 0 && (
            <div className="flex flex-col gap-3 pt-2 border-t border-gray-700">
              {Array.from(extrasBySource.entries()).map(([src, cols]) => {
                const isOpen = expandedGroups.has(src)
                return (
                  <div key={src} className="flex flex-col gap-1.5">
                    <div className="flex items-center gap-2 flex-wrap">
                      <button
                        className="flex items-center gap-1 hover:opacity-90"
                        onClick={() => toggleGroup(src)}
                        title={isOpen ? `Collapse ${src} columns` : `Expand ${src} columns`}
                      >
                        {isOpen
                          ? <ChevronUp className="w-3 h-3 text-gray-400" />
                          : <ChevronDown className="w-3 h-3 text-gray-400" />}
                        <span className={clsx('badge text-xs font-mono', sourceColourClass(src))}>
                          {src}
                        </span>
                      </button>
                      <span className="text-xs text-gray-500">
                        {cols.length} field{cols.length === 1 ? '' : 's'}
                      </span>
                      <button
                        className="text-xs text-gray-400 hover:text-gray-200 underline-offset-2 hover:underline"
                        onClick={() => setManyVisible(cols, true)}
                      >
                        Select all
                      </button>
                      <span className="text-gray-600">·</span>
                      <button
                        className="text-xs text-gray-400 hover:text-gray-200 underline-offset-2 hover:underline"
                        onClick={() => setManyVisible(cols, false)}
                      >
                        Clear
                      </button>
                      <span className="text-[11px] text-gray-500">(this group only)</span>
                    </div>
                    {isOpen && (
                      <div className="flex flex-wrap gap-2">
                        {cols.map(col => (
                          <button
                            key={`${src}:${col}`}
                            onClick={() => toggleCol(col)}
                            className={clsx(
                              'flex items-center gap-1 px-2 py-1 rounded text-xs font-mono transition-colors',
                              visibleCols.has(col)
                                ? 'bg-amber-700/40 text-amber-300 border border-amber-700/50'
                                : 'bg-gray-700 text-gray-400 border border-gray-600 hover:text-gray-200',
                            )}
                          >
                            {visibleCols.has(col) ? <Eye className="w-3 h-3" /> : <EyeOff className="w-3 h-3" />}
                            {col}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}

      {/* Status line */}
      <div className="flex items-center justify-between text-xs text-gray-500">
        <span>{isLoading ? 'Loading...' : `${entries.length} entries`}</span>
        {isFetching && !isLoading && <span className="text-brand-400">Refreshing...</span>}
      </div>

      {error && <div className="text-red-400 text-sm">Failed to load entries.</div>}

      {/* Table */}
      <div className="overflow-auto rounded-lg border border-gray-800 max-h-[60vh]">
        <table className="w-full min-w-max">
          <thead>
            <tr>
              {cols.map(col => (
                <th key={col} className="table-header capitalize">{col.replace(/_/g, ' ')}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {entries.length === 0 && !isLoading ? (
              <tr>
                <td colSpan={cols.length} className="px-3 py-8 text-center text-sm text-gray-500">
                  No entries found.
                </td>
              </tr>
            ) : (
              entries.map((entry, i) => (
                <tr key={entry.id ?? i} className="hover:bg-gray-800/50 transition-colors">
                  {cols.map(col => (
                    <td key={col} className="table-cell">
                      {col === 'source' ? (
                        <span className={clsx('badge text-xs font-mono', sourceColourClass(String(entry.source ?? '')))}>
                          {String(entry.source ?? '—')}
                        </span>
                      ) : col === 'severity' && entry[col] ? (
                        <span className={SEVERITY_CLASS[String(entry[col]).toLowerCase()] ?? 'badge bg-gray-700 text-gray-300'}>
                          {String(entry[col])}
                        </span>
                      ) : col === 'ingested_at' || col === 'published_at' ? (
                        <span className="font-mono text-xs text-gray-400">
                          {entry[col] ? new Date(String(entry[col])).toLocaleString() : '—'}
                        </span>
                      ) : (
                        <span title={String(entry[col] ?? '')}>
                          {entry[col] != null ? String(entry[col]) : '—'}
                        </span>
                      )}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
