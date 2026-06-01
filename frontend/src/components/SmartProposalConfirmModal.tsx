/**
 * Confirmation modal for spawning a CONSOLIDATED smart-mode proposal job
 * (prompts-032 Phase C; model selection added prompts-034).
 *
 * The manual flow is now consolidated/global: the operator picks one or more
 * feeds, a model, a sample size, and a field scope, then the backend
 * produces a SINGLE proposal spanning all selected feeds. Nothing is applied
 * until the operator approves the resulting proposal.
 *
 * Controls:
 *   - feeds      multiselect (toggle chips) — at least one required
 *   - model      dropdown of "provider · model" pairs drawn from each
 *                provider's available_models (discovered models; prompts-036 —
 *                a green Test is no longer required). Blank = configured default
 *   - sample size 1..100 (per feed; capped by the backend)
 *   - field scope all | configured (feed-fields.yaml)
 *   - a pre-send summary of the chosen options
 */
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { X, Loader2 } from 'lucide-react'
import { api, type SmartFieldScope, type SmartJobHandle } from '../api/client'

interface Props {
  /** All feed names the operator may choose from. */
  sources: string[]
  /** Optional pre-selected feeds (e.g. a row the operator clicked from). */
  initialSelected?: string[]
  onClose: () => void
  /** prompts-033: receives the started job handle so the parent can render an
   *  optimistic "Processing" row and poll for completion. */
  onCreated: (handle: SmartJobHandle) => void
}

export default function SmartProposalConfirmModal({
  sources,
  initialSelected,
  onClose,
  onCreated,
}: Props) {
  const [selected, setSelected] = useState<string[]>(
    () => (initialSelected ?? []).filter((s) => sources.includes(s)),
  )
  // prompts-034: the operator now picks a concrete "provider · model" pair from
  // the discovered model catalog. Empty string = configured default (provider's
  // default model). The index references `modelOptions` below.
  // prompts-036: source switched from tested_models to available_models — a
  // green Test Connection is no longer required to make a model selectable; a
  // bad model surfaces at proposal request/response time instead.
  const [modelChoice, setModelChoice] = useState('')
  const [sampleSize, setSampleSize] = useState(10)
  const [fieldScope, setFieldScope] = useState<SmartFieldScope>('all')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const { data: providers } = useQuery({
    queryKey: ['llm-providers'],
    queryFn: () => api.llm.listProviders(),
  })

  // prompts-034: flatten every provider's models into a single ordered list of
  // selectable "provider · model" pairs.
  // prompts-036: the source is now each provider's discovered catalog
  // (available_models) rather than tested_models, so a model is selectable as
  // soon as it is discovered — no green Test required. De-duped per
  // (provider, model) so the same model id under two providers stays distinct.
  const modelOptions = useMemo(() => {
    const opts: { provider: string; model: string }[] = []
    const seen = new Set<string>()
    for (const p of providers ?? []) {
      for (const m of p.available_models ?? []) {
        const key = `${p.name}\u0000${m}`
        if (seen.has(key)) continue
        seen.add(key)
        opts.push({ provider: p.name, model: m })
      }
    }
    return opts
  }, [providers])

  const chosen = useMemo(() => {
    if (modelChoice === '') return null
    return modelOptions[Number(modelChoice)] ?? null
  }, [modelChoice, modelOptions])

  function toggle(name: string) {
    setSelected((prev) =>
      prev.includes(name) ? prev.filter((s) => s !== name) : [...prev, name],
    )
  }

  // prompts-033: one-click select-all / clear for the feed picker.
  const allSelected = sources.length > 0 && selected.length === sources.length
  function toggleAll() {
    setSelected(allSelected ? [] : [...sources])
  }

  async function handleSubmit() {
    if (selected.length === 0) return
    setError(null)
    setSubmitting(true)
    try {
      const handle = await api.smartMappings.createJob({
        sources: selected,
        provider: chosen?.provider,
        model: chosen?.model,
        sample_size: sampleSize,
        field_scope: fieldScope,
      })
      onCreated(handle)
      onClose()
    } catch (err) {
      setError((err as Error).message)
      setSubmitting(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70"
      role="dialog"
      aria-modal="true"
      aria-labelledby="smart-modal-title"
    >
      <div className="card w-full max-w-lg mx-4 max-h-[90vh] overflow-y-auto">
        <div className="flex items-start justify-between mb-4">
          <div>
            <h2 id="smart-modal-title" className="text-base font-semibold text-gray-100">
              Generate consolidated mapping proposal
            </h2>
            <p className="text-xs text-gray-500 mt-1">
              The LLM consolidates a sample of raw entries across the selected
              feeds into one global mapping. Nothing is applied until you approve.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-gray-500 hover:text-gray-300"
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="space-y-4">
          {/* Feed multiselect */}
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <label className="block text-xs text-gray-400">
                Feeds ({selected.length} selected)
              </label>
              {sources.length > 0 && (
                <button
                  type="button"
                  onClick={toggleAll}
                  className="text-xs text-brand-400 hover:text-brand-300"
                >
                  {allSelected ? 'Clear all' : 'Select all'}
                </button>
              )}
            </div>
            {sources.length === 0 ? (
              <p className="text-xs text-gray-600 italic">
                No feeds with ingested data are available.
              </p>
            ) : (
              <div className="flex flex-wrap gap-1.5" role="group" aria-label="Feeds">
                {sources.map((name) => {
                  const on = selected.includes(name)
                  return (
                    <button
                      key={name}
                      type="button"
                      aria-pressed={on}
                      onClick={() => toggle(name)}
                      className={
                        'text-xs font-mono px-2 py-1 rounded border transition-colors ' +
                        (on
                          ? 'bg-brand-900/60 text-brand-200 border-brand-700'
                          : 'bg-gray-800 text-gray-400 border-gray-700 hover:border-gray-600')
                      }
                    >
                      {name}
                    </button>
                  )
                })}
              </div>
            )}
          </div>

          {/* Model dropdown (prompts-034; prompts-036): discovered models are
              selectable — no green Test Connection required. */}
          <div>
            <label htmlFor="smart-model" className="block text-xs text-gray-400 mb-1">
              Model
            </label>
            <select
              id="smart-model"
              value={modelChoice}
              onChange={(e) => setModelChoice(e.target.value)}
              className="input w-full"
            >
              <option value="">Configured default</option>
              {modelOptions.map((o, i) => (
                <option key={`${o.provider}\u0000${o.model}`} value={String(i)}>
                  {o.provider} · {o.model}
                </option>
              ))}
            </select>
            {modelOptions.length === 0 && (
              <p className="text-xs text-gray-600 italic mt-1">
                No discovered models yet. Add a provider, or run Discover Models
                on a provider in Settings, to make its models selectable here.
                The configured default will be used.
              </p>
            )}
          </div>

          {/* Sample size */}
          <div>
            <label htmlFor="smart-sample" className="block text-xs text-gray-400 mb-1">
              Sample size per feed (1–100)
            </label>
            <input
              id="smart-sample"
              type="number"
              min={1}
              max={100}
              value={sampleSize}
              onChange={(e) => setSampleSize(Math.max(1, Math.min(100, Number(e.target.value) || 1)))}
              className="input w-full"
            />
          </div>

          {/* Field scope radio */}
          <div>
            <span className="block text-xs text-gray-400 mb-1.5">Field scope</span>
            <div className="flex flex-col gap-1.5">
              <label className="flex items-start gap-2 text-xs text-gray-300 cursor-pointer">
                <input
                  type="radio"
                  name="field-scope"
                  value="all"
                  checked={fieldScope === 'all'}
                  onChange={() => setFieldScope('all')}
                  className="mt-0.5"
                />
                <span>
                  <span className="text-gray-200">All discovered fields</span>
                  <span className="text-gray-600"> — every raw field seen in the sample</span>
                </span>
              </label>
              <label className="flex items-start gap-2 text-xs text-gray-300 cursor-pointer">
                <input
                  type="radio"
                  name="field-scope"
                  value="configured"
                  checked={fieldScope === 'configured'}
                  onChange={() => setFieldScope('configured')}
                  className="mt-0.5"
                />
                <span>
                  <span className="text-gray-200">Only configured fields</span>
                  <span className="text-gray-600"> — fields enabled in feed-fields.yaml</span>
                </span>
              </label>
            </div>
          </div>

          {/* Pre-send summary */}
          <div className="text-xs bg-gray-950/60 border border-gray-800 rounded px-3 py-2 space-y-1">
            <div className="text-gray-400 font-medium mb-1">Summary</div>
            <div>
              <span className="text-gray-500">Feeds: </span>
              <span className="text-gray-200 font-mono">
                {selected.length > 0 ? selected.join(', ') : '— none selected —'}
              </span>
            </div>
            <div>
              <span className="text-gray-500">Model: </span>
              {chosen ? (
                <span className="text-gray-200">
                  {chosen.provider} <span className="text-gray-500">/</span>{' '}
                  <span className="font-mono">{chosen.model}</span>
                </span>
              ) : (
                <span className="text-gray-200">configured default</span>
              )}
            </div>
            <div>
              <span className="text-gray-500">Sample size: </span>
              <span className="text-gray-200">{sampleSize} per feed</span>
            </div>
            <div>
              <span className="text-gray-500">Scope: </span>
              <span className="text-gray-200">
                {fieldScope === 'all' ? 'all discovered fields' : 'configured fields only'}
              </span>
            </div>
          </div>

          {error && (
            <div className="text-xs text-red-400 bg-red-900/30 border border-red-800/50 rounded px-2 py-1.5">
              {error}
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 mt-5">
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="btn-secondary text-sm"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={submitting || selected.length === 0}
            className="btn-primary text-sm flex items-center gap-1.5"
          >
            {submitting && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
            {submitting ? 'Submitting…' : 'Generate proposal'}
          </button>
        </div>
      </div>
    </div>
  )
}
