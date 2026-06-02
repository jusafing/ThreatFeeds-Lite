import { useState, useEffect, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  api,
  Watcher,
  WatcherInput,
  WatcherCondition,
  WatcherDataset,
  WatcherSeverity,
  WatcherMode,
  WatcherFormat,
  WatcherMatchType,
} from '../api/client'
import Toggle from '../components/Toggle'
import { getAppBasePrefix } from '../utils/basePrefix'
import { clsx } from 'clsx'
import { Plus, Trash2, Pencil, ChevronDown, ChevronUp, Copy, X } from 'lucide-react'

type Tab = 'summary' | 'config' | 'details'

const SEVERITIES: WatcherSeverity[] = ['low', 'medium', 'high', 'critical']
const DATASETS: WatcherDataset[] = ['all', 'raw', 'normalized']
const MODES: WatcherMode[] = ['realtime', 'scheduled']
const FORMATS: WatcherFormat[] = ['json', 'csv', 'xml']
const MATCH_TYPES: WatcherMatchType[] = ['exact', 'wildcard', 'regex', 'gte', 'lte']
const MATCH_TYPE_LABELS: Record<WatcherMatchType, string> = {
  exact: 'exact',
  wildcard: 'wildcard',
  regex: 'regex',
  gte: '≥ (numeric)',
  lte: '≤ (numeric)',
}

const MAX_FEED_MIN = 1
const MAX_FEED_MAX = 100_000
const INTERVAL_MIN = 10

function feedUrl(id: string): string {
  const prefix = getAppBasePrefix()
  const path = `${prefix}/feed/watcher/${id}/`
  return `${window.location.origin}${path}`
}

/** Copy text to clipboard, falling back to a hidden textarea + execCommand when
 *  the async Clipboard API is unavailable (insecure context / older browsers).
 *  Returns whether the copy is believed to have succeeded. */
async function copyToClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    /* fall through to legacy path */
  }
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.focus()
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  } catch {
    return false
  }
}

function formatTimestamp(ts: string | null | undefined): string {
  if (!ts) return 'never'
  const d = new Date(ts)
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleString()
}

export default function Watchers() {
  const [activeTab, setActiveTab] = useState<Tab>('summary')

  const TABS: { id: Tab; label: string }[] = [
    { id: 'summary', label: 'Summary' },
    { id: 'config', label: 'Configuration' },
    { id: 'details', label: 'Details' },
  ]

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-lg font-semibold text-gray-100">Watchers</h1>
        <p className="text-sm text-gray-500">
          Saved filters that publish matching events to a public feed URL.
        </p>
      </div>

      <div className="border-b border-gray-800">
        <nav className="flex gap-6 flex-wrap">
          {TABS.map(({ id, label }) => (
            <button
              key={id}
              onClick={() => setActiveTab(id)}
              className={clsx(
                'pb-3 text-sm font-medium transition-colors',
                activeTab === id ? 'tab-active' : 'tab-inactive',
              )}
            >
              {label}
            </button>
          ))}
        </nav>
      </div>

      <div>
        {activeTab === 'summary' && <SummaryTab />}
        {activeTab === 'config' && <ConfigTab />}
        {activeTab === 'details' && <DetailsTab />}
      </div>
    </div>
  )
}

// ── Shared query ─────────────────────────────────────────────────────────────

function useWatchers() {
  return useQuery({ queryKey: ['watchers'], queryFn: api.watchers.list })
}

function FeedLink({ id }: { id: string }) {
  const [copied, setCopied] = useState(false)
  const url = feedUrl(id)
  return (
    <div className="flex items-center gap-1.5">
      <a
        href={url}
        target="_blank"
        rel="noreferrer"
        className="text-xs text-blue-400 hover:underline font-mono truncate max-w-md"
      >
        {url}
      </a>
      <button
        title="Copy feed URL"
        className="text-gray-500 hover:text-gray-300"
        onClick={async () => {
          const ok = await copyToClipboard(url)
          if (ok) {
            setCopied(true)
            setTimeout(() => setCopied(false), 1500)
          }
        }}
      >
        <Copy className="w-3.5 h-3.5" />
      </button>
      {copied && <span className="text-xs text-green-400">copied</span>}
    </div>
  )
}

// ── Summary tab ──────────────────────────────────────────────────────────────

function SummaryTab() {
  const qc = useQueryClient()
  const { data: watchers, isLoading } = useWatchers()

  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      api.watchers.setEnabled(id, enabled),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['watchers'] }),
  })

  if (isLoading) return <p className="text-sm text-gray-500">Loading…</p>
  if (!watchers || watchers.length === 0)
    return <p className="text-sm text-gray-500">No watchers yet. Create one in the Configuration tab.</p>

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-gray-500 border-b border-gray-800">
            <th className="py-2 pr-4">Name</th>
            <th className="py-2 pr-4">Severity</th>
            <th className="py-2 pr-4">Dataset</th>
            <th className="py-2 pr-4">Mode</th>
            <th className="py-2 pr-4">Format</th>
            <th className="py-2 pr-4">Triggers</th>
            <th className="py-2 pr-4">Last Triggered</th>
            <th className="py-2 pr-4">Enabled</th>
            <th className="py-2 pr-4">Feed URL</th>
          </tr>
        </thead>
        <tbody>
          {watchers.map((w) => (
            <tr key={w.id} className="border-b border-gray-800/60">
              <td className="py-2 pr-4 text-gray-200">{w.name}</td>
              <td className="py-2 pr-4 text-gray-400">{w.severity}</td>
              <td className="py-2 pr-4 text-gray-400">{w.dataset}</td>
              <td className="py-2 pr-4 text-gray-400">
                {w.mode === 'scheduled' ? `scheduled (${w.interval_sec}s)` : 'realtime'}
              </td>
              <td className="py-2 pr-4 text-gray-400 uppercase">{w.format}</td>
              <td className="py-2 pr-4 tabular-nums text-gray-300">{w.trigger_count}</td>
              <td className="py-2 pr-4 whitespace-nowrap text-gray-400">
                {formatTimestamp(w.last_triggered_at)}
              </td>
              <td className="py-2 pr-4">
                <Toggle
                  checked={w.enabled}
                  disabled={toggle.isPending}
                  onChange={(v) => toggle.mutate({ id: w.id, enabled: v })}
                />
              </td>
              <td className="py-2 pr-4">
                <FeedLink id={w.id} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Configuration tab ────────────────────────────────────────────────────────

function ConfigTab() {
  const qc = useQueryClient()
  const { data: watchers } = useWatchers()
  const [showForm, setShowForm] = useState(false)
  const [editing, setEditing] = useState<Watcher | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)
  const [blockedMsg, setBlockedMsg] = useState<string | null>(null)
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null)

  const formOpen = showForm || editing !== null

  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      api.watchers.setEnabled(id, enabled),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['watchers'] }),
  })
  const remove = useMutation({
    mutationFn: (id: string) => api.watchers.remove(id),
    onSuccess: () => {
      setConfirmingDelete(null)
      qc.invalidateQueries({ queryKey: ['watchers'] })
    },
  })

  const closeForm = () => {
    setShowForm(false)
    setEditing(null)
    setDirty(false)
    setBlockedMsg(null)
  }

  // Guard against silently discarding unsaved edits when the user starts a new
  // create/edit while a dirty form is open. Allow the switch when the form is
  // pristine; otherwise surface an inline message and keep the current form.
  const requestOpenCreate = () => {
    if (formOpen && dirty) {
      setBlockedMsg('Finish or discard your current changes before adding another watcher.')
      return
    }
    setBlockedMsg(null)
    setEditing(null)
    setShowForm(true)
  }
  const requestEdit = (w: Watcher) => {
    if (editing?.id === w.id) return
    if (formOpen && dirty) {
      setBlockedMsg(`Finish or discard your current changes before editing "${w.name}".`)
      return
    }
    setBlockedMsg(null)
    setShowForm(false)
    setEditing(w)
  }

  return (
    <div className="space-y-4 max-w-3xl">
      {!formOpen && (
        <button className="btn-primary text-xs inline-flex items-center gap-1" onClick={requestOpenCreate}>
          <Plus className="w-4 h-4" /> Add Watcher
        </button>
      )}

      {blockedMsg && <p className="text-xs text-amber-400">{blockedMsg}</p>}

      {formOpen && (
        <WatcherForm
          key={editing?.id ?? 'new'}
          existing={watchers ?? []}
          editing={editing}
          onDirtyChange={setDirty}
          onClose={closeForm}
          onSaved={() => {
            closeForm()
            qc.invalidateQueries({ queryKey: ['watchers'] })
          }}
        />
      )}

      <div className="space-y-2">
        {(watchers ?? []).map((w) => (
          <div key={w.id} className="border border-gray-700 rounded-lg">
            <div className="flex items-center justify-between px-3 py-2.5">
              <button
                className="flex items-center gap-2 text-left"
                onClick={() => setExpanded(expanded === w.id ? null : w.id)}
              >
                {expanded === w.id ? (
                  <ChevronUp className="w-4 h-4 text-gray-500" />
                ) : (
                  <ChevronDown className="w-4 h-4 text-gray-500" />
                )}
                <span className="text-sm text-gray-200">{w.name}</span>
                <span className="text-xs text-gray-500">
                  {w.severity} · {w.dataset} · {w.mode}
                </span>
              </button>
              <div className="flex items-center gap-3">
                <Toggle
                  checked={w.enabled}
                  disabled={toggle.isPending}
                  onChange={(v) => toggle.mutate({ id: w.id, enabled: v })}
                />
                <button
                  title="Edit"
                  className="text-gray-400 hover:text-gray-200"
                  onClick={() => requestEdit(w)}
                >
                  <Pencil className="w-4 h-4" />
                </button>
                <button
                  title="Delete"
                  className="text-gray-400 hover:text-red-400"
                  onClick={() => setConfirmingDelete(w.id)}
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            </div>
            {confirmingDelete === w.id && (
              <div className="border-t border-gray-800 px-3 py-2.5 bg-red-950/20 flex items-center justify-between gap-3">
                <span className="text-xs text-red-300">
                  Delete watcher "{w.name}"? This permanently removes its triggered events.
                </span>
                <div className="flex items-center gap-2 shrink-0">
                  <button
                    className="btn-secondary text-xs"
                    disabled={remove.isPending}
                    onClick={() => setConfirmingDelete(null)}
                  >
                    Cancel
                  </button>
                  <button
                    className="text-xs px-2.5 py-1 rounded bg-red-700 hover:bg-red-600 text-white disabled:opacity-50"
                    disabled={remove.isPending}
                    onClick={() => remove.mutate(w.id)}
                  >
                    {remove.isPending ? 'Deleting…' : 'Confirm delete'}
                  </button>
                </div>
              </div>
            )}
            {expanded === w.id && (
              <div className="border-t border-gray-800 px-3 py-2.5 space-y-1 text-xs text-gray-400">
                <p>Feeds: {w.feeds.length ? w.feeds.join(', ') : 'all'}</p>
                <p>
                  Conditions:{' '}
                  {w.conditions.length
                    ? w.conditions
                        .map((c) => `${c.field || '*'} ${c.match_type} "${c.value}"`)
                        .join(' AND ')
                    : 'none'}
                </p>
                <p>Format: {w.format.toUpperCase()} · Max events: {w.max_feed_events}</p>
                <FeedLink id={w.id} />
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Create / edit form ───────────────────────────────────────────────────────

const EMPTY: WatcherInput = {
  name: '',
  severity: 'critical',
  dataset: 'all',
  feeds: [],
  conditions: [],
  mode: 'realtime',
  interval_sec: 120,
  format: 'json',
  max_feed_events: 10,
  enabled: false,
}

function WatcherForm({
  existing,
  editing,
  onClose,
  onSaved,
  onDirtyChange,
}: {
  existing: Watcher[]
  editing: Watcher | null
  onClose: () => void
  onSaved: () => void
  onDirtyChange?: (dirty: boolean) => void
}) {
  const initial = useMemo<WatcherInput>(
    () =>
      editing
        ? {
            name: editing.name,
            severity: editing.severity,
            dataset: editing.dataset,
            feeds: [...editing.feeds],
            conditions: editing.conditions.map((c) => ({ ...c })),
            mode: editing.mode,
            interval_sec: editing.interval_sec,
            format: editing.format,
            max_feed_events: editing.max_feed_events,
            enabled: editing.enabled,
          }
        : { ...EMPTY },
    [editing],
  )
  const [form, setForm] = useState<WatcherInput>(initial)
  const [error, setError] = useState<string | null>(null)

  // Report dirtiness to the parent so it can guard against discarding edits.
  const dirty = useMemo(
    () => JSON.stringify(form) !== JSON.stringify(initial),
    [form, initial],
  )
  useEffect(() => {
    onDirtyChange?.(dirty)
  }, [dirty, onDirtyChange])

  const { data: feedsMeta } = useQuery({
    queryKey: ['watcher-meta-feeds'],
    queryFn: api.watchers.metaFeeds,
  })
  // Field list is feed-aware: re-derived from sampled stored events whenever the
  // dataset or the selected feeds change, so per-source custom fields surface.
  const sortedFeeds = useMemo(() => [...form.feeds].sort(), [form.feeds])
  const { data: fieldsMeta } = useQuery({
    queryKey: ['watcher-meta-fields', form.dataset, sortedFeeds],
    queryFn: () => api.watchers.metaFields(form.dataset, form.feeds),
  })

  const set = <K extends keyof WatcherInput>(k: K, v: WatcherInput[K]) =>
    setForm((f) => ({ ...f, [k]: v }))

  const nameTaken = useMemo(() => {
    const trimmed = form.name.trim().toLowerCase()
    return existing.some(
      (w) => w.name.trim().toLowerCase() === trimmed && (!editing || w.id !== editing.id),
    )
  }, [form.name, existing, editing])

  const validInterval = form.mode !== 'scheduled' || form.interval_sec >= INTERVAL_MIN
  const validMax = form.max_feed_events >= MAX_FEED_MIN && form.max_feed_events <= MAX_FEED_MAX
  const hasCondition = form.conditions.length > 0
  const numericConditionsValid = form.conditions.every(
    (c) =>
      (c.match_type !== 'gte' && c.match_type !== 'lte') ||
      (c.value.trim() !== '' && !Number.isNaN(Number(c.value.trim()))),
  )
  const canSave =
    form.name.trim() !== '' &&
    !nameTaken &&
    validInterval &&
    validMax &&
    hasCondition &&
    numericConditionsValid

  const save = useMutation({
    mutationFn: (body: WatcherInput) =>
      editing ? api.watchers.update(editing.id, body) : api.watchers.create(body),
    onSuccess: onSaved,
    onError: (err: unknown) => setError(err instanceof Error ? err.message : String(err)),
  })

  const addCondition = () =>
    set('conditions', [...form.conditions, { field: '', value: '', match_type: 'exact' }])
  const updateCondition = (i: number, patch: Partial<WatcherCondition>) =>
    set(
      'conditions',
      form.conditions.map((c, idx) => (idx === i ? { ...c, ...patch } : c)),
    )
  const removeCondition = (i: number) =>
    set('conditions', form.conditions.filter((_, idx) => idx !== i))

  const toggleFeed = (name: string) =>
    set('feeds', form.feeds.includes(name) ? form.feeds.filter((f) => f !== name) : [...form.feeds, name])

  return (
    <div className="border border-gray-700 rounded-lg p-4 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-gray-200">
          {editing ? `Edit "${editing.name}"` : 'New Watcher'}
        </h2>
        <button className="text-gray-500 hover:text-gray-300" onClick={onClose}>
          <X className="w-4 h-4" />
        </button>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <label className="space-y-1">
          <span className="text-xs text-gray-400">Name</span>
          <input
            className="input w-full"
            value={form.name}
            onChange={(e) => set('name', e.target.value)}
          />
        </label>
        <label className="space-y-1">
          <span className="text-xs text-gray-400">Severity (label)</span>
          <select className="input w-full" value={form.severity} onChange={(e) => set('severity', e.target.value as WatcherSeverity)}>
            {SEVERITIES.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
          <span className="text-[11px] text-gray-500">
            Classification label only — does not affect matching. To match on
            severity, add a condition with field “severity”.
          </span>
        </label>
        <label className="space-y-1">
          <span className="text-xs text-gray-400">Dataset</span>
          <select className="input w-full" value={form.dataset} onChange={(e) => set('dataset', e.target.value as WatcherDataset)}>
            {DATASETS.map((d) => (
              <option key={d} value={d}>{d}</option>
            ))}
          </select>
        </label>
        <label className="space-y-1">
          <span className="text-xs text-gray-400">Format</span>
          <select className="input w-full" value={form.format} onChange={(e) => set('format', e.target.value as WatcherFormat)}>
            {FORMATS.map((f) => (
              <option key={f} value={f}>{f.toUpperCase()}</option>
            ))}
          </select>
        </label>
        <label className="space-y-1">
          <span className="text-xs text-gray-400">Mode</span>
          <select className="input w-full" value={form.mode} onChange={(e) => set('mode', e.target.value as WatcherMode)}>
            {MODES.map((m) => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
        </label>
        {form.mode === 'scheduled' && (
          <label className="space-y-1">
            <span className="text-xs text-gray-400">Interval (seconds)</span>
            <input
              type="number"
              min={INTERVAL_MIN}
              className="input w-full tabular-nums"
              value={form.interval_sec}
              onChange={(e) => set('interval_sec', Number(e.target.value))}
            />
          </label>
        )}
        <label className="space-y-1">
          <span className="text-xs text-gray-400">Max feed events</span>
          <input
            type="number"
            min={MAX_FEED_MIN}
            max={MAX_FEED_MAX}
            className="input w-full tabular-nums"
            value={form.max_feed_events}
            onChange={(e) => set('max_feed_events', Number(e.target.value))}
          />
        </label>
      </div>

      {/* Feeds multiselect */}
      <div className="space-y-1">
        <p className="text-xs text-gray-400">Feeds (empty = all feeds)</p>
        <div className="flex flex-wrap gap-1.5 max-h-32 overflow-y-auto border border-gray-800 rounded p-2">
          {(feedsMeta?.feeds ?? []).map((name) => (
            <button
              key={name}
              onClick={() => toggleFeed(name)}
              className={clsx(
                'text-xs px-2 py-0.5 rounded border',
                form.feeds.includes(name)
                  ? 'bg-blue-900/40 border-blue-600 text-blue-200'
                  : 'border-gray-700 text-gray-400 hover:border-gray-500',
              )}
            >
              {name}
            </button>
          ))}
          {(feedsMeta?.feeds?.length ?? 0) === 0 && (
            <span className="text-xs text-gray-600">No feeds available.</span>
          )}
        </div>
      </div>

      {/* Conditions */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <p className="text-xs text-gray-400">Conditions (all must match · at least one required)</p>
          <button className="text-xs text-blue-400 hover:underline inline-flex items-center gap-1" onClick={addCondition}>
            <Plus className="w-3.5 h-3.5" /> Add condition
          </button>
        </div>
        {form.conditions.length === 0 && (
          <p className="text-xs text-amber-400">Add at least one condition to define what this watcher matches.</p>
        )}
        {form.conditions.map((c, i) => (
          <div key={i} className="flex items-center gap-2">
            <select
              className="input flex-1"
              value={c.field}
              onChange={(e) => updateCondition(i, { field: e.target.value })}
            >
              <option value="">(any field)</option>
              {(fieldsMeta?.fields ?? []).map((f) => (
                <option key={f} value={f}>{f}</option>
              ))}
            </select>
            <select
              className="input w-32"
              value={c.match_type}
              onChange={(e) => updateCondition(i, { match_type: e.target.value as WatcherMatchType })}
            >
              {MATCH_TYPES.map((m) => (
                <option key={m} value={m}>{MATCH_TYPE_LABELS[m]}</option>
              ))}
            </select>
            <input
              className="input flex-1"
              placeholder={c.match_type === 'gte' || c.match_type === 'lte' ? 'number' : 'value'}
              value={c.value}
              onChange={(e) => updateCondition(i, { value: e.target.value })}
            />
            <button className="text-gray-500 hover:text-red-400" onClick={() => removeCondition(i)}>
              <Trash2 className="w-4 h-4" />
            </button>
          </div>
        ))}
      </div>

      <label className="flex items-center gap-2">
        <Toggle checked={form.enabled} onChange={(v) => set('enabled', v)} />
        <span className="text-xs text-gray-400">Enabled</span>
      </label>

      {nameTaken && <p className="text-xs text-red-400">A watcher with this name already exists.</p>}
      {!hasCondition && <p className="text-xs text-red-400">At least one condition is required.</p>}
      {!numericConditionsValid && (
        <p className="text-xs text-red-400">Numeric (≥/≤) conditions require a numeric value.</p>
      )}
      {!validInterval && <p className="text-xs text-red-400">Interval must be at least {INTERVAL_MIN} seconds.</p>}
      {!validMax && (
        <p className="text-xs text-red-400">
          Max feed events must be between {MAX_FEED_MIN} and {MAX_FEED_MAX.toLocaleString()}.
        </p>
      )}
      {error && <p className="text-xs text-red-400">{error}</p>}

      <div className="flex items-center gap-2">
        <button
          className="btn-primary text-xs"
          disabled={!canSave || save.isPending}
          onClick={() => {
            setError(null)
            save.mutate(form)
          }}
        >
          {save.isPending ? 'Saving…' : editing ? 'Save changes' : 'Create watcher'}
        </button>
        <button className="btn-secondary text-xs" onClick={onClose}>
          Cancel
        </button>
      </div>
    </div>
  )
}

// ── Details tab ──────────────────────────────────────────────────────────────

const PAGE_SIZE = 50

function DetailsTab() {
  const { data: watchers } = useWatchers()
  const [selected, setSelected] = useState<string>('')
  const [page, setPage] = useState(0)

  useEffect(() => {
    if (!selected && watchers && watchers.length > 0) setSelected(watchers[0].id)
  }, [watchers, selected])

  const { data, isLoading } = useQuery({
    queryKey: ['watcher-events', selected, page],
    queryFn: () => api.watchers.events(selected, { limit: PAGE_SIZE, offset: page * PAGE_SIZE }),
    enabled: !!selected,
  })

  if (!watchers || watchers.length === 0)
    return <p className="text-sm text-gray-500">No watchers yet.</p>

  const total = data?.total ?? 0
  const maxPage = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1)

  return (
    <div className="space-y-4">
      <label className="space-y-1 inline-block">
        <span className="text-xs text-gray-400">Watcher</span>
        <select
          className="input w-64 block"
          value={selected}
          onChange={(e) => {
            setSelected(e.target.value)
            setPage(0)
          }}
        >
          {watchers.map((w) => (
            <option key={w.id} value={w.id}>{w.name}</option>
          ))}
        </select>
      </label>

      {isLoading ? (
        <p className="text-sm text-gray-500">Loading…</p>
      ) : !data || data.events.length === 0 ? (
        <p className="text-sm text-gray-500">No triggered events.</p>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="min-w-full text-xs">
              <thead>
                <tr className="text-left text-gray-500 border-b border-gray-800">
                  <th className="py-2 pr-4">Triggered</th>
                  <th className="py-2 pr-4">Dataset</th>
                  <th className="py-2 pr-4">Source</th>
                  <th className="py-2 pr-4">Event</th>
                </tr>
              </thead>
              <tbody>
                {data.events.map((ev) => (
                  <tr key={ev.id} className="border-b border-gray-800/60 align-top">
                    <td className="py-2 pr-4 whitespace-nowrap text-gray-400">{ev.triggered_at}</td>
                    <td className="py-2 pr-4 text-gray-400">{ev.dataset}</td>
                    <td className="py-2 pr-4 text-gray-400">{ev.source_name ?? '—'}</td>
                    <td className="py-2 pr-4">
                      <pre className="font-mono text-[11px] text-gray-300 whitespace-pre-wrap break-all max-w-2xl">
                        {JSON.stringify(ev.event, null, 0)}
                      </pre>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="flex items-center gap-3 text-xs text-gray-400">
            <button
              className="btn-secondary text-xs"
              disabled={page <= 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
            >
              Prev
            </button>
            <span>
              Page {page + 1} of {maxPage + 1} · {total} total
            </span>
            <button
              className="btn-secondary text-xs"
              disabled={page >= maxPage}
              onClick={() => setPage((p) => Math.min(maxPage, p + 1))}
            >
              Next
            </button>
          </div>
        </>
      )}
    </div>
  )
}
