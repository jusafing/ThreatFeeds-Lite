/**
 * Add LLM Provider wizard (prompts-027).
 *
 * Strict 4-stage operator-driven flow:
 *
 *   Stage 1 — Identify
 *     Operator fills in name, kind, base_url, api_key + options.
 *     Button: "Connect to provider" — calls POST /providers/discover
 *     (list_models only; no probe). Inline failure message stays
 *     beside the button; no downstream stage renders.
 *
 *   Stage 2 — Discover (revealed once the discover call returned at all,
 *     i.e. the server responded — NOT gated on aggregate status==='ok')
 *     Model dropdown populated from discoverResult.models. A free-text
 *     input replaces the dropdown when (a) kind is anthropic (no /models
 *     endpoint) OR (b) the server returned 200 with an empty catalog
 *     (0 models published). prompts-028: this decoupling restores the
 *     pre-027 behaviour where a 200 with a non-empty model list always
 *     surfaced the dropdown; the backend still records status==='error'
 *     for the empty-catalog case in the Test Details transcript, but the
 *     wizard no longer treats that as a hard block. Only a *thrown*
 *     discover error (network / 4xx / 5xx) hides stage 2.
 *     Button: "Test Model" — calls POST /providers/test
 *     (full 2-step run; we only care about the 'complete' verdict).
 *
 *   Stage 3 — Probe (OPTIONAL; revealed alongside stage 2)
 *     "Test Model" produces probeResult. Success shows a green
 *     verdict; failure shows an inline error. prompts-055: the probe
 *     no longer gates stage 4 — it is purely informational. The
 *     operator may add the provider without ever testing it.
 *
 *   Stage 4 — Commit (revealed as soon as stage 2 is visible AND a
 *     model is selected — independent of the probe; prompts-055)
 *     Button: "Add Provider" — calls POST /providers.
 *
 * Edit invalidation: any change to a stage-1 field (name, kind,
 * base_url, api_key, timeout, retries, skip-tls) clears discoverResult,
 * probeResult, and lastProbedDraftHash so the operator re-Connects
 * from scratch. Changing the picked model only invalidates the probe
 * verdict (discover result is still good).
 *
 * Identifier regex (^[A-Za-z0-9_-]{1,40}$) is enforced client-side for
 * live feedback; the backend re-validates on POST.
 */
import { useEffect, useMemo, useState } from 'react'
import {
  X, RefreshCw, CheckCircle2, XCircle, Plus, Eye, Zap, Info,
} from 'lucide-react'

import {
  api,
  synthesizeErrorTestResult,
  type LLMDiscoverResult,
  type LLMProviderConfig,
  type LLMProviderKind,
  type LLMTestRunResult,
} from '../api/client'
import TestDetailsModal from './TestDetailsModal'

const KINDS: { id: LLMProviderKind; label: string }[] = [
  { id: 'openai', label: 'OpenAI' },
  { id: 'anthropic', label: 'Anthropic' },
  { id: 'ollama', label: 'Ollama (local)' },
  { id: 'openai_compatible', label: 'OpenAI-compatible' },
]

const DEFAULT_BASE_URL: Record<LLMProviderKind, string> = {
  openai: 'https://api.openai.com/v1',
  anthropic: 'https://api.anthropic.com',
  ollama: 'http://localhost:11434',
  openai_compatible: '',
}

/** Mirrors backend.llm.config.PROVIDER_NAME_RE exactly. */
const PROVIDER_NAME_RE = /^[A-Za-z0-9_-]{1,40}$/

/** Truncate long inline error strings so they fit in one row. */
const _INLINE_ERR_LIMIT = 200

interface Props {
  /** Names already present on the server — used for live duplicate check. */
  existingNames: string[]
  onClose: () => void
  /** Called after a successful POST /api/llm/providers. */
  onAdded: () => void
}

/**
 * Compute the identity hash of the stage-1 + model fields. Any change
 * invalidates the last probe verdict so Save (stage 4) stays gated.
 * Plain JSON of an ordered key set — identity, not security.
 */
function draftHash(p: {
  name: string
  kind: LLMProviderKind
  baseUrl: string
  apiKey: string
  model: string
  timeoutSeconds: number
  maxRetries: number
  skipTlsVerify: boolean
}): string {
  return JSON.stringify([
    p.name.trim(),
    p.kind,
    p.baseUrl.trim(),
    p.apiKey,
    p.model.trim(),
    p.timeoutSeconds,
    p.maxRetries,
    p.skipTlsVerify,
  ])
}

export default function AddProviderWizard({ existingNames, onClose, onAdded }: Props) {
  // ── Stage 1 fields ──────────────────────────────────────────────────────
  const [name, setName] = useState('')
  const [kind, setKind] = useState<LLMProviderKind>('openai')
  const [baseUrl, setBaseUrl] = useState(DEFAULT_BASE_URL.openai)
  const [apiKey, setApiKey] = useState('')
  const [timeoutSeconds, setTimeoutSeconds] = useState(30)
  const [maxRetries, setMaxRetries] = useState(2)
  const [skipTlsVerify, setSkipTlsVerify] = useState(false)

  // ── Stage 2/3 picked model + verdicts ───────────────────────────────────
  const [model, setModel] = useState('')

  const [connecting, setConnecting] = useState(false)
  const [discoverResult, setDiscoverResult] = useState<LLMDiscoverResult | null>(null)
  const [discoverError, setDiscoverError] = useState<string | null>(null)

  const [probing, setProbing] = useState(false)
  const [probeResult, setProbeResult] = useState<LLMTestRunResult | null>(null)
  const [probeError, setProbeError] = useState<string | null>(null)
  /** Draft-hash captured at the moment the last successful probe completed.
   *  When the current draft hash diverges, the green probe verdict is
   *  invalidated and Stage 4 disappears. */
  const [lastProbedHash, setLastProbedHash] = useState<string | null>(null)

  const [adding, setAdding] = useState(false)
  const [addError, setAddError] = useState<string | null>(null)

  const [showDetails, setShowDetails] = useState(false)

  // Reset everything when kind changes (different defaults / branches).
  useEffect(() => {
    setBaseUrl(prev => (prev === '' ? DEFAULT_BASE_URL[kind] : prev))
    setDiscoverResult(null)
    setDiscoverError(null)
    setProbeResult(null)
    setProbeError(null)
    setLastProbedHash(null)
    setModel('')
  }, [kind])

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !showDetails) onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose, showDetails])

  // ── Edit-invalidates-stage helpers ──────────────────────────────────────

  /** Stage-1 field edit: full reset of all derived state. Forces re-Connect. */
  const invalidateStage1 = () => {
    setDiscoverResult(null)
    setDiscoverError(null)
    setProbeResult(null)
    setProbeError(null)
    setLastProbedHash(null)
    setModel('')
  }

  /** Model change: discover stays valid; only the probe verdict resets. */
  const invalidateProbe = () => {
    setProbeResult(null)
    setProbeError(null)
    setLastProbedHash(null)
  }

  // ── Validation ──────────────────────────────────────────────────────────

  const nameValidationError = useMemo(() => {
    const trimmed = name.trim()
    if (!trimmed) return null
    if (!PROVIDER_NAME_RE.test(trimmed)) {
      return 'Allowed: A-Z, a-z, 0-9, _ and - (1-40 chars).'
    }
    if (existingNames.includes(trimmed)) {
      return `A provider named "${trimmed}" already exists.`
    }
    return null
  }, [name, existingNames])

  const canConnect = useMemo(() => {
    if (!name.trim() || nameValidationError) return false
    if (!baseUrl.trim()) return false
    if (kind !== 'ollama' && !apiKey.trim()) return false
    return true
  }, [name, nameValidationError, baseUrl, apiKey, kind])

  const discoveredModels = useMemo(
    () => discoverResult?.models ?? [],
    [discoverResult],
  )

  // Empty-catalog: the server *responded successfully* (a list_models
  // step with a 2xx status_code) but published 0 models. This is
  // distinct from a transport/HTTP failure (401/5xx/network), which
  // leaves no successful step and must surface as a red error. Anthropic
  // is excluded (it never lists; its free-text path is handled
  // separately by useFreeTextModel). prompts-028.
  const emptyCatalog = useMemo(() => {
    if (kind === 'anthropic') return false
    if (!discoverResult) return false
    if (discoveredModels.length > 0) return false
    const details = discoverResult.details ?? []
    return details.some(
      d =>
        d.step === 'list_models' &&
        typeof d.status_code === 'number' &&
        d.status_code >= 200 &&
        d.status_code < 300,
    )
  }, [kind, discoverResult, discoveredModels])

  // Use a free-text model input instead of the dropdown when there is no
  // discoverable catalog: anthropic (no /models endpoint) OR an empty
  // 200 catalog.
  const useFreeTextModel = kind === 'anthropic' || emptyCatalog

  // Stage 2 visible once the discover call returned a *usable* outcome:
  //   - a non-empty model list (any aggregate status — prompts-028
  //     decoupling: a 200 with models but backend status==='error' still
  //     yields a selectable list), OR
  //   - an empty catalog from a server that responded 2xx (free-text), OR
  //   - anthropic (no /models endpoint; always free-text).
  // A thrown discover error (network/4xx/5xx held in `discoverError`) or
  // a transport/HTTP failure (non-2xx, no models, not empty-catalog)
  // keeps stage 2 hidden.
  const stage2Visible = useMemo(() => {
    if (discoverError) return false
    if (!discoverResult) return false
    if (!canConnect) return false
    if (kind === 'anthropic') return true
    if (discoveredModels.length > 0) return true
    return emptyCatalog
  }, [discoverError, discoverResult, canConnect, kind, discoveredModels, emptyCatalog])

  // Stage 4 reveal: prompts-055 — Save is enabled as soon as the
  // discover step produced a usable model picker (stage 2 visible) and
  // the operator has selected/typed a model. The probe is optional and
  // no longer gates this. currentHash/lastProbedHash remain below solely
  // to drive the informational "Model OK" probe pill.
  const currentHash = useMemo(() => draftHash({
    name, kind, baseUrl, apiKey, model,
    timeoutSeconds, maxRetries, skipTlsVerify,
  }), [name, kind, baseUrl, apiKey, model, timeoutSeconds, maxRetries, skipTlsVerify])

  const stage4Visible = useMemo(() => {
    if (!stage2Visible) return false
    if (!model.trim()) return false
    return true
  }, [stage2Visible, model])

  // ── Draft payload ───────────────────────────────────────────────────────

  const draftPayload = useMemo<LLMProviderConfig>(() => ({
    name: name.trim(),
    kind,
    base_url: baseUrl.trim() || undefined,
    api_key: apiKey,
    model: model.trim() || undefined,
    timeout_seconds: timeoutSeconds,
    max_retries: maxRetries,
    skip_tls_verify: skipTlsVerify,
  }), [name, kind, baseUrl, apiKey, model, timeoutSeconds, maxRetries, skipTlsVerify])

  // ── Actions ─────────────────────────────────────────────────────────────

  /** Stage 1 → 2: discover models only. */
  async function runConnect() {
    setConnecting(true)
    setDiscoverResult(null)
    setDiscoverError(null)
    setProbeResult(null)
    setProbeError(null)
    setLastProbedHash(null)
    setModel('')
    try {
      const r = await api.llm.discoverDraft(draftPayload)
      setDiscoverResult(r)
      // Auto-pick the first model so the operator can hit "Test Model"
      // immediately. prompts-028: decoupled from aggregate status — pick
      // whenever a non-empty list came back (a 200 with models but
      // status==='error' still yields a usable list). Empty catalog /
      // anthropic leave model='' for free-text entry.
      if (r.models && r.models.length > 0) {
        setModel(r.models[0])
      }
    } catch (e) {
      setDiscoverError(e instanceof Error ? e.message : String(e))
    } finally {
      setConnecting(false)
    }
  }

  /** Stage 2 → 3: probe the picked model. */
  async function runProbe() {
    setProbing(true)
    setProbeResult(null)
    setProbeError(null)
    setLastProbedHash(null)
    // Capture the hash BEFORE awaiting so we can detect stale state if
    // the operator edits during the round-trip.
    const hashAtProbe = currentHash
    try {
      const r = await api.llm.testProviderDraft(draftPayload)
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

  /** Stage 4: commit the provider. */
  async function runAdd() {
    setAdding(true)
    setAddError(null)
    try {
      // prompts-031 change 1: persist the discovered catalog so the
      // freshly-added provider's card keeps the model dropdown after
      // Save (the operator can re-Discover later). Anthropic / empty-
      // catalog drafts leave it omitted and stay on the free-text path.
      const addBody: LLMProviderConfig = {
        ...draftPayload,
        available_models:
          discoverResult?.models && discoverResult.models.length > 0
            ? discoverResult.models
            : undefined,
      }
      await api.llm.addProvider(addBody)
      onAdded()
      onClose()
    } catch (e) {
      setAddError(e instanceof Error ? e.message : String(e))
    } finally {
      setAdding(false)
    }
  }

  // ── Inline error strings (truncated) ────────────────────────────────────

  // prompts-028: only a *thrown* discover error is a hard failure.
  // A 200 response with an empty catalog (backend status==='error' whose
  // sole list_models verdict is the benign "0 models published" string)
  // is NOT surfaced as a red error here — it drives the amber
  // empty-catalog note + free-text path instead. A list_models step that
  // carries a real transport/HTTP error string IS surfaced.
  const discoverInlineError = useMemo(() => {
    if (discoverError) return discoverError
    // prompts-028: a non-empty model list is a usable outcome regardless
    // of aggregate status — never surface a red error when models exist.
    if (discoveredModels.length > 0) return null
    if (emptyCatalog) return null
    if (discoverResult && discoverResult.status === 'error') {
      // Find the last list_models entry with an error.
      const details = discoverResult.details ?? []
      for (let i = details.length - 1; i >= 0; i--) {
        if (details[i].step === 'list_models' && details[i].error) {
          return details[i].error
        }
      }
      return 'Discover failed.'
    }
    return null
  }, [discoverError, discoverResult, emptyCatalog, discoveredModels])

  const probeInlineError = useMemo(() => {
    if (probeError) return probeError
    if (probeResult && probeResult.status === 'error') {
      // prompts-061: scan ANY step (last to first) for a real error detail,
      // mirroring the persisted-card extraction in LLMProvidersTab. The
      // generic "Probe failed." fallback only shows when no step carries a
      // detail, so we never surface that literal while a concrete cause exists.
      const details = probeResult.details ?? []
      for (let i = details.length - 1; i >= 0; i--) {
        if (details[i].error) {
          return details[i].error
        }
      }
      return 'Probe failed.'
    }
    return null
  }, [probeError, probeResult])

  const truncate = (s: string) =>
    s.length > _INLINE_ERR_LIMIT ? s.slice(0, _INLINE_ERR_LIMIT) + '…' : s

  // issue_local_02: the result backing "View test details". Prefer the richest
  // available payload (probe > discover transcript), but fall back to a
  // synthesised single-step result built from a thrown error string so the
  // link + modal also open when Test/Connect threw before any structured
  // transcript came back (network/4xx/5xx).
  const detailsResult = useMemo<LLMTestRunResult | null>(() => {
    if (probeResult) return probeResult
    if (probeError) return synthesizeErrorTestResult(probeError, 'complete')
    if (discoverResult) {
      return {
        status: discoverResult.status,
        details: discoverResult.details,
        models: discoverResult.models,
        sample: null,
      }
    }
    if (discoverError) return synthesizeErrorTestResult(discoverError, 'list_models')
    return null
  }, [probeResult, probeError, discoverResult, discoverError])

  // ── Render ──────────────────────────────────────────────────────────────

  return (
    <>
      <div
        className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
        role="dialog"
        aria-modal="true"
        aria-labelledby="add-llm-title"
      >
        <div className="card w-full max-w-2xl max-h-[90vh] overflow-y-auto">
          <div className="flex items-start justify-between mb-4">
            <div>
              <h2 id="add-llm-title" className="text-base font-semibold text-gray-100">
                Add LLM provider
              </h2>
              <p className="text-xs text-gray-500 mt-1">
                Connect to the provider, pick a model, then add. Testing is optional.
              </p>
            </div>
            <button
              type="button"
              className="text-gray-400 hover:text-gray-200"
              aria-label="Close"
              onClick={onClose}
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          {/* ── Stage 1: Identify ─────────────────────────────────────── */}

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label" htmlFor="add-llm-name">Name</label>
              <input
                id="add-llm-name"
                className="input"
                value={name}
                onChange={e => { setName(e.target.value); invalidateStage1() }}
                placeholder="my-openai"
                autoFocus
              />
              {nameValidationError && (
                <p className="text-xs text-red-400 mt-1">{nameValidationError}</p>
              )}
            </div>
            <div>
              <label className="label" htmlFor="add-llm-kind">Kind</label>
              <select
                id="add-llm-kind"
                className="input"
                value={kind}
                onChange={e => setKind(e.target.value as LLMProviderKind)}
              >
                {KINDS.map(k => (
                  <option key={k.id} value={k.id}>{k.label}</option>
                ))}
              </select>
            </div>

            <div className="col-span-2">
              <label className="label" htmlFor="add-llm-baseurl">Base URL</label>
              <input
                id="add-llm-baseurl"
                className="input"
                value={baseUrl}
                onChange={e => { setBaseUrl(e.target.value); invalidateStage1() }}
                placeholder={DEFAULT_BASE_URL[kind] || 'https://...'}
              />
              {kind === 'openai_compatible' && (
                <p className="text-xs text-gray-500 mt-1 italic">
                  Examples: <code>http://host:port/v1</code> (vLLM, LM Studio,
                  llama.cpp) — <code>http://host:port/api</code> (OpenWebUI).
                  For OpenWebUI, the API key is a JWT issued in
                  Settings → Account → API Keys.
                </p>
              )}
            </div>

            <div className="col-span-2">
              <label className="label" htmlFor="add-llm-apikey">
                API key
                {kind === 'ollama' && (
                  <span className="ml-2 text-xs text-gray-500">(optional for ollama)</span>
                )}
              </label>
              <input
                id="add-llm-apikey"
                className="input"
                type="password"
                autoComplete="new-password"
                value={apiKey}
                onChange={e => { setApiKey(e.target.value); invalidateStage1() }}
              />
            </div>

            <div>
              <label className="label" htmlFor="add-llm-timeout">Timeout (s)</label>
              <input
                id="add-llm-timeout"
                className="input"
                type="number"
                min={1}
                value={timeoutSeconds}
                onChange={e => { setTimeoutSeconds(Number(e.target.value)); invalidateStage1() }}
              />
            </div>
            <div>
              <label className="label" htmlFor="add-llm-retries">Max retries</label>
              <input
                id="add-llm-retries"
                className="input"
                type="number"
                min={0}
                value={maxRetries}
                onChange={e => { setMaxRetries(Number(e.target.value)); invalidateStage1() }}
              />
            </div>

            <div className="col-span-2">
              <label className="flex items-center gap-2 text-sm text-gray-300">
                <input
                  type="checkbox"
                  checked={skipTlsVerify}
                  onChange={e => { setSkipTlsVerify(e.target.checked); invalidateStage1() }}
                />
                Skip TLS verify
              </label>
              {skipTlsVerify && (
                <div className="mt-2 text-xs text-amber-400 bg-amber-900/10 border border-amber-700/30 rounded p-2">
                  Warning: TLS certificate verification is disabled for this provider only.
                  Use only for local development or self-signed internal endpoints.
                </div>
              )}
            </div>
          </div>

          {/* ── Stage 1 action: Connect to provider ─────────────────── */}

          <div className="mt-4 border-t border-gray-800 pt-3 flex items-center gap-3 flex-wrap">
            <button
              type="button"
              className="btn-secondary flex items-center gap-1.5"
              onClick={runConnect}
              disabled={!canConnect || connecting}
            >
              <RefreshCw className={connecting ? 'w-3.5 h-3.5 animate-spin' : 'w-3.5 h-3.5'} />
              {connecting ? 'Connecting…' : 'Connect to provider'}
            </button>

            {/* prompts-028: green pill whenever models came back
                non-empty (decoupled from aggregate status). Anthropic has
                no list but its discover "succeeds" for routing purposes. */}
            {discoverResult && !discoverError && discoveredModels.length > 0 && (
              <span
                className="flex items-center gap-1.5 text-xs text-green-400"
                data-testid="discover-verdict-ok"
              >
                <CheckCircle2 className="w-3.5 h-3.5" />
                Models discovered
                {` (${discoveredModels.length})`}
              </span>
            )}

            {/* prompts-028: amber note for a reachable server that
                published 0 models — operator falls through to free-text. */}
            {emptyCatalog && !discoverError && (
              <span
                className="flex items-center gap-1.5 text-xs text-amber-400"
                data-testid="discover-empty-catalog"
              >
                <Info className="w-3.5 h-3.5 shrink-0" />
                Server reachable, 0 models published
              </span>
            )}

            {discoverInlineError && (
              <span
                className="flex items-center gap-1.5 text-xs text-red-400"
                data-testid="discover-verdict-error"
              >
                <XCircle className="w-3.5 h-3.5 shrink-0" />
                <span className="break-all" role="alert">
                  {truncate(discoverInlineError)}
                </span>
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
          </div>

          {/* ── Stage 2: pick a model + Stage 3: probe it ───────────── */}

          {stage2Visible && (
            <div
              className="mt-4 border-t border-gray-800 pt-3 space-y-3"
              data-testid="stage-2"
            >
              <div>
                <label className="label" htmlFor="add-llm-model">Model</label>
                {useFreeTextModel ? (
                  <>
                    <input
                      id="add-llm-model"
                      className="input"
                      value={model}
                      onChange={e => { setModel(e.target.value); invalidateProbe() }}
                      placeholder={
                        kind === 'anthropic'
                          ? 'claude-3-5-sonnet-20241022'
                          : 'model-id'
                      }
                    />
                    <p className="text-xs text-gray-500 mt-1">
                      {kind === 'anthropic'
                        ? 'Anthropic does not expose a /models endpoint — enter the model id you want to use.'
                        : 'Server reachable, 0 models published — enter the model id you want to use.'}
                    </p>
                  </>
                ) : (
                  <select
                    id="add-llm-model"
                    className="input"
                    value={model}
                    onChange={e => { setModel(e.target.value); invalidateProbe() }}
                  >
                    <option value="">(select a model)</option>
                    {discoveredModels.map(m => (
                      <option key={m} value={m}>{m}</option>
                    ))}
                  </select>
                )}
              </div>

              <div className="flex items-center gap-3 flex-wrap">
                <button
                  type="button"
                  className="btn-secondary flex items-center gap-1.5"
                  onClick={runProbe}
                  disabled={!model.trim() || probing}
                >
                  <Zap className={probing ? 'w-3.5 h-3.5 animate-pulse' : 'w-3.5 h-3.5'} />
                  {probing ? 'Testing…' : 'Test Model'}
                </button>

                {probeResult?.status === 'ok' && lastProbedHash === currentHash && (
                  <span
                    className="flex items-center gap-1.5 text-xs text-green-400"
                    data-testid="probe-verdict-ok"
                  >
                    <CheckCircle2 className="w-3.5 h-3.5" />
                    Model OK
                    {probeResult.sample && (
                      <span className="text-gray-400"> — sample: {probeResult.sample}</span>
                    )}
                  </span>
                )}

                {probeInlineError && (
                  <span
                    className="flex items-center gap-1.5 text-xs text-red-400"
                    data-testid="probe-verdict-error"
                  >
                    <XCircle className="w-3.5 h-3.5 shrink-0" />
                    <span className="break-all" role="alert">
                      {truncate(probeInlineError)}
                    </span>
                  </span>
                )}
              </div>
            </div>
          )}

          {/* ── Stage 4: commit (rendered once a model is selected;
                  prompts-055 — independent of the optional probe) ─── */}

          {stage4Visible && (
            <div
              className="mt-4 border-t border-gray-800 pt-3 flex items-center gap-3 flex-wrap"
              data-testid="stage-4"
            >
              <button
                type="button"
                className="btn-primary flex items-center gap-1.5"
                onClick={runAdd}
                disabled={adding}
              >
                <Plus className="w-3.5 h-3.5" />
                {adding ? 'Adding…' : 'Add Provider'}
              </button>
              <button
                type="button"
                className="btn-secondary"
                onClick={onClose}
                disabled={adding}
              >
                Cancel
              </button>
              {addError && (
                <span className="text-xs text-red-400 break-all" role="alert">
                  {addError}
                </span>
              )}
            </div>
          )}

          {/* Fallback Cancel-only row when Stage 4 is not visible. */}
          {!stage4Visible && (
            <div className="mt-4 border-t border-gray-800 pt-3">
              <button type="button" className="btn-secondary" onClick={onClose}>
                Cancel
              </button>
            </div>
          )}
        </div>
      </div>

      {showDetails && detailsResult && (
        <TestDetailsModal
          result={detailsResult}
          providerLabel={name.trim() || '(unnamed draft)'}
          onClose={() => setShowDetails(false)}
        />
      )}
    </>
  )
}
