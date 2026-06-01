import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { useRunNow, useNormalizerRunning } from '../hooks/useNormalizerRun'
import {
  Search, RefreshCw, AlertTriangle, Play, Loader2,
  SlidersHorizontal, ChevronLeft, ChevronRight, Filter,
} from 'lucide-react'

// prompts-040: fallback columns shown when no consolidated mapping is active
// (auto / manual mode, or smart mode with nothing approved yet). When a mapping
// IS active the table tracks its canonical fields instead — see `columns` below.
const CANONICAL_COLS = [
  'source_name', 'ip_address', 'domain', 'hash', 'severity',
  'timestamp', 'actor', 'cve', 'port', 'country',
]

// prompts-043: page-size options for the client-side paginated viewer.
const PAGE_SIZE_OPTIONS = [50, 100, 150, 200]
const DEFAULT_PAGE_SIZE = 100

// prompts-043: a column whose distinct-value count is at or below this gets a
// unique-value dropdown filter (like the Sources filter); higher-cardinality
// columns get a free-text "contains" filter instead.
const LOW_CARD_THRESHOLD = 25

// prompts-044: these product/cve/cvss columns always get a unique-value dropdown
// filter regardless of cardinality (operators filter them by exact value). `title`
// is intentionally excluded — it is near-unique per row, so it stays a text filter.
const FORCE_DROPDOWN_COLS = new Set([
  'cve_id', 'cvss_score', 'cvss_vector', 'affected_product', 'affected_vendor',
])

// prompts-044: column display order. Feed first, then publish date, then the
// "informative" fields operators scan for, then everything else (alphabetical),
// with normalized_at always pinned last (handled separately).
const COLUMN_PRIORITY = [
  'source_name',
  'published_at',
  'title', 'indicator', 'indicator_type', 'cve_id', 'cvss_score', 'cvss_vector',
  'affected_product', 'affected_vendor', 'severity', 'threat_type', 'actor',
  'malware_family', 'campaign',
]

// Sort a column list by COLUMN_PRIORITY; unlisted columns sort alphabetically
// after the prioritised ones. `normalized_at` is appended by the caller.
function orderColumns(cols: string[]): string[] {
  return [...cols].sort((a, b) => {
    const ia = COLUMN_PRIORITY.indexOf(a)
    const ib = COLUMN_PRIORITY.indexOf(b)
    if (ia !== -1 && ib !== -1) return ia - ib
    if (ia !== -1) return -1
    if (ib !== -1) return 1
    return a.localeCompare(b)
  })
}

// prompts-043: deterministic per-source color chips for visual distinction.
const SOURCE_PALETTE = [
  'bg-blue-900/40 text-blue-300 border-blue-700/40',
  'bg-emerald-900/40 text-emerald-300 border-emerald-700/40',
  'bg-amber-900/40 text-amber-300 border-amber-700/40',
  'bg-violet-900/40 text-violet-300 border-violet-700/40',
  'bg-rose-900/40 text-rose-300 border-rose-700/40',
  'bg-cyan-900/40 text-cyan-300 border-cyan-700/40',
  'bg-fuchsia-900/40 text-fuchsia-300 border-fuchsia-700/40',
  'bg-lime-900/40 text-lime-300 border-lime-700/40',
  'bg-orange-900/40 text-orange-300 border-orange-700/40',
  'bg-teal-900/40 text-teal-300 border-teal-700/40',
]

function sourceColor(name: string): string {
  let h = 0
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0
  return SOURCE_PALETTE[h % SOURCE_PALETTE.length]
}

function formatCell(col: string, value: unknown): string {
  if (value == null) return ''
  const s = String(value)
  if (col === 'normalized_at') return s.replace('T', ' ').slice(0, 19)
  return s
}

export default function NormalizedTable() {
  // Search: typed value vs. the committed term (applied on Search button / Enter).
  const [searchInput, setSearchInput] = useState('')
  const [appliedSearch, setAppliedSearch] = useState('')
  // Per-column filter values (empty string = inactive).
  const [fieldFilters, setFieldFilters] = useState<Record<string, string>>({})
  const [pageSize, setPageSize] = useState<number>(DEFAULT_PAGE_SIZE)
  const [page, setPage] = useState(1)
  const [hiddenCols, setHiddenCols] = useState<Set<string>>(new Set())
  const [showColumnPicker, setShowColumnPicker] = useState(false)
  // prompts-044: per-field filter bar is collapsed by default.
  const [showFilters, setShowFilters] = useState(false)

  // prompts-040: shared global run state. "Run & Apply" re-runs the normalizer
  // (mode-aware) and the flag disables every run button while a run is active.
  const runNow = useRunNow()
  const running = useNormalizerRunning()

  // prompts-043: the Application setting caps how many rows the viewer pulls in
  // one request; all paging/filtering/search then happens client-side.
  const { data: paginationMaxData } = useQuery({
    queryKey: ['pagination-max'],
    queryFn: api.getPaginationMax,
  })
  const paginationMax = paginationMaxData?.pagination_max ?? 1000

  const { data: entries = [], isFetching, refetch } = useQuery({
    queryKey: ['normalized-entries', paginationMax],
    queryFn: () => api.getNormalizedEntries({ limit: paginationMax }),
    refetchInterval: 30_000,
  })

  // prompts-039: warn in the Viewer when the normalizer is disabled, since the
  // table below will not refresh until it is re-enabled and run.
  const { data: normCfg } = useQuery({
    queryKey: ['normalizer-config'],
    queryFn: () => api.getNormalizerConfig(),
  })
  const normalizerDisabled = normCfg?.['enabled'] === false

  // prompts-040: when a consolidated mapping is active, the table columns track
  // its canonical fields so the viewer reflects exactly what the active mapping
  // produces. Fall back to the static canonical set when no mapping is active.
  const { data: activeRes } = useQuery({
    queryKey: ['smart-active'],
    queryFn: () => api.smartMappings.getActive(),
  })

  const dataColumns = useMemo(() => {
    const mapping = activeRes?.active?.mapping
    if (mapping && Object.keys(mapping).length > 0) {
      // prompts-044: exclude canonical `source` — it duplicates the housekeeping
      // `source_name` column (the "Source" vs "Source Name" duplicate filter).
      const canonical = Array.from(new Set(Object.values(mapping)))
        .filter((c) => c !== 'source_name' && c !== 'source' && c !== 'normalized_at')
      return orderColumns(['source_name', ...canonical])
    }
    return orderColumns(CANONICAL_COLS)
  }, [activeRes])

  // normalized_at is always the trailing column of the universe.
  const allColumns = useMemo(() => [...dataColumns, 'normalized_at'], [dataColumns])

  // Distinct non-empty string values per column (from the fetched window).
  const distinct = useMemo(() => {
    const m: Record<string, string[]> = {}
    for (const col of allColumns) {
      const set = new Set<string>()
      for (const e of entries) {
        const v = (e as Record<string, unknown>)[col]
        if (v != null && String(v) !== '') set.add(String(v))
      }
      m[col] = Array.from(set).sort()
    }
    return m
  }, [entries, allColumns])

  const isDropdownCol = (col: string) => {
    const n = distinct[col]?.length ?? 0
    if (n === 0) return false
    // prompts-044: forced columns are always dropdowns; others use cardinality.
    return FORCE_DROPDOWN_COLS.has(col) || n <= LOW_CARD_THRESHOLD
  }

  // Apply per-column filters, then the committed search term — all client-side.
  const filtered = useMemo(() => {
    let rows = entries as Record<string, unknown>[]
    for (const [col, val] of Object.entries(fieldFilters)) {
      if (!val) continue
      const eq = isDropdownCol(col)
      rows = rows.filter((e) => {
        const cell = e[col] != null ? String(e[col]) : ''
        return eq ? cell === val : cell.toLowerCase().includes(val.toLowerCase())
      })
    }
    const term = appliedSearch.trim().toLowerCase()
    if (term) {
      rows = rows.filter((e) =>
        allColumns.some((col) => {
          const cell = e[col]
          return cell != null && String(cell).toLowerCase().includes(term)
        }),
      )
    }
    return rows
    // isDropdownCol depends on `distinct`, which is in deps via reference.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entries, fieldFilters, appliedSearch, distinct, allColumns])

  // Reset to page 1 whenever the result set changes shape.
  useEffect(() => {
    setPage(1)
  }, [appliedSearch, fieldFilters, pageSize, entries])

  const total = filtered.length
  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  const safePage = Math.min(page, totalPages)
  const start = (safePage - 1) * pageSize
  const pageRows = filtered.slice(start, start + pageSize)

  const visibleColumns = allColumns.filter((c) => !hiddenCols.has(c))

  const applySearch = () => setAppliedSearch(searchInput)
  const setFilter = (col: string, val: string) =>
    setFieldFilters((prev) => ({ ...prev, [col]: val }))
  const toggleColumn = (col: string) =>
    setHiddenCols((prev) => {
      const next = new Set(prev)
      if (next.has(col)) next.delete(col)
      else next.add(col)
      return next
    })
  // prompts-044: bulk column visibility. "Clear all" always keeps the feed name.
  const showAllColumns = () => setHiddenCols(new Set())
  const clearAllColumns = () =>
    setHiddenCols(new Set(allColumns.filter((c) => c !== 'source_name')))

  return (
    <div className="space-y-4">
      {normalizerDisabled && (
        <div
          role="alert"
          className="flex items-start gap-2 rounded border border-amber-700/60 bg-amber-900/20 p-2.5 text-xs text-amber-300"
        >
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
          <span>
            The normalizer is currently disabled. These results are not being
            updated. Enable it in the Normalizer → Settings tab to resume
            normalization.
          </span>
        </div>
      )}

      {/* Action row: page size, columns toggle, refresh, run */}
      <div className="flex items-center gap-3 flex-wrap">
        <label className="flex items-center gap-1.5 text-xs text-gray-400">
          Rows per page
          <select
            value={pageSize}
            onChange={(e) => setPageSize(Number(e.target.value))}
            className="input w-24"
            aria-label="Rows per page"
          >
            {PAGE_SIZE_OPTIONS.map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
        </label>
        <button
          onClick={() => setShowColumnPicker((v) => !v)}
          className="btn btn-secondary flex items-center gap-1.5"
          aria-expanded={showColumnPicker}
        >
          <SlidersHorizontal className="h-4 w-4" />
          Columns
        </button>
        <button
          onClick={() => setShowFilters((v) => !v)}
          className="btn btn-secondary flex items-center gap-1.5"
          aria-expanded={showFilters}
        >
          <Filter className="h-4 w-4" />
          Filters
        </button>
        <div className="flex-1" />
        <button
          onClick={() => refetch()}
          className="btn btn-secondary flex items-center gap-1.5"
          title="Refresh Table"
        >
          <RefreshCw className={`h-4 w-4 ${isFetching ? 'animate-spin' : ''}`} />
          Refresh Table
        </button>
        <button
          onClick={() => runNow.mutate()}
          disabled={running}
          className="btn btn-primary flex items-center gap-1.5 disabled:opacity-50"
          title="Re-run the normalizer and re-apply the active mapping"
        >
          {running ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Play className="h-4 w-4" />
          )}
          {running ? 'Running…' : 'Run & Apply'}
        </button>
      </div>

      {/* Search row (prompts-043): dedicated row above the filters. */}
      <div className="flex items-center gap-2">
        <div className="relative flex-1 max-w-md">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-gray-500 pointer-events-none" />
          <input
            type="text"
            placeholder="Search all fields…"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') applySearch() }}
            className="input pl-8 w-full"
            aria-label="Search all fields"
          />
        </div>
        <button
          onClick={applySearch}
          className="btn btn-primary flex items-center gap-1.5"
        >
          <Search className="h-4 w-4" />
          Search
        </button>
      </div>

      {/* Per-field filters (prompts-043): one control per column. Collapsed by
          default (prompts-044) — toggled via the "Filters" button. */}
      {showFilters && (
        <div className="flex items-end gap-3 flex-wrap">
          {allColumns.map((col) => (
            <div key={col} className="flex flex-col gap-1">
              <span className="text-[10px] uppercase tracking-wide text-gray-500">
                {col.replace(/_/g, ' ')}
              </span>
              {isDropdownCol(col) ? (
                <select
                  value={fieldFilters[col] ?? ''}
                  onChange={(e) => setFilter(col, e.target.value)}
                  className="input w-40"
                  aria-label={`Filter by ${col}`}
                >
                  <option value="">All</option>
                  {distinct[col].map((v) => (
                    <option key={v} value={v}>{v}</option>
                  ))}
                </select>
              ) : (
                <input
                  type="text"
                  value={fieldFilters[col] ?? ''}
                  onChange={(e) => setFilter(col, e.target.value)}
                  placeholder="contains…"
                  className="input w-40"
                  aria-label={`Filter by ${col}`}
                />
              )}
            </div>
          ))}
        </div>
      )}

      {/* Column picker (prompts-043): hidden by default. */}
      {showColumnPicker && (
        <div className="rounded-lg border border-gray-800 bg-gray-900/40 p-3">
          <div className="flex items-center justify-between mb-2">
            <p className="text-[10px] uppercase tracking-wide text-gray-500">
              Visible columns
            </p>
            <div className="flex items-center gap-2">
              <button
                onClick={showAllColumns}
                className="text-[11px] rounded px-2 py-0.5 border border-gray-700 text-gray-300 hover:bg-gray-800"
              >
                Add all
              </button>
              <button
                onClick={clearAllColumns}
                className="text-[11px] rounded px-2 py-0.5 border border-gray-700 text-gray-300 hover:bg-gray-800"
              >
                Clear all
              </button>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {allColumns.map((col) => {
              const visible = !hiddenCols.has(col)
              return (
                <button
                  key={col}
                  onClick={() => toggleColumn(col)}
                  aria-pressed={visible}
                  className={`text-xs rounded px-2 py-1 border transition-colors ${
                    visible
                      ? 'bg-brand-900/40 text-brand-300 border-brand-700/40'
                      : 'bg-gray-800 text-gray-500 border-gray-700'
                  }`}
                >
                  {col.replace(/_/g, ' ')}
                </button>
              )
            })}
          </div>
        </div>
      )}

      {/* Count */}
      <p className="text-xs text-gray-500">
        {total === 0
          ? '0 entries'
          : `Showing ${start + 1}–${Math.min(start + pageSize, total)} of ${total} entries`}
        {entries.length >= paginationMax && (
          <span className="text-gray-600"> (capped at {paginationMax})</span>
        )}
      </p>

      {/* Table */}
      <div className="overflow-x-auto rounded-lg border border-gray-800">
        <table className="w-full text-sm text-left text-gray-300">
          <thead className="text-xs uppercase text-gray-500 bg-gray-900 border-b border-gray-800">
            <tr>
              {visibleColumns.map((col) => (
                <th key={col} className="px-4 py-3 font-medium whitespace-nowrap">
                  {col.replace(/_/g, ' ')}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-800">
            {pageRows.length === 0 ? (
              <tr>
                <td
                  colSpan={Math.max(1, visibleColumns.length)}
                  className="px-4 py-8 text-center text-gray-600 italic"
                >
                  No normalized entries match. Adjust filters, or run the
                  normalizer and wait for the scheduled job.
                </td>
              </tr>
            ) : (
              pageRows.map((entry, idx) => (
                <tr key={start + idx} className="hover:bg-gray-800/40 transition-colors">
                  {visibleColumns.map((col) => {
                    if (col === 'source_name') {
                      const name = entry[col] != null ? String(entry[col]) : ''
                      return (
                        <td key={col} className="px-4 py-2.5 text-xs whitespace-nowrap">
                          {name && (
                            <span
                              data-source-chip={name}
                              className={`inline-block rounded border px-1.5 py-0.5 font-mono ${sourceColor(name)}`}
                            >
                              {name}
                            </span>
                          )}
                        </td>
                      )
                    }
                    return (
                      <td
                        key={col}
                        className={`px-4 py-2.5 text-xs max-w-[180px] truncate ${
                          col === 'normalized_at'
                            ? 'text-gray-500 whitespace-nowrap'
                            : 'text-gray-400'
                        }`}
                      >
                        {formatCell(col, entry[col])}
                      </td>
                    )
                  })}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-3 text-xs text-gray-400">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={safePage <= 1}
            className="btn btn-secondary flex items-center gap-1 disabled:opacity-40"
            aria-label="Previous page"
          >
            <ChevronLeft className="h-4 w-4" /> Prev
          </button>
          <span className="tabular-nums">
            Page {safePage} of {totalPages}
          </span>
          <button
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={safePage >= totalPages}
            className="btn btn-secondary flex items-center gap-1 disabled:opacity-40"
            aria-label="Next page"
          >
            Next <ChevronRight className="h-4 w-4" />
          </button>
        </div>
      )}
    </div>
  )
}
