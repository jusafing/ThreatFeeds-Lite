/**
 * LLM Providers configuration tab (prompts-021D-2; rebuilt in 022 step 7;
 * Discover-then-Probe staging added in 027 step 5).
 *
 * Backend contract:
 *   GET    /api/llm/config                — redacted config (api_key="***" if set)
 *   PUT    /api/llm/config                — UPDATES ONLY {enabled, default_provider}
 *                                           (022 step 4 narrowed this; per-provider
 *                                           CRUD lives on the routes below)
 *   GET    /api/llm/providers             — redacted listing
 *   POST   /api/llm/providers             — add a provider (used by AddProviderWizard)
 *   PUT    /api/llm/providers/{n}         — edit one provider (per-card Save)
 *   DELETE /api/llm/providers/{n}         — remove one provider (per-card Delete)
 *   POST   /api/llm/providers/test        — ephemeral Test (wizard + per-card probe)
 *   POST   /api/llm/providers/discover    — discover-only (wizard stage 1)
 *   POST   /api/llm/providers/{n}/discover — discover-only (per-card Discover Models)
 *   POST   /api/llm/providers/{n}/test    — legacy persisted Test (kept for callers
 *                                            outside this surface)
 *
 * Per-card layout (027 step 5): the card now mirrors the wizard's
 * Discover -> Probe -> Save staging:
 *   - "Default model to use" dropdown is populated from
 *     ``draft.available_models`` on first paint (no click required).
 *     The legacy post-test-only dropdown is gone.
 *   - "Discover Models" button calls POST /providers/{name}/discover
 *     and on success PUTs the new ``available_models`` list back to
 *     persist it.
 *   - "Test connection" probes the currently-selected dropdown value
 *     via POST /providers/test (the draft endpoint) with
 *     ``api_key: "***"`` — the backend's merge-stored-key branch
 *     (027 step 1) inserts the on-disk key for us.
 *   - Save is gated on a probe-since-last-edit verdict:
 *     the card hashes (model, base_url, api_key_draft, timeout,
 *     retries, skip_tls) at the moment of a successful probe and only
 *     enables Save while the current draft hash matches. Any field
 *     edit invalidates and forces a fresh probe.
 *
 * Security:
 *   - api_key fields render as <input type="password"> and are draft-only:
 *     the draft state is initialised to "" (never the redacted "***")
 *     and only included in the PUT payload when the operator actually
 *     typed something. When left empty, the PUT/probe sends "***" so
 *     the backend preserves the existing value.
 */
import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { clsx } from 'clsx'
import {
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Eye,
  Plus,
  Save,
  Search,
  Trash2,
  XCircle,
  Zap,
} from 'lucide-react'

import {
  api,
  LLMConfigUpdate,
  LLMProvider,
  LLMProviderConfig,
  LLMProviderKind,
  LLMTestRunResult,
  synthesizeErrorTestResult,
} from '../../api/client'
import Toggle from '../../components/Toggle'
import AddProviderWizard from '../../components/AddProviderWizard'
import TestDetailsModal from '../../components/TestDetailsModal'

const KINDS: { id: LLMProviderKind; label: string }[] = [
  { id: 'openai',             label: 'OpenAI' },
  { id: 'anthropic',          label: 'Anthropic' },
  { id: 'ollama',             label: 'Ollama (local)' },
  { id: 'openai_compatible',  label: 'OpenAI-compatible' },
]

const REDACTED = '***'

/** Local edit draft for an existing provider — api_key is held separately. */
interface ProviderDraft extends LLMProvider {
  /** Raw new key the operator typed. Empty = no change. Never echoed back from GET. */
  _api_key_draft: string
}

function fromServer(p: LLMProvider): ProviderDraft {
  return { ...p, _api_key_draft: '' }
}

/**
 * Build the PUT /api/llm/providers/{name} payload — re-injects "***"
 * when no new key was typed so the backend retains the stored value.
 * The path 'name' is authoritative; the field is still sent for
 * consistency but the backend overrides it.
 */
function toPersistPayload(d: ProviderDraft): LLMProviderConfig {
  const { _api_key_draft, ...rest } = d
  const api_key = _api_key_draft.length > 0 ? _api_key_draft : REDACTED
  return { ...rest, api_key }
}

/**
 * Build the POST /api/llm/providers/test payload — same as
 * toPersistPayload but explicitly omits ``available_models`` (the
 * probe doesn't need it and it's not part of the runtime LLMClient
 * constructor surface). The backend's 027-merge-stored-key branch
 * uses ``name`` + ``api_key === "***"`` to inject the stored key.
 */
function toProbePayload(d: ProviderDraft): LLMProviderConfig {
  const out = toPersistPayload(d)
  const { available_models: _unused, ...rest } = out
  void _unused
  return rest
}

/** Identity hash of the fields that affect probe validity. */
function probeHash(d: ProviderDraft): string {
  return JSON.stringify([
    d.kind,
    (d.base_url ?? '').trim(),
    d._api_key_draft, // empty draft means "stored key" — stable identifier
    (d.model ?? '').trim(),
    d.timeout_seconds ?? 30,
    d.max_retries ?? 2,
    !!d.skip_tls_verify,
  ])
}

export default function LLMProvidersTab() {
  const qc = useQueryClient()
  const cfgQuery = useQuery({ queryKey: ['llm-config'], queryFn: api.llm.getConfig })

  const [enabled, setEnabled] = useState(false)
  const [defaultProvider, setDefaultProvider] = useState<string>('')
  const [serverDrafts, setServerDrafts] = useState<ProviderDraft[]>([])
  const [showAddWizard, setShowAddWizard] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  // Hydrate local state from server.
  useEffect(() => {
    if (!cfgQuery.data) return
    setEnabled(cfgQuery.data.enabled)
    setDefaultProvider(cfgQuery.data.default_provider ?? '')
    setServerDrafts(cfgQuery.data.providers.map(fromServer))
  }, [cfgQuery.data])

  const saveMutation = useMutation({
    mutationFn: (payload: LLMConfigUpdate) => api.llm.setConfig(payload),
    onSuccess: () => {
      setSaveError(null)
      qc.invalidateQueries({ queryKey: ['llm-config'] })
    },
    onError: (e: unknown) => setSaveError(e instanceof Error ? e.message : String(e)),
  })

  const onSaveTopLevel = () => {
    saveMutation.mutate({
      enabled,
      default_provider: defaultProvider || null,
    })
  }

  const providerNames = useMemo(
    () => serverDrafts.map(d => d.name).filter(Boolean),
    [serverDrafts],
  )

  if (cfgQuery.isLoading) return <div className="text-sm text-gray-400">Loading LLM config…</div>
  if (cfgQuery.error) return <div className="text-sm text-red-400">Failed to load LLM config.</div>

  return (
    <div className="space-y-5">
      {/* Top-level: enabled + default */}
      <div className="card space-y-4">
        <div>
          <h3 className="text-sm font-semibold text-gray-200">LLM Providers</h3>
          <p className="text-xs text-gray-500 mt-1">
            Configure providers used by Smart Mappings. Disabled by default.
          </p>
        </div>

        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm text-gray-300">Enable LLM</p>
            <p className="text-xs text-gray-500">
              When off, Smart Mappings cannot reach any provider and proposal jobs will fail fast.
            </p>
          </div>
          <Toggle checked={enabled} onChange={setEnabled} />
        </div>

        <div>
          <label className="label">Default provider</label>
          <select
            className="input w-64"
            value={defaultProvider}
            onChange={e => setDefaultProvider(e.target.value)}
            disabled={!enabled}
          >
            <option value="">(none)</option>
            {providerNames.map(n => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
          {enabled && !defaultProvider && (
            <p className="text-xs text-amber-400 mt-1">
              A default provider is required when LLM is enabled.
            </p>
          )}
        </div>

        <div className="border-t border-gray-800 pt-3 flex items-center gap-3">
          <button
            className="btn-primary flex items-center gap-1.5"
            onClick={onSaveTopLevel}
            disabled={saveMutation.isPending}
          >
            <Save className="w-3.5 h-3.5" />
            {saveMutation.isPending ? 'Saving…' : 'Save'}
          </button>
          {saveMutation.isSuccess && !saveError && (
            <span className="text-xs text-green-400">Saved.</span>
          )}
          {saveError && <span className="text-xs text-red-400">{saveError}</span>}
        </div>
      </div>

      {/* Existing providers — each card persists itself via the per-provider CRUD routes */}
      {serverDrafts.map(d => (
        <ProviderCard
          key={d.name}
          initial={d}
          isLastProvider={serverDrafts.length === 1}
          llmEnabled={enabled}
          onPersisted={() => qc.invalidateQueries({ queryKey: ['llm-config'] })}
        />
      ))}

      {/* Add new provider — opens the wizard modal (022 step 6) */}
      <button
        className="btn-secondary flex items-center gap-1.5"
        onClick={() => setShowAddWizard(true)}
      >
        <Plus className="w-3.5 h-3.5" />
        Add LLM
      </button>

      {showAddWizard && (
        <AddProviderWizard
          existingNames={providerNames}
          onClose={() => setShowAddWizard(false)}
          onAdded={() => qc.invalidateQueries({ queryKey: ['llm-config'] })}
        />
      )}
    </div>
  )
}

// ── Provider card (per-card Discover / Test / Save / Delete — 027 step 5) ───

function ProviderCard({
  initial,
  isLastProvider,
  llmEnabled,
  onPersisted,
}: {
  initial: ProviderDraft
  /** True when this is the only configured provider. */
  isLastProvider: boolean
  /** Whether the top-level LLM toggle is currently enabled. */
  llmEnabled: boolean
  /** Called after a successful PUT or DELETE so the parent can refetch. */
  onPersisted: () => void
}) {
  // Card-local draft so Save / Reset semantics work without disturbing
  // sibling cards.
  const [draft, setDraft] = useState<ProviderDraft>(initial)
  useEffect(() => { setDraft(initial) }, [initial])

  const [probeResult, setProbeResult] = useState<LLMTestRunResult | null>(null)
  const [probeError, setProbeError] = useState<string | null>(null)
  const [probing, setProbing] = useState(false)
  const [showDetails, setShowDetails] = useState(false)
  /** prompts-038: provider cards start collapsed to a one-line summary;
   *  the operator expands a card to edit it. */
  const [expanded, setExpanded] = useState(false)
  /** Hash captured at the moment of the last green probe. Save stays
   *  enabled only while currentHash === lastProbedHash. */
  const [lastProbedHash, setLastProbedHash] = useState<string | null>(null)

  const [discoverError, setDiscoverError] = useState<string | null>(null)
  const [discovering, setDiscovering] = useState(false)
  /** Set to a short success/error string after Discover Models writes. */
  const [discoverNote, setDiscoverNote] = useState<string | null>(null)

  const [savedAt, setSavedAt] = useState<number | null>(null)

  // prompts-033 (decision A): Save is the single orchestrator. It ran model
  // discovery ONLY when the base URL changed, then always ran a connection
  // test, and persisted ONLY if the test passed.
  // prompts-036: the mandatory blocking connection test is REMOVED — once
  // models are discovered/populated, Save persists directly. A bad model is
  // caught at proposal request/response time, not pre-flight. Discovery still
  // runs only when the base URL changed; a changed URL that yields no models
  // remains a hard block (the provider would be unusable). The "Test
  // connection" and "Discover Models" buttons stay as OPTIONAL manual tools.
  const [saving, setSaving] = useState(false)
  const [saveWarning, setSaveWarning] = useState<string | null>(null)

  /** prompts-031: in-card delete confirmation (replaces window.confirm). */
  const [confirmDelete, setConfirmDelete] = useState(false)

  const currentHash = useMemo(() => probeHash(draft), [draft])

  /** Any field edit invalidates the last probe verdict so the OK pill clears. */
  const onChange = (patch: Partial<ProviderDraft>) => {
    setDraft(prev => ({ ...prev, ...patch }))
    setLastProbedHash(null)
    setProbeResult(null)
    setProbeError(null)
    setSavedAt(null)
    setSaveWarning(null)
  }

  /**
   * prompts-033 (decision A) → amended by prompts-036: Save orchestrates
   * discover → persist (the mandatory blocking connection test is gone).
   *   1. base_url changed  → discover models (draft endpoint) first; a
   *      discovery failure on a changed URL aborts WITHOUT persisting.
   *   2. always            → persist via updateProvider.
   * A bad model is no longer rejected here; it surfaces at proposal
   * request/response time. The "Test connection" button remains available for
   * an optional manual sanity check but never gates Save.
   */
  const runSave = async () => {
    setSaveWarning(null)
    setSavedAt(null)
    setSaving(true)
    try {
      let working = draft
      const urlChanged = (draft.base_url ?? '') !== (initial.base_url ?? '')

      // 1. Discovery — only when the base URL changed.
      if (urlChanged) {
        const d = await api.llm.discoverDraft({ ...toProbePayload(draft), name: initial.name })
        if (d.status !== 'ok' || !d.models || d.models.length === 0) {
          const detailErr = (d.details ?? []).map(x => x.error).filter(Boolean).pop()
          setSaveWarning(
            `Discovery failed: ${detailErr || 'No models discovered.'} Provider was not saved.`,
          )
          return
        }
        working = { ...draft, available_models: d.models }
        setDraft(working)
      }

      // 2. Persist — no blocking connection test (prompts-036).
      await api.llm.updateProvider(initial.name, toPersistPayload(working))
      setSavedAt(Date.now())
      onPersisted()
    } catch (e) {
      setSaveWarning(
        `Save failed: ${e instanceof Error ? e.message : String(e)}. Provider was not saved.`,
      )
    } finally {
      setSaving(false)
    }
  }

  const deleteMutation = useMutation({
    mutationFn: () => api.llm.deleteProvider(initial.name),
    onSuccess: () => onPersisted(),
  })

  /** Discover Models: refresh ``available_models`` and persist via PUT. */
  const runDiscover = async () => {
    setDiscovering(true)
    setDiscoverError(null)
    setDiscoverNote(null)
    try {
      const r = await api.llm.discoverProvider(initial.name)
      if (r.status !== 'ok' || !r.models || r.models.length === 0) {
        // Surface the canonical 023 verdict or transport message.
        const detailErr = (r.details ?? [])
          .map(d => d.error)
          .filter(Boolean)
          .pop()
        setDiscoverError(detailErr || 'No models discovered.')
        return
      }
      // Persist the freshly-discovered list. Use the draft so the
      // operator's unsaved changes ride along, with stored-key merge
      // via "***" in the PUT body.
      const nextDraft: ProviderDraft = { ...draft, available_models: r.models }
      setDraft(nextDraft)
      await api.llm.updateProvider(initial.name, toPersistPayload(nextDraft))
      onPersisted()
      setDiscoverNote(`Discovered ${r.models.length} models.`)
    } catch (e) {
      setDiscoverError(e instanceof Error ? e.message : String(e))
    } finally {
      setDiscovering(false)
    }
  }

  /** Probe the currently-selected model via the draft endpoint. */
  const runProbe = async () => {
    setProbing(true)
    setProbeResult(null)
    setProbeError(null)
    const hashAtProbe = currentHash
    try {
      // Force the persisted name so the backend merge-stored-key
      // branch (027 step 1) can match this draft to its on-disk row.
      const r = await api.llm.testProviderDraft({
        ...toProbePayload(draft),
        name: initial.name,
      })
      setProbeResult(r)
      if (r.status === 'ok') {
        setLastProbedHash(hashAtProbe)
      }
    } catch (e) {
      setProbeError(e instanceof Error ? e.message : String(e))
    } finally {
      setProbing(false)
    }
  }

  const onDelete = () => setConfirmDelete(true)

  const availableModels: string[] = draft.available_models ?? []
  const probeOk = probeResult?.status === 'ok' && lastProbedHash === currentHash
  // prompts-033 (decision A): Save is the orchestrator. It is enabled whenever a
  // model is selected and no save is in flight — no prior green probe required.
  const canSave = !!(draft.model ?? '').trim() && !saving

  const probeInlineError = useMemo(() => {
    if (probeError) return probeError
    if (probeResult && probeResult.status === 'error') {
      const details = probeResult.details ?? []
      for (let i = details.length - 1; i >= 0; i--) {
        if (details[i].error) return details[i].error
      }
      return 'Probe failed.'
    }
    return null
  }, [probeError, probeResult])

  // issue_local_02: backing result for "View test details". Prefer the probe
  // transcript; otherwise synthesise a single-step result from a thrown
  // Test/Discover error string so the link + modal still open when the call
  // failed before any structured payload arrived.
  const detailsResult = useMemo<LLMTestRunResult | null>(() => {
    if (probeResult) return probeResult
    if (probeError) return synthesizeErrorTestResult(probeError, 'complete')
    if (discoverError) return synthesizeErrorTestResult(discoverError, 'list_models')
    return null
  }, [probeResult, probeError, discoverError])

  return (
    <>
      <div className="card space-y-3" data-testid={`provider-card-${initial.name}`}>
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 min-w-0">
            <button
              type="button"
              className="btn-ghost p-1 shrink-0"
              aria-label={expanded ? `Collapse ${initial.name}` : `Expand ${initial.name}`}
              aria-expanded={expanded}
              onClick={() => setExpanded(v => !v)}
            >
              {expanded ? (
                <ChevronUp className="w-4 h-4 text-gray-400" />
              ) : (
                <ChevronDown className="w-4 h-4 text-gray-400" />
              )}
            </button>
            <h4 className="text-sm font-semibold text-gray-200 shrink-0">{initial.name}</h4>
            {!expanded && (
              <span className="text-xs text-gray-500 truncate">
                <span className="text-gray-400">{initial.kind}</span>
                {(initial.base_url ?? '').trim() && (
                  <> · {initial.base_url}</>
                )}
              </span>
            )}
          </div>
          <button
            className="text-xs text-red-400 hover:text-red-300 flex items-center gap-1 disabled:opacity-50 shrink-0"
            onClick={onDelete}
            disabled={deleteMutation.isPending}
            aria-label={`Delete ${initial.name}`}
          >
            <Trash2 className="w-3.5 h-3.5" />
            {deleteMutation.isPending ? 'Deleting…' : 'Delete'}
          </button>
        </div>

        {expanded && (
          <>
            <ProviderFields
              draft={draft}
              onChange={onChange}
              availableModels={availableModels}
              disableName
            />

            <div className="border-t border-gray-800 pt-3 flex flex-wrap items-center gap-3">
          <button
            className="btn-secondary flex items-center gap-1.5"
            onClick={runDiscover}
            disabled={discovering}
          >
            <Search className={clsx('w-3.5 h-3.5', discovering && 'animate-pulse')} />
            {discovering ? 'Discovering…' : 'Discover Models'}
          </button>

          <button
            className="btn-secondary flex items-center gap-1.5"
            onClick={runProbe}
            disabled={!(draft.model ?? '').trim() || probing}
            title={
              !(draft.model ?? '').trim()
                ? 'Pick a model from "Default model to use" first.'
                : undefined
            }
          >
            <Zap className={clsx('w-3.5 h-3.5', probing && 'animate-pulse')} />
            {probing ? 'Testing…' : 'Test connection'}
          </button>

          <button
            className="btn-primary flex items-center gap-1.5"
            onClick={runSave}
            disabled={!canSave}
            title={
              !(draft.model ?? '').trim()
                ? 'Pick a model from "Default model to use" first.'
                : undefined
            }
          >
            <Save className="w-3.5 h-3.5" />
            {saving ? 'Saving…' : 'Save'}
          </button>

          {probeOk && (
            <span
              className="flex items-center gap-1.5 text-xs text-green-400"
              data-testid="card-probe-ok"
            >
              <CheckCircle2 className="w-3.5 h-3.5" />
              Model OK
              {probeResult?.sample && (
                <span className="text-gray-400"> — sample: {probeResult.sample}</span>
              )}
            </span>
          )}

          {probeInlineError && (
            <span
              className="flex items-center gap-1.5 text-xs text-red-400"
              data-testid="card-probe-error"
            >
              <XCircle className="w-3.5 h-3.5 shrink-0" />
              <span className="break-all" role="alert">{probeInlineError}</span>
            </span>
          )}

          {detailsResult && (
            <button
              type="button"
              className="text-blue-400 hover:underline flex items-center gap-1 ml-1 text-xs"
              onClick={() => setShowDetails(true)}
            >
              <Eye className="w-3 h-3" />
              View test details
            </button>
          )}

          {discoverNote && (
            <span className="text-xs text-green-400">{discoverNote}</span>
          )}
          {discoverError && (
            <span className="text-xs text-red-400 break-all" role="alert">
              {discoverError}
            </span>
          )}

          {savedAt && !saving && !saveWarning && (
            <span className="text-xs text-green-400">Saved.</span>
          )}
        </div>

        <p className="text-xs text-gray-500 italic">
          Saving runs discovery when the base URL changed, then persists. A
          model problem is reported when you generate a proposal — Test
          connection is an optional manual check.
        </p>
          </>
        )}

        {confirmDelete && (
          <div
            className="border border-red-500/40 bg-red-500/5 rounded p-3 space-y-2"
            data-testid={`delete-confirm-${initial.name}`}
            role="alertdialog"
            aria-label={`Confirm delete ${initial.name}`}
          >
            <p className="text-xs text-gray-200">
              Delete provider "{initial.name}"? This persists immediately.
            </p>
            {isLastProvider && llmEnabled && (
              <p className="text-xs text-amber-400">
                This is the last provider — deleting it will also disable LLM.
              </p>
            )}
            <div className="flex items-center gap-2">
              <button
                className="btn-danger flex items-center gap-1.5"
                onClick={() => {
                  setConfirmDelete(false)
                  deleteMutation.mutate()
                }}
                disabled={deleteMutation.isPending}
                data-testid={`delete-confirm-yes-${initial.name}`}
              >
                <Trash2 className="w-3.5 h-3.5" />
                {deleteMutation.isPending ? 'Deleting…' : 'Confirm delete'}
              </button>
              <button
                className="btn-secondary"
                onClick={() => setConfirmDelete(false)}
                disabled={deleteMutation.isPending}
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        {saveWarning && (
          <p
            className="text-xs text-red-400 break-all"
            role="alert"
            data-testid={`card-save-warning-${initial.name}`}
          >
            {saveWarning}
          </p>
        )}
        {deleteMutation.isError && (
          <p className="text-xs text-red-400 break-all" role="alert">
            {String((deleteMutation.error as Error).message)}
          </p>
        )}
      </div>

      {showDetails && detailsResult && (
        <TestDetailsModal
          result={detailsResult}
          providerLabel={initial.name}
          onClose={() => setShowDetails(false)}
        />
      )}
    </>
  )
}

// ── Form fields (shared edit) ────────────────────────────────────────────

function ProviderFields({
  draft,
  onChange,
  availableModels,
  disableName = false,
}: {
  draft: ProviderDraft
  onChange: (patch: Partial<ProviderDraft>) => void
  /** Persisted list of models, used to render the "Default model to use"
   *  dropdown. Empty array means the operator has not run Discover yet. */
  availableModels: string[]
  disableName?: boolean
}) {
  return (
    <div className="grid grid-cols-2 gap-3">
      <div>
        <label className="label">Name</label>
        <input
          className="input"
          value={draft.name}
          disabled={disableName}
          onChange={e => onChange({ name: e.target.value })}
        />
      </div>
      <div>
        <label className="label">Kind</label>
        <select
          className="input"
          value={draft.kind}
          onChange={e => onChange({ kind: e.target.value as LLMProviderKind })}
        >
          {KINDS.map(k => <option key={k.id} value={k.id}>{k.label}</option>)}
        </select>
      </div>

      <div className="col-span-2">
        <label className="label">Base URL</label>
        <input
          className="input"
          placeholder="(default for kind)"
          value={draft.base_url ?? ''}
          onChange={e => onChange({ base_url: e.target.value })}
        />
      </div>

      <div className="col-span-2">
        <label className="label" htmlFor={`apikey-${draft.name || 'new'}`}>
          API key
          {draft.api_key === REDACTED && (
            <span className="ml-2 text-xs text-gray-500">(stored — leave blank to keep)</span>
          )}
        </label>
        <input
          id={`apikey-${draft.name || 'new'}`}
          className="input"
          type="password"
          autoComplete="new-password"
          placeholder={draft.api_key === REDACTED ? '••••••••' : ''}
          value={draft._api_key_draft}
          onChange={e => onChange({ _api_key_draft: e.target.value })}
        />
      </div>

      <div className="col-span-2">
        <label className="label" htmlFor={`model-${draft.name || 'new'}`}>
          Default model to use
        </label>
        {availableModels.length > 0 || draft.kind === 'anthropic' ? (
          draft.kind === 'anthropic' ? (
            <input
              id={`model-${draft.name || 'new'}`}
              className="input"
              value={draft.model ?? ''}
              onChange={e => onChange({ model: e.target.value })}
              placeholder="claude-3-5-sonnet-20241022"
            />
          ) : (
            <select
              id={`model-${draft.name || 'new'}`}
              className="input"
              value={draft.model ?? ''}
              onChange={e => onChange({ model: e.target.value })}
            >
              <option value="">(select a model)</option>
              {availableModels.map(m => (
                <option key={m} value={m}>{m}</option>
              ))}
              {/* If the stored model isn't in available_models (e.g.
                  imported from legacy YAML), surface it so the operator
                  can still see what's currently in effect. */}
              {draft.model && !availableModels.includes(draft.model) && (
                <option key={`legacy-${draft.model}`} value={draft.model}>
                  {draft.model} (not in discovered list)
                </option>
              )}
            </select>
          )
        ) : (
          <div className="text-xs text-gray-500 italic">
            No models discovered. Click "Discover Models" to populate this list.
          </div>
        )}
      </div>

      <div>
        <label className="label">Timeout (s)</label>
        <input
          className="input"
          type="number"
          min={1}
          value={draft.timeout_seconds ?? 30}
          onChange={e => onChange({ timeout_seconds: Number(e.target.value) })}
        />
      </div>
      <div>
        <label className="label">Max retries</label>
        <input
          className="input"
          type="number"
          min={0}
          value={draft.max_retries ?? 2}
          onChange={e => onChange({ max_retries: Number(e.target.value) })}
        />
      </div>
      <div className="flex items-end">
        <label className="flex items-center gap-2 text-sm text-gray-300">
          <input
            type="checkbox"
            checked={!!draft.skip_tls_verify}
            onChange={e => onChange({ skip_tls_verify: e.target.checked })}
          />
          Skip TLS verify
        </label>
      </div>

      {draft.skip_tls_verify && (
        <div className="col-span-2 text-xs text-amber-400 bg-amber-900/10 border border-amber-700/30 rounded p-2">
          Warning: TLS certificate verification is disabled for this provider only.
          Use only for local development or self-signed internal endpoints.
        </div>
      )}
    </div>
  )
}
