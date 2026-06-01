/**
 * Reusable source list editor for API pull, RSS pull, and Remote JSON pull sources.
 * When sourceType is provided, each row can expand to show per-source field config.
 */
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { IngestResponse, JobHandle, PreviewResponse, SourceDef } from '../api/client'
import { useSourceRefresh, useRefreshingSources, refreshId, type RefreshKind } from '../hooks/useExternalRefresh'
import { Plus, Trash2, Pencil, Check, X, RefreshCw, ChevronDown, ChevronUp } from 'lucide-react'
import Toggle from './Toggle'
import SourceFieldsPanel from './SourceFieldsPanel'
import SourcePreviewModal from './SourcePreviewModal'
import { clsx } from 'clsx'

interface Props {
  sources: SourceDef[]
  queryKey: string
  onAdd: (s: SourceDef) => Promise<SourceDef>
  onUpdate: (name: string, s: SourceDef) => Promise<SourceDef>
  onDelete: (name: string) => Promise<unknown>
  onRefresh?: (name: string) => Promise<IngestResponse>
  /** When provided, Add Source first fetches a preview; user confirms before persistence. */
  onPreview?: (s: SourceDef) => Promise<PreviewResponse>
  onConfirmPreview?: (previewId: string) => Promise<IngestResponse | JobHandle>
  onCancelPreview?: (previewId: string) => Promise<unknown>
  /** When provided, each source row shows an expandable per-source field panel. */
  sourceType?: string
}

const EMPTY_SOURCE: SourceDef = { name: '', url: '', enabled: true, interval_minutes: 15 }

/** Auto-clearing per-source refresh result badge */
function RefreshBadge({ result }: { result: IngestResponse }) {
  return (
    <span className="text-xs text-green-400 shrink-0 font-mono">
      +{result.inserted}
      {result.skipped > 0 && <span className="text-gray-500"> / {result.skipped} skip</span>}
    </span>
  )
}

export default function SourceList({
  sources, queryKey, onAdd, onUpdate, onDelete, onRefresh,
  onPreview, onConfirmPreview, onCancelPreview, sourceType,
}: Props) {
  const qc = useQueryClient()
  const invalidate = () => qc.invalidateQueries({ queryKey: [queryKey] })

  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState<SourceDef>(EMPTY_SOURCE)
  const [editingName, setEditingName] = useState<string | null>(null)
  const [editDraft, setEditDraft] = useState<SourceDef>(EMPTY_SOURCE)
  const [expandedFields, setExpandedFields] = useState<Set<string>>(new Set())
  // Per-source refresh results, auto-cleared after 5 s
  const [refreshResults, setRefreshResults] = useState<Record<string, IngestResponse>>({})
  const sourceRefresh = useSourceRefresh()
  const refreshingSources = useRefreshingSources()
  // The refresh kind matches the sourceType for refreshable lists (api-pull / rss-pull).
  const refreshKind = sourceType as RefreshKind | undefined
  const isRefreshing = (name: string) =>
    refreshKind != null && refreshingSources.has(refreshId(refreshKind, name))

  // Preview/confirm state (only used when onPreview is provided)
  const [preview, setPreview] = useState<PreviewResponse | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)

  const addMut = useMutation({ mutationFn: onAdd, onSuccess: () => { invalidate(); setAdding(false); setDraft(EMPTY_SOURCE) } })
  const previewMut = useMutation({
    mutationFn: (s: SourceDef) => onPreview!(s),
    onSuccess: (res) => { setPreview(res); setPreviewError(null) },
    onError: (e) => setPreviewError(String(e)),
  })
  const confirmMut = useMutation({
    mutationFn: (id: string) => onConfirmPreview!(id),
    onSuccess: () => {
      invalidate()
      setPreview(null)
      setAdding(false)
      setDraft(EMPTY_SOURCE)
    },
    onError: (e) => setPreviewError(String(e)),
  })

  const handleAddOrPreview = () => {
    if (onPreview) previewMut.mutate(draft)
    else addMut.mutate(draft)
  }

  const handleCancelPreview = () => {
    if (preview && onCancelPreview) {
      onCancelPreview(preview.preview_id).catch(() => { /* best-effort */ })
    }
    setPreview(null)
    setPreviewError(null)
  }
  const updateMut = useMutation({ mutationFn: ({ name, s }: { name: string; s: SourceDef }) => onUpdate(name, s), onSuccess: () => { invalidate(); setEditingName(null) } })
  const deleteMut = useMutation({ mutationFn: onDelete, onSuccess: invalidate })
  const toggleMut = useMutation({
    mutationFn: ({ name, enabled }: { name: string; enabled: boolean }) => {
      const src = sources.find(s => s.name === name)!
      return onUpdate(name, { ...src, enabled })
    },
    onSuccess: invalidate,
  })

  const handleRefresh = (name: string) => {
    if (!onRefresh || refreshKind == null || isRefreshing(name)) return
    sourceRefresh.mutate(
      { kind: refreshKind, name },
      {
        onSuccess: (result) => {
          setRefreshResults(prev => ({ ...prev, [name]: result }))
          setTimeout(() => setRefreshResults(prev => {
            const next = { ...prev }
            delete next[name]
            return next
          }), 5000)
        },
        onError: () => {
          setRefreshResults(prev => ({ ...prev, [name]: { inserted: 0, skipped: 0, errors: ['fetch failed'] } }))
        },
      },
    )
  }

  const toggleFieldsExpand = (name: string) => {
    setExpandedFields(prev => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  return (
    <div className="space-y-3">
      {sources.length === 0 && !adding && (
        <p className="text-xs text-gray-600 px-3 py-2">No sources configured.</p>
      )}

      {sources.map(src => (
        <div key={src.name} className="rounded-lg border border-gray-700 bg-gray-800/50 overflow-hidden">
          {editingName === src.name ? (
            /* Edit mode */
            <div className="p-3 space-y-2">
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="label">Name (read-only)</label>
                  <input className="input opacity-50" value={editDraft.name} disabled />
                </div>
                <div>
                  <label className="label">URL</label>
                  <input className="input" value={editDraft.url} onChange={e => setEditDraft(d => ({ ...d, url: e.target.value }))} />
                </div>
                <div>
                  <label className="label">Interval (minutes)</label>
                  <input className="input" type="number" min={1} value={editDraft.interval_minutes ?? 15}
                    onChange={e => setEditDraft(d => ({ ...d, interval_minutes: Number(e.target.value) }))} />
                </div>
              </div>
              <div className="flex gap-2 justify-end">
                <button className="btn-ghost" onClick={() => setEditingName(null)}><X className="w-3.5 h-3.5" /> Cancel</button>
                <button className="btn-primary" disabled={updateMut.isPending}
                  onClick={() => updateMut.mutate({ name: src.name, s: editDraft })}>
                  <Check className="w-3.5 h-3.5" /> Save
                </button>
              </div>
            </div>
          ) : (
            /* View mode */
            <>
              <div className="flex items-center gap-3 px-3 py-2.5">
                <Toggle
                  checked={src.enabled}
                  onChange={enabled => toggleMut.mutate({ name: src.name, enabled })}
                />
                <div className="flex-1 min-w-0">
                  <p className={clsx('text-sm font-mono font-medium truncate', src.enabled ? 'text-gray-200' : 'text-gray-500')}>
                    {src.name}
                  </p>
                  <p className="text-xs text-gray-500 truncate">{src.url}</p>
                </div>
                <span className="text-xs text-gray-600 shrink-0">{src.interval_minutes ?? 15}m</span>
                {refreshResults[src.name] && <RefreshBadge result={refreshResults[src.name]} />}
                {onRefresh && (
                  <button
                    className="btn-ghost p-1"
                    title="Manual refresh"
                    disabled={isRefreshing(src.name)}
                    onClick={() => handleRefresh(src.name)}
                  >
                    <RefreshCw className={clsx('w-3.5 h-3.5', isRefreshing(src.name) && 'animate-spin')} />
                  </button>
                )}
                <button className="btn-ghost p-1" onClick={() => { setEditingName(src.name); setEditDraft({ ...src }) }}>
                  <Pencil className="w-3.5 h-3.5" />
                </button>
                <button className="btn-ghost p-1 text-red-400 hover:text-red-300" onClick={() => deleteMut.mutate(src.name)}>
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
                {sourceType && (
                  <button
                    className="btn-ghost p-1"
                    title="Field configuration"
                    onClick={() => toggleFieldsExpand(src.name)}
                  >
                    {expandedFields.has(src.name)
                      ? <ChevronUp className="w-3.5 h-3.5" />
                      : <ChevronDown className="w-3.5 h-3.5" />}
                  </button>
                )}
              </div>
              {sourceType && expandedFields.has(src.name) && (
                <div className="border-t border-gray-700 px-3 py-3">
                  <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
                    Field Configuration — {src.name}
                  </p>
                  <SourceFieldsPanel sourceType={sourceType} sourceName={src.name} />
                </div>
              )}
            </>
          )}
        </div>
      ))}

      {/* Add form */}
      {adding ? (
        <div className="rounded-lg border border-brand-700/40 bg-brand-900/10 p-3 space-y-2">
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="label">Name *</label>
              <input className="input font-mono" placeholder="my_source" value={draft.name}
                onChange={e => setDraft(d => ({ ...d, name: e.target.value.replace(/\s+/g, '_').toLowerCase() }))} />
            </div>
            <div>
              <label className="label">URL *</label>
              <input className="input" placeholder="https://..." value={draft.url}
                onChange={e => setDraft(d => ({ ...d, url: e.target.value }))} />
            </div>
            <div>
              <label className="label">Interval (minutes)</label>
              <input className="input" type="number" min={1} value={draft.interval_minutes ?? 15}
                onChange={e => setDraft(d => ({ ...d, interval_minutes: Number(e.target.value) }))} />
            </div>
          </div>
          {addMut.isError && <p className="text-xs text-red-400">{String(addMut.error)}</p>}
          {previewMut.isError && <p className="text-xs text-red-400">{String(previewMut.error)}</p>}
          <div className="flex gap-2 justify-end">
            <button className="btn-ghost" onClick={() => { setAdding(false); setDraft(EMPTY_SOURCE) }}>
              <X className="w-3.5 h-3.5" /> Cancel
            </button>
            <button
              className="btn-primary"
              disabled={!draft.name || !draft.url || addMut.isPending || previewMut.isPending}
              onClick={handleAddOrPreview}
            >
              <Check className="w-3.5 h-3.5" />
              {onPreview ? (previewMut.isPending ? 'Fetching preview...' : 'Preview') : 'Add Source'}
            </button>
          </div>
        </div>
      ) : (
        <button className="btn-secondary w-full justify-center" onClick={() => setAdding(true)}>
          <Plus className="w-3.5 h-3.5" /> Add Source
        </button>
      )}

      {preview && (
        <SourcePreviewModal
          preview={preview}
          confirming={confirmMut.isPending}
          error={previewError}
          onConfirm={() => confirmMut.mutate(preview.preview_id)}
          onCancel={handleCancelPreview}
        />
      )}
    </div>
  )
}
