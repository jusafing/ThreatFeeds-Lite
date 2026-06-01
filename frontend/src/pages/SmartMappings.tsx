/**
 * Smart Mappings page (prompts-021E-2).
 *
 * Lists proposals from /api/smart-mappings/proposals with a status filter,
 * lets the operator generate a new proposal via SmartProposalConfirmModal,
 * and provides per-proposal approve / reject actions.
 *
 * Approve overlays the proposal mapping onto the active mapping; existing
 * operator entries win on conflict. Approving activates the proposal — it
 * becomes the active mapping and is applied by the normalizer when
 * Smart-mapping mode is enabled (prompts-056: the single Approve action;
 * the old "Approve + set mode=manual" mode-flip button was removed —
 * normalizer mode is managed on the Normalizer page). The response may
 * surface a "hint" when normalizer mode would ignore the mapping.
 */
import { useEffect, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { clsx } from 'clsx'
import { Sparkles, CheckCircle2, XCircle, Clock, AlertTriangle, Loader2, Trash2, Zap, Globe, RotateCcw, Archive, ChevronUp, ChevronDown, Play, Lock, History } from 'lucide-react'
import { api, type SmartProposal, type SmartProposalStatus, type SmartProposalOutcome, type SmartJobHandle } from '../api/client'
import { useRunActive, useNormalizerRunning } from '../hooks/useNormalizerRun'
import { useSmartActiveJobs } from '../hooks/useSmartActiveJobs'
import SmartProposalConfirmModal from '../components/SmartProposalConfirmModal'
import SmartProposalErrorModal from '../components/SmartProposalErrorModal'
import RunHistoryModal from '../components/RunHistoryModal'

// prompts-033: the outcome multiselect replaces the legacy status dropdown +
// "include auto-applied / discarded" checkbox. Empty selection = show all.
const OUTCOMES: SmartProposalOutcome[] = [
  'pending_review',
  'auto_applied',
  'approved',
  'rejected',
  'discarded_below_threshold',
  'error',
]

// prompts-032: sentinel source_name for consolidated (multi-feed) proposals.
const CONSOLIDATED_SENTINEL = '__consolidated__'

// prompts-034: human-facing label for a proposal. Prefer the persisted
// `proposal_name` ("Proposal-<UTC timestamp>"). Legacy rows predate the column,
// so fall back to a synthesised label: "Proposal-<created_at>" for consolidated
// rows, or the single feed's source_name otherwise.
function proposalLabel(p: SmartProposal): string {
  if (p.proposal_name) return p.proposal_name
  const consolidated =
    p.source_name === CONSOLIDATED_SENTINEL || (p.sources?.length ?? 0) > 0
  return consolidated ? `Proposal-${p.created_at}` : p.source_name
}

function statusBadge(status: SmartProposalStatus, isActive = false) {
  switch (status) {
    case 'pending':
      return (
        <span className="badge bg-yellow-900/50 text-yellow-300 border border-yellow-800/50 flex items-center gap-1">
          <Clock className="w-3 h-3" /> pending
        </span>
      )
    case 'approved':
      // prompts-042: only the proposal backing the active consolidated mapping
      // is green ("Approved / Active"). Superseded approved proposals are shown
      // in light blue ("Approved / Inactive") so exactly one is highlighted.
      return isActive ? (
        <span className="badge bg-green-900/50 text-green-300 border border-green-800/50 flex items-center gap-1">
          <CheckCircle2 className="w-3 h-3" /> Approved / Active
        </span>
      ) : (
        <span className="badge bg-blue-900/40 text-blue-300 border border-blue-800/50 flex items-center gap-1">
          <CheckCircle2 className="w-3 h-3" /> Approved / Inactive
        </span>
      )
    case 'rejected':
      return (
        <span className="badge bg-gray-800 text-gray-400 border border-gray-700 flex items-center gap-1">
          <XCircle className="w-3 h-3" /> rejected
        </span>
      )
    case 'error':
      return (
        <span className="badge bg-red-900/50 text-red-400 border border-red-800/50 flex items-center gap-1">
          <AlertTriangle className="w-3 h-3" /> error
        </span>
      )
  }
}

// prompts-021E-4: outcome carries auto-apply / discard provenance; status is
// the legacy operator-facing label. We render both so audit history is clear.
function outcomeBadge(outcome: SmartProposalOutcome | undefined) {
  if (!outcome || outcome === 'pending_review') return null
  if (outcome === 'auto_applied') {
    return (
      <span className="badge bg-brand-900/50 text-brand-300 border border-brand-800/50 flex items-center gap-1" title="auto-applied by smart-mode">
        <Zap className="w-3 h-3" /> auto-applied
      </span>
    )
  }
  if (outcome === 'discarded_below_threshold') {
    return (
      <span className="badge bg-gray-900/50 text-gray-500 border border-gray-800/50 flex items-center gap-1" title="below coverage_delta threshold">
        <Trash2 className="w-3 h-3" /> discarded
      </span>
    )
  }
  return null
}

/**
 * prompts-033: an optimistic "Processing" row for an in-flight consolidated
 * proposal job. Polls GET /smart-mappings/jobs/<id> until the job reaches a
 * terminal state. On success it removes itself and triggers a list refresh;
 * on error it stays visible with the failure message until dismissed.
 */
function ProcessingRow({
  handle,
  onResolved,
  onDismiss,
}: {
  handle: SmartJobHandle
  /** Called once when the job first reaches done/error → refresh the list. */
  onResolved: () => void
  /** Remove this processing row (auto on done; manual dismiss on error). */
  onDismiss: (jobId: string) => void
}) {
  const jobQuery = useQuery({
    queryKey: ['smart-job', handle.job_id],
    queryFn: () => api.smartMappings.getJob(handle.job_id),
    // Poll until terminal, then stop.
    refetchInterval: (query) => {
      const s = query.state.data?.state
      return s === 'done' || s === 'error' ? false : 1500
    },
  })

  const state = jobQuery.data?.state ?? handle.state
  const isDone = state === 'done'
  const isError = state === 'error'
  // prompts-049: the in-memory job store evicts a job ~1h after it settles and
  // loses everything on a backend restart. When a persisted handle outlives its
  // job, GET /jobs/{id} 404s — treat that as terminal so the stale Processing
  // row self-heals (drops + refreshes) instead of polling "Processing" forever.
  const fetchGone = jobQuery.isError
  const resolved = isDone || isError || fetchGone
  // prompts-034: detail popup for a job that errored without inserting a
  // proposal row. Surfaces the job's error_msg in the shared modal shape.
  const [showLog, setShowLog] = useState(false)

  useEffect(() => {
    if (!resolved) return
    // Refresh the proposals/active-mapping queries once the job settles.
    onResolved()
    // On success the real proposal now appears in the list, so drop the
    // optimistic row. When the job is gone from the server (evicted/restart) we
    // also drop it. On an in-band error we keep it so the operator sees the
    // failure message until they dismiss it.
    if (isDone || fetchGone) onDismiss(handle.job_id)
  }, [resolved, isDone, fetchGone, handle.job_id, onResolved, onDismiss])

  return (
    <li className="py-3 first:pt-0 last:pb-0" data-testid={`processing-${handle.job_id}`}>
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm font-mono text-gray-100" title={handle.sources.join(', ')}>
            New proposal
            <span className="text-xs text-gray-500"> ({handle.sources.length} feeds)</span>
          </span>
          {isError ? (
            <span className="badge bg-red-900/50 text-red-400 border border-red-800/50 flex items-center gap-1">
              <AlertTriangle className="w-3 h-3" /> error
            </span>
          ) : (
            <span className="badge bg-blue-900/50 text-blue-300 border border-blue-800/50 flex items-center gap-1">
              <Loader2 className="w-3 h-3 animate-spin" /> Processing
            </span>
          )}
        </div>
        {isError && (
          <button
            type="button"
            onClick={() => onDismiss(handle.job_id)}
            className="text-xs text-gray-500 hover:text-gray-300"
          >
            Dismiss
          </button>
        )}
      </div>
      {/* prompts-034: surface the job parameters while it runs so the operator
          can confirm what was submitted. The handle echoes the chosen model
          (null = provider default) and field scope. */}
      <p className="text-xs text-gray-500 mt-1">
        feeds: <span className="font-mono text-gray-400">{handle.sources.join(', ')}</span>
        {' · '}provider: {handle.provider ? (
          <span className="font-mono text-gray-400">{handle.provider}</span>
        ) : (
          'configured default'
        )}
        {' · '}model: {handle.model ? (
          <span className="font-mono text-gray-400">{handle.model}</span>
        ) : (
          'configured default'
        )}
        {' · '}scope: {handle.field_scope}
        {typeof handle.sample_size === 'number' && (
          <>{' · '}sample: {handle.sample_size}</>
        )}
      </p>
      {isError && (
        <p className="text-xs text-red-400 mt-1.5">
          {jobQuery.data?.error_msg ?? 'The proposal job failed.'}
          {' · '}
          <button
            type="button"
            onClick={() => setShowLog(true)}
            className="text-brand-400 hover:text-brand-300 underline"
          >
            View details
          </button>
        </p>
      )}
      {showLog && (
        <SmartProposalErrorModal
          detail={{
            title: 'Proposal job failed',
            subtitle: (
              <>
                Job <span className="font-mono text-gray-400">{handle.job_id}</span> ·{' '}
                {handle.sources.length} feeds
                {handle.model ? (
                  <>
                    {' · '}
                    <span className="font-mono text-gray-400">{handle.model}</span>
                  </>
                ) : null}
              </>
            ),
            sections: [
              {
                title: 'Error',
                body: jobQuery.data?.error_msg ?? 'The proposal job failed.',
              },
            ],
          }}
          onClose={() => setShowLog(false)}
        />
      )}
    </li>
  )
}

export default function SmartMappings() {
  const qc = useQueryClient()
  const [sourceFilter, setSourceFilter] = useState('')
  // prompts-033: outcome multiselect. Empty set = show everything. We always
  // fetch outcome='all' from the backend and filter client-side so toggling
  // outcomes never triggers a refetch.
  // prompts-038: default to pending + approved only (operators care about
  // actionable + active proposals); clearing the selection still shows all.
  const [outcomeFilter, setOutcomeFilter] = useState<Set<SmartProposalOutcome>>(
    () => new Set<SmartProposalOutcome>(['pending_review', 'approved']),
  )
  // prompts-034: when true, the list also includes archived proposals (the
  // backend hides them by default). Server-side filter → part of the query key.
  const [showArchived, setShowArchived] = useState(false)
  const [showModal, setShowModal] = useState(false)
  // prompts-033: the proposal whose raw LLM log is shown in the detail popup.
  const [logProposal, setLogProposal] = useState<SmartProposal | null>(null)
  // prompts-034: id of the proposal whose Archive action is armed (awaiting an
  // explicit second-click confirmation), mirroring the provider-delete pattern.
  const [confirmArchiveId, setConfirmArchiveId] = useState<number | null>(null)
  // prompts-033: optimistic in-flight proposal jobs rendered as "Processing"
  // rows above the list until they settle. prompts-049: held in the app-global
  // query cache (useSmartActiveJobs) so they survive navigating away and back.
  const { activeJobs, add: addActiveJob, remove: removeActiveJob } = useSmartActiveJobs()
  const [actionError, setActionError] = useState<string | null>(null)
  const [actionHint, setActionHint] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  // prompts-038: expand/collapse of the active-mapping card details, and the
  // in-flight state for the on-demand RUN (re-apply) action.
  const [activeExpanded, setActiveExpanded] = useState(false)
  // prompts-040: the active-card "Run" now uses the shared global run mutation
  // so its in-flight state survives navigation and is consistent with every
  // other run button. `running` is true whenever ANY normalizer run is active.
  const runActive = useRunActive()
  const running = useNormalizerRunning()
  // prompts-039: Run History modal visibility.
  const [showHistory, setShowHistory] = useState(false)

  const { data: sources } = useQuery({
    queryKey: ['summary'],
    queryFn: () => api.getSummary(),
  })

  // prompts-032 Phase D: the active consolidated mapping backing the card.
  const activeQuery = useQuery({
    queryKey: ['smart-active'],
    queryFn: () => api.smartMappings.getActive(),
    refetchInterval: 5000,
  })

  const proposalsQuery = useQuery({
    queryKey: ['smart-proposals', sourceFilter, showArchived],
    queryFn: () =>
      api.smartMappings.listProposals({
        source: sourceFilter || undefined,
        outcome: 'all',
        archived: showArchived ? 'all' : 'active',
      }),
    refetchInterval: 5000,
  })

  function toggleOutcome(o: SmartProposalOutcome) {
    setOutcomeFilter((prev) => {
      const next = new Set(prev)
      if (next.has(o)) next.delete(o)
      else next.add(o)
      return next
    })
  }

  // prompts-033: client-side outcome filtering. Empty selection shows all rows.
  const visibleProposals = useMemo(() => {
    const all = proposalsQuery.data ?? []
    if (outcomeFilter.size === 0) return all
    return all.filter((p) => {
      const key = (p.outcome ?? 'pending_review') as SmartProposalOutcome
      return outcomeFilter.has(key)
    })
  }, [proposalsQuery.data, outcomeFilter])

  // prompts-033: '__total__' is a summary total-count sentinel row from
  // GET /summary — it is not a real feed and must never appear in the source
  // filter or the proposal-modal feed picker.
  const sourceNames = useMemo(
    () =>
      (sources ?? [])
        .map((s) => s.source)
        .filter((s) => s !== '__total__')
        .sort(),
    [sources],
  )

  async function refresh() {
    await qc.invalidateQueries({ queryKey: ['smart-proposals'] })
    await qc.invalidateQueries({ queryKey: ['smart-active'] })
  }

  // prompts-033: a new job started from the confirm modal → show it as a
  // Processing row immediately, before the proposal exists in the DB.
  function handleJobStarted(handle: SmartJobHandle) {
    addActiveJob(handle)
  }

  function dismissJob(jobId: string) {
    removeActiveJob(jobId)
  }

  // prompts-056: a single "Approve" action. Approving activates the
  // proposal (its mapping becomes the active one); it is applied by the
  // normalizer when Smart-mapping mode is enabled. The legacy
  // "Approve + set mode=manual" variant (which flipped the normalizer mode
  // as a side effect) was removed — mode is managed on the Normalizer page.
  async function handleApprove(p: SmartProposal) {
    setActionError(null)
    setActionHint(null)
    setBusyId(p.id)
    try {
      const res = await api.smartMappings.approve(p.id, {})
      if (res.hint) setActionHint(res.hint)
      await refresh()
    } catch (err) {
      setActionError((err as Error).message)
    } finally {
      setBusyId(null)
    }
  }

  async function handleReject(p: SmartProposal) {
    setActionError(null)
    setActionHint(null)
    setBusyId(p.id)
    try {
      await api.smartMappings.reject(p.id, {})
      await refresh()
    } catch (err) {
      setActionError((err as Error).message)
    } finally {
      setBusyId(null)
    }
  }

  // prompts-038: re-apply the active consolidated mapping on demand — clears
  // and re-normalizes its feeds, then runs the normalizer. Surfaces the run
  // counters in the action banner.
  async function handleRunActive() {
    setActionError(null)
    setActionHint(null)
    try {
      const res = await runActive.mutateAsync()
      setActionHint(
        `Re-applied active mapping: processed ${res.processed ?? 0}, ` +
          `inserted ${res.inserted ?? 0}, errors ${res.errors ?? 0}.`,
      )
      await refresh()
    } catch (err) {
      setActionError((err as Error).message)
    }
  }

  // prompts-032 Phase D: return an operator-rejected proposal to pending.
  async function handleReenable(p: SmartProposal) {
    setActionError(null)
    setActionHint(null)
    setBusyId(p.id)
    try {
      await api.smartMappings.reenable(p.id, {})
      await refresh()
    } catch (err) {
      setActionError((err as Error).message)
    } finally {
      setBusyId(null)
    }
  }

  // prompts-034: archive a proposal (any status). Hides it from the default
  // list without deleting it. Gated behind an in-card confirmation.
  async function handleArchive(p: SmartProposal) {
    setActionError(null)
    setActionHint(null)
    setBusyId(p.id)
    try {
      await api.smartMappings.archive(p.id, {})
      setConfirmArchiveId(null)
      await refresh()
    } catch (err) {
      setActionError((err as Error).message)
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="p-6 max-w-5xl space-y-5">
      <div>
        <h1 className="text-lg font-semibold text-gray-100 flex items-center gap-2">
          <Sparkles className="w-5 h-5 text-brand-400" />
          Smart Mappings
        </h1>
        <p className="text-sm text-gray-500">
          LLM-assisted field-mapping proposals. The operator reviews and approves
          each proposal; nothing is applied automatically.
        </p>
      </div>

      {/* Generate panel */}
      <div className="card">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <p className="text-xs text-gray-500 max-w-md">
            Generate one <strong>consolidated</strong> proposal spanning the feeds
            you choose. Pick feeds, provider, sample size, and field scope in the
            dialog.
          </p>
          <button
            type="button"
            className="btn-primary text-sm flex items-center gap-1.5"
            disabled={sourceNames.length === 0}
            onClick={() => setShowModal(true)}
          >
            <Sparkles className="w-3.5 h-3.5" />
            Generate proposal
          </button>
        </div>
      </div>

      {/* Active consolidated mapping card (prompts-032 Phase D; prompts-038
          adds the proposal name, expand/collapse details, and on-demand RUN) */}
      {activeQuery.data?.active ? (
        <div className="card border-brand-700/60 bg-brand-950/30 ring-1 ring-brand-800/40">
          <div className="flex items-start justify-between gap-3 flex-wrap">
            <div className="flex items-start gap-2.5 min-w-0">
              <button
                type="button"
                className="btn-ghost p-1 mt-0.5 shrink-0"
                aria-label={activeExpanded ? 'Collapse details' : 'Expand details'}
                aria-expanded={activeExpanded}
                onClick={() => setActiveExpanded((v) => !v)}
              >
                {activeExpanded ? (
                  <ChevronUp className="w-4 h-4 text-brand-400" />
                ) : (
                  <ChevronDown className="w-4 h-4 text-brand-400" />
                )}
              </button>
              <Globe className="w-5 h-5 text-brand-400 mt-0.5 shrink-0" />
              <div className="min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-xs text-brand-300/80">Active consolidated mapping:</span>
                  <span
                    className="text-sm font-semibold text-brand-100 truncate"
                    title={`version #${activeQuery.data.active.id}`}
                  >
                    {activeQuery.data.active.proposal_name
                      || `version #${activeQuery.data.active.id}`}
                  </span>
                  <span className="badge bg-green-900/50 text-green-300 border border-green-800/50 flex items-center gap-1">
                    <CheckCircle2 className="w-3 h-3" /> active
                  </span>
                </div>
                {activeExpanded && (
                  <p className="text-xs text-gray-400 mt-1">
                    <span className="text-gray-200 font-medium">
                      {activeQuery.data.active.field_count}
                    </span>{' '}
                    fields ·{' '}
                    <span className="text-gray-200 font-medium">
                      {activeQuery.data.active.sources.length}
                    </span>{' '}
                    {activeQuery.data.active.sources.length === 1 ? 'feed' : 'feeds'}
                    {activeQuery.data.active.sources.length > 0 && (
                      <>
                        :{' '}
                        <span
                          className="text-gray-300"
                          title={activeQuery.data.active.sources.join(', ')}
                        >
                          {activeQuery.data.active.sources.join(', ')}
                        </span>
                      </>
                    )}
                    {activeQuery.data.active.field_scope && (
                      <> · scope: {activeQuery.data.active.field_scope}</>
                    )}
                  </p>
                )}
                {/* prompts-039: full {raw_field → canonical} mapping definition. */}
                {activeExpanded &&
                  activeQuery.data.active.mapping &&
                  Object.keys(activeQuery.data.active.mapping).length > 0 && (
                    <div className="mt-2 rounded border border-brand-800/40 bg-gray-950/40 p-2 max-h-48 overflow-y-auto">
                      <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-1">
                        Mapping definition
                      </div>
                      <ul className="space-y-0.5">
                        {Object.entries(activeQuery.data.active.mapping).map(
                          ([raw, canonical]) => (
                            <li
                              key={raw}
                              className="text-xs font-mono text-gray-300 flex items-center gap-1.5"
                            >
                              <span className="text-gray-400 truncate">{raw}</span>
                              <span className="text-gray-600">→</span>
                              <span className="text-brand-300 truncate">{canonical}</span>
                            </li>
                          ),
                        )}
                      </ul>
                    </div>
                  )}
              </div>
            </div>
            <div className="flex items-center gap-3 shrink-0">
              <button
                type="button"
                className="btn-secondary text-xs flex items-center gap-1.5 disabled:opacity-50"
                onClick={handleRunActive}
                disabled={running}
                title="Re-apply this mapping to its feeds now"
              >
                {running ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Play className="w-3.5 h-3.5" />
                )}
                {running ? 'Running…' : 'Run'}
              </button>
              <button
                type="button"
                className="btn-secondary text-xs flex items-center gap-1.5"
                onClick={() => setShowHistory(true)}
                title="View normalizer run history"
              >
                <History className="w-3.5 h-3.5" />
                Run History
              </button>
              {activeExpanded && (
                <div className="text-xs text-gray-500 text-right">
                  <div>approved {activeQuery.data.active.created_at}</div>
                  <div className="text-gray-600">
                    version #{activeQuery.data.active.id}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      ) : (
        <div className="card border-gray-800 bg-gray-900/40">
          <p className="text-xs text-gray-500 flex items-center gap-2">
            <Globe className="w-4 h-4 text-gray-600" />
            No active consolidated mapping yet. Approve a proposal below to
            activate one.
          </p>
        </div>
      )}

      {/* Filters */}
      <div className="card">
        <div className="flex items-end gap-4 flex-wrap">
          <div>
            <label htmlFor="source-filter" className="block text-xs text-gray-400 mb-1">
              Source
            </label>
            <select
              id="source-filter"
              className="input"
              value={sourceFilter}
              onChange={(e) => setSourceFilter(e.target.value)}
            >
              <option value="">All sources</option>
              {sourceNames.map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
          </div>
          {/* prompts-033: outcome multiselect (replaces status dropdown +
              include-audit checkbox). Empty selection shows all rows. */}
          <div>
            <span className="block text-xs text-gray-400 mb-1">Outcome</span>
            <div
              className="flex items-center gap-1.5 flex-wrap"
              role="group"
              aria-label="Outcome filter"
            >
              {OUTCOMES.map((o) => {
                const selected = outcomeFilter.has(o)
                return (
                  <button
                    key={o}
                    type="button"
                    onClick={() => toggleOutcome(o)}
                    aria-pressed={selected}
                    className={clsx(
                      'text-[11px] px-2 py-0.5 rounded border transition-colors',
                      selected
                        ? 'bg-brand-900/50 text-brand-200 border-brand-700'
                        : 'bg-gray-900 text-gray-500 border-gray-700 hover:text-gray-300',
                    )}
                  >
                    {o.replace(/_/g, ' ')}
                  </button>
                )
              })}
            </div>
          </div>
          {/* prompts-034: reveal archived proposals (hidden by default). */}
          <div>
            <span className="block text-xs text-gray-400 mb-1">Archived</span>
            <button
              type="button"
              onClick={() => setShowArchived((v) => !v)}
              aria-pressed={showArchived}
              data-testid="toggle-archived"
              className={clsx(
                'text-[11px] px-2 py-0.5 rounded border transition-colors',
                showArchived
                  ? 'bg-brand-900/50 text-brand-200 border-brand-700'
                  : 'bg-gray-900 text-gray-500 border-gray-700 hover:text-gray-300',
              )}
            >
              {showArchived ? 'Showing archived' : 'Show archived'}
            </button>
          </div>
        </div>
      </div>

      {/* Action feedback */}
      {actionError && (
        <div className="text-xs text-red-400 bg-red-900/30 border border-red-800/50 rounded px-2 py-1.5">
          {actionError}
        </div>
      )}
      {actionHint && (
        <div className="text-xs text-yellow-300 bg-yellow-900/30 border border-yellow-800/50 rounded px-2 py-1.5">
          {actionHint}
        </div>
      )}

      {/* Proposals list */}
      <div className="card">
        {/* prompts-033: optimistic Processing rows for in-flight jobs always
            render above the persisted proposals. */}
        {activeJobs.length > 0 && (
          <ul className="divide-y divide-gray-800 mb-3 border-b border-gray-800 pb-3">
            {activeJobs.map((j) => (
              <ProcessingRow
                key={j.job_id}
                handle={j}
                onResolved={refresh}
                onDismiss={dismissJob}
              />
            ))}
          </ul>
        )}
        {proposalsQuery.isLoading ? (
          <p className="text-sm text-gray-500">Loading…</p>
        ) : visibleProposals.length > 0 ? (
          <div className="space-y-3">
            {visibleProposals.map((p) => {
              const mappingEntries = Object.entries(p.mapping ?? {})
              // prompts-039: the proposal backing the active consolidated
              // mapping is locked — it cannot be archived (would orphan the
              // live mapping). Backend enforces this too (409).
              const isActiveProposal =
                activeQuery.data?.active?.proposal_id === p.id
              return (
                <div
                  key={p.id}
                  className={clsx(
                    'rounded-lg border p-3',
                    isActiveProposal
                      ? 'border-green-800/50 bg-green-950/20'
                      : 'border-gray-800 bg-gray-950/40',
                  )}
                >
                  <div className="flex items-center justify-between gap-3 mb-1.5">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span
                        className="text-sm font-mono text-gray-100"
                        title={(p.sources ?? []).join(', ')}
                      >
                        {proposalLabel(p)}
                        {p.sources && p.sources.length > 0 && (
                          <span className="text-xs text-gray-500"> ({p.sources.length} feeds)</span>
                        )}
                      </span>
                      {statusBadge(p.status, isActiveProposal)}
                      {outcomeBadge(p.outcome)}
                      {/* prompts-021E-3 trigger provenance */}
                      {p.trigger_reason && p.trigger_reason !== 'manual' && (
                        <span
                          className="badge bg-gray-800 text-gray-400 border border-gray-700 text-[10px]"
                          title={`triggered by ${p.trigger_reason}`}
                        >
                          {p.trigger_reason}
                        </span>
                      )}
                      {/* prompts-021E-4 coverage delta */}
                      {typeof p.score === 'number' && (
                        <span
                          className="text-[10px] font-mono text-gray-500"
                          title={
                            p.score_breakdown
                              ? `coverage_before=${p.score_breakdown.coverage_before.toFixed(3)} after=${p.score_breakdown.coverage_after.toFixed(3)}`
                              : 'coverage_delta'
                          }
                        >
                          Δ{(p.score * 100).toFixed(1)}%
                        </span>
                      )}
                      <span className="text-xs text-gray-500">
                        #{p.id} · {p.provider_name ?? '—'} / {p.model ?? '—'}
                        {typeof p.sample_size === 'number' && p.sample_size > 0 && (
                          <> · {p.sample_size}/feed</>
                        )}
                        {p.field_scope && <> · scope: {p.field_scope}</>}
                      </span>
                    </div>
                    <div className="text-xs text-gray-600">{p.created_at}</div>
                  </div>

                  {mappingEntries.length > 0 ? (
                    <div className="text-xs font-mono text-gray-400 bg-gray-950/60 border border-gray-800 rounded px-2 py-1.5">
                      {mappingEntries.map(([raw, canon]) => (
                        <div key={raw}>
                          <span className="text-gray-300">{raw}</span>
                          <span className="text-gray-600"> → </span>
                          <span className="text-brand-400">{canon}</span>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-xs text-gray-600 italic">
                      (no mapping —{' '}
                      <button
                        type="button"
                        onClick={() => setLogProposal(p)}
                        className="text-brand-400 hover:text-brand-300 underline not-italic"
                      >
                        view LLM log
                      </button>
                      )
                    </p>
                  )}

                  {/* prompts-033: error rows always expose the raw LLM log,
                      even when a partial mapping was parsed. */}
                  {p.status === 'error' && mappingEntries.length > 0 && (
                    <button
                      type="button"
                      onClick={() => setLogProposal(p)}
                      className="mt-1.5 text-xs text-brand-400 hover:text-brand-300 underline"
                    >
                      View LLM log
                    </button>
                  )}

                  {p.status === 'pending' && (
                    <div className="flex items-center gap-2 mt-2">
                      <button
                        type="button"
                        className="btn-primary text-xs"
                        disabled={busyId === p.id}
                        onClick={() => handleApprove(p)}
                      >
                        {busyId === p.id ? <Loader2 className="w-3 h-3 animate-spin" /> : 'Approve'}
                      </button>
                      <button
                        type="button"
                        className="btn-danger text-xs"
                        disabled={busyId === p.id}
                        onClick={() => handleReject(p)}
                      >
                        Reject
                      </button>
                    </div>
                  )}

                  {/* prompts-032 Phase D: re-enable operator-rejected proposals.
                      Auto-discarded rows (below threshold) are an automated
                      signal and cannot be re-enabled — no button shown. */}
                  {p.status === 'rejected' && p.outcome !== 'discarded_below_threshold' && (
                    <div className="flex items-center gap-2 mt-2">
                      <button
                        type="button"
                        className="btn-secondary text-xs flex items-center gap-1.5"
                        disabled={busyId === p.id}
                        onClick={() => handleReenable(p)}
                      >
                        {busyId === p.id ? (
                          <Loader2 className="w-3 h-3 animate-spin" />
                        ) : (
                          <RotateCcw className="w-3 h-3" />
                        )}
                        Re-enable
                      </button>
                    </div>
                  )}

                  {/* prompts-034: archive action — available on any proposal,
                      gated behind an in-card confirmation. Archived rows show a
                      badge instead (only visible via the Archived filter). */}
                  {p.archived ? (
                    <div className="mt-2">
                      <span className="badge bg-gray-800 text-gray-400 border border-gray-700 flex items-center gap-1 w-fit">
                        <Archive className="w-3 h-3" /> archived
                      </span>
                    </div>
                  ) : isActiveProposal ? (
                    <div className="mt-2">
                      <span
                        className="text-xs text-gray-600 flex items-center gap-1"
                        title="This proposal backs the active consolidated mapping and cannot be archived. Deactivate the mapping first."
                      >
                        <Lock className="w-3 h-3" /> Active — cannot archive
                      </span>
                    </div>
                  ) : confirmArchiveId === p.id ? (
                    <div
                      className="border border-gray-700 bg-gray-900/60 rounded p-2.5 mt-2 space-y-2"
                      data-testid={`archive-confirm-${p.id}`}
                      role="alertdialog"
                      aria-label={`Confirm archive proposal ${p.id}`}
                    >
                      <p className="text-xs text-gray-200">
                        Archive this proposal? It will be hidden from the default
                        list but kept for audit (not deleted).
                      </p>
                      <div className="flex items-center gap-2">
                        <button
                          type="button"
                          className="btn-secondary text-xs flex items-center gap-1.5"
                          disabled={busyId === p.id}
                          onClick={() => handleArchive(p)}
                          data-testid={`archive-confirm-yes-${p.id}`}
                        >
                          {busyId === p.id ? (
                            <Loader2 className="w-3 h-3 animate-spin" />
                          ) : (
                            <Archive className="w-3 h-3" />
                          )}
                          Confirm archive
                        </button>
                        <button
                          type="button"
                          className="text-xs text-gray-500 hover:text-gray-300"
                          disabled={busyId === p.id}
                          onClick={() => setConfirmArchiveId(null)}
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className="mt-2">
                      <button
                        type="button"
                        className="text-xs text-gray-500 hover:text-gray-300 flex items-center gap-1"
                        onClick={() => setConfirmArchiveId(p.id)}
                        data-testid={`archive-${p.id}`}
                      >
                        <Archive className="w-3 h-3" /> Archive
                      </button>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        ) : (
          <p className="text-sm text-gray-500">
            {activeJobs.length > 0
              ? 'Generating your consolidated proposal…'
              : 'No proposals yet. Click “Generate proposal” above to create a consolidated mapping.'}
          </p>
        )}
      </div>

      {showModal && (
        <SmartProposalConfirmModal
          sources={sourceNames}
          onClose={() => setShowModal(false)}
          onCreated={handleJobStarted}
        />
      )}

      {logProposal && (
        <SmartProposalErrorModal
          proposal={logProposal}
          onClose={() => setLogProposal(null)}
        />
      )}

      {showHistory && <RunHistoryModal onClose={() => setShowHistory(false)} />}
    </div>
  )
}
