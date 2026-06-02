import { useState, useRef, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  api,
  ListenerConfig,
  FieldsConfig,
  IngestResponse,
  PreviewResponse,
  RemoteJsonSourceDef,
  UploadProgress,
  isJobHandle,
} from '../api/client'
import FieldList from '../components/FieldList'
import JobProgressBar from '../components/JobProgressBar'
import LocalFeedPreviewPanel from '../components/LocalFeedPreviewPanel'
import SourceList from '../components/SourceList'
import SourceFieldsPanel from '../components/SourceFieldsPanel'
import SourcePreviewModal from '../components/SourcePreviewModal'
import Toggle from '../components/Toggle'
import ThreatIntelCatalog from '../components/ThreatIntelCatalog'
import BrandLogo from '../components/BrandLogo'
import UserManagementTab from './configuration/UserManagementTab'
import { useJobProgress } from '../hooks/useJobProgress'
import { useSourceRefresh, useRefreshingSources, useRefreshAll, useRefreshAllBusy, useRefreshAllResult, refreshId, type RefreshKind } from '../hooks/useExternalRefresh'
import { getAppBasePrefix } from '../utils/basePrefix'
import { useAuth } from '../auth/useAuth'
import { clsx } from 'clsx'
import { Upload, Plus, Trash2, Pencil, Check, X, RefreshCw, ChevronDown, ChevronUp } from 'lucide-react'

type Tab =
  | 'application'
  | 'listener'
  | 'api'
  | 'rss'
  | 'local-feed'
  | 'remote-feed'
  | 'threat-intel'
  | 'global-fields'
  | 'user-management'

const BASE_TABS: { id: Tab; label: string }[] = [
  { id: 'threat-intel',  label: 'Open Threat Feeds' },
  { id: 'local-feed',    label: 'Local Feed' },
  { id: 'remote-feed',   label: 'External Feeds' },
  { id: 'rss',           label: 'External RSS' },
  { id: 'api',           label: 'External API' },
  { id: 'listener',      label: 'Listener Endpoint' },
  { id: 'global-fields', label: 'Global Field Defaults' },
  { id: 'application',   label: 'Application' },
]

export default function Configuration() {
  const { authEnabled, isAdmin } = useAuth()
  const [activeTab, setActiveTab] = useState<Tab>('threat-intel')

  // Auth-gated tabs:
  //   - User Management is admin-only (prompts-045).
  // Self-service account management moved to its own top-level Account page
  // (prompts-046), so there is no longer an Account tab here.
  const TABS: { id: Tab; label: string }[] = [
    ...BASE_TABS,
    ...(authEnabled && isAdmin
      ? [{ id: 'user-management' as Tab, label: 'User Management' }]
      : []),
  ]

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-lg font-semibold text-gray-100">Configuration</h1>
        <p className="text-sm text-gray-500">Manage ingestion sources and field mappings.</p>
      </div>

      {/* Tabs */}
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

      <div className="max-w-3xl">
        {activeTab === 'application'   && <ApplicationTab />}
        {activeTab === 'listener'      && <ListenerTab />}
        {activeTab === 'api'           && <ApiTab />}
        {activeTab === 'rss'           && <RssTab />}
        {activeTab === 'local-feed'    && <LocalFeedTab />}
        {activeTab === 'remote-feed'   && <RemoteFeedTab />}
        {activeTab === 'threat-intel'  && <ThreatIntelCatalog />}
        {activeTab === 'global-fields' && <GlobalFieldsTab />}
        {activeTab === 'user-management' && <UserManagementTab />}
      </div>
    </div>
  )
}

// ── Shared helpers ────────────────────────────────────────────────────────────

function IngestResultBadge({ result }: { result: IngestResponse }) {
  const hasErrors = result.errors && result.errors.length > 0
  return (
    <div className={clsx(
      'rounded p-2 text-xs space-y-1',
      hasErrors
        ? 'bg-red-900/20 border border-red-700/30'
        : 'bg-green-900/20 border border-green-700/30',
    )}>
      <p className={clsx('font-medium', hasErrors ? 'text-red-400' : 'text-green-400')}>
        {hasErrors ? 'Completed with errors' : 'Done'}
        {' — '}
        <span className="text-gray-300">{result.inserted} inserted</span>
        {typeof result.duplicates === 'number' && result.duplicates > 0 && (
          <span className="text-gray-500">, {result.duplicates} duplicates</span>
        )}
        {typeof result.discarded === 'number' && result.discarded > 0 && (
          <span className="text-gray-500">, {result.discarded} discarded</span>
        )}
        {typeof result.duplicates !== 'number' && result.skipped > 0 && (
          <span className="text-gray-500">, {result.skipped} skipped</span>
        )}
        {typeof result.total_read === 'number' && (
          <span className="text-gray-600"> (read {result.total_read})</span>
        )}
      </p>
      {hasErrors && (
        <ul className="text-red-400 space-y-0.5 pl-2">
          {result.errors.map((e, i) => <li key={i} className="truncate">• {e}</li>)}
        </ul>
      )}
    </div>
  )
}

// ── Application Tab (prompts-017) ────────────────────────────────────────────

/** Validate a base URL prefix client-side using the same rules as the backend. */
function validateAppBasePrefix(v: string): string | null {
  if (v === '') return null
  if (v.length > 200) return 'Maximum length is 200 characters.'
  if (!v.startsWith('/')) return 'Must start with /.'
  if (v.endsWith('/')) return 'Must not end with /.'
  if (v.includes('//')) return 'Must not contain //.'
  if (!/^\/[A-Za-z0-9._\-/]*[A-Za-z0-9._-]$/.test(v)) {
    return 'Only A-Z, a-z, 0-9, dot, underscore, hyphen, and / are allowed.'
  }
  return null
}

function ApplicationTab() {
  const qc = useQueryClient()
  const { data: cfg } = useQuery({
    queryKey: ['app-base-prefix'],
    queryFn: api.getAppBasePrefix,
  })
  const [input, setInput] = useState<string>('')
  const [savedValue, setSavedValue] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Sync local input with server value once loaded / after refetch.
  useEffect(() => {
    if (cfg?.app_base_prefix != null) {
      setInput(cfg.app_base_prefix)
    }
  }, [cfg?.app_base_prefix])

  const mutation = useMutation({
    mutationFn: (v: string) => api.setAppBasePrefix(v),
    onSuccess: (resp) => {
      setSavedValue(resp.app_base_prefix)
      setError(null)
      qc.invalidateQueries({ queryKey: ['app-base-prefix'] })
    },
    onError: (err: unknown) => {
      setSavedValue(null)
      setError(err instanceof Error ? err.message : String(err))
    },
  })

  const validationError = validateAppBasePrefix(input)
  const unchanged = input === (cfg?.app_base_prefix ?? '')
  const saveDisabled = validationError !== null || unchanged || mutation.isPending

  return (
    <div className="card space-y-4">
      <div>
        <h3 className="text-sm font-semibold text-gray-200">Application</h3>
        <p className="text-xs text-gray-500 mt-1">
          Application-wide settings. These affect how the app generates URLs and where the
          API is mounted. Changes require a backend restart to take effect.
        </p>
      </div>

      <div className="border border-gray-700 rounded-lg px-3 py-2.5 space-y-2">
        <div>
          <p className="text-sm text-gray-300">Base URL Prefix</p>
          <p className="text-xs text-gray-500">
            Set this when the application is served behind a reverse proxy under a sub-path.
            Leave empty to mount at the root. Must start with <code className="font-mono">/</code> and
            must not end with <code className="font-mono">/</code>. Examples: <code className="font-mono">/feeds</code>,
            {' '}<code className="font-mono">/intel/v1</code>.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <input
            type="text"
            className="input flex-1 font-mono"
            placeholder="(empty — mount at root)"
            value={input}
            onChange={e => { setInput(e.target.value); setSavedValue(null); setError(null) }}
            spellCheck={false}
          />
          <button
            className="btn-primary text-xs"
            disabled={saveDisabled}
            onClick={() => mutation.mutate(input)}
          >
            {mutation.isPending ? 'Saving…' : 'Save'}
          </button>
        </div>
        {validationError && (
          <p className="text-xs text-red-400">{validationError}</p>
        )}
        {error && (
          <p className="text-xs text-red-400">{error}</p>
        )}
        {savedValue !== null && !error && (
          <div className="rounded border border-amber-700/40 bg-amber-900/20 text-amber-300 text-xs p-2 space-y-0.5">
            <p className="font-medium">Saved — restart required.</p>
            <p>
              New prefix: <code className="font-mono">{savedValue === '' ? '(empty)' : savedValue}</code>.
              Run <code className="font-mono">./threatfeeds-lite restart</code> on the server for
              the change to take effect.
            </p>
          </div>
        )}
      </div>

      <PaginationMaxSetting />
      <WatcherMaxEventsSetting />
      <LogoSetting />
    </div>
  )
}

// ── Normalized viewer pagination cap (prompts-043) ───────────────────────────

const PAGINATION_MAX_MIN = 50
const PAGINATION_MAX_MAX = 100_000

function PaginationMaxSetting() {
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ['pagination-max'],
    queryFn: api.getPaginationMax,
  })
  const [input, setInput] = useState<string>('')
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (data?.pagination_max != null) setInput(String(data.pagination_max))
  }, [data?.pagination_max])

  const mutation = useMutation({
    mutationFn: (v: number) => api.setPaginationMax(v),
    onSuccess: () => {
      setSaved(true)
      setError(null)
      qc.invalidateQueries({ queryKey: ['pagination-max'] })
    },
    onError: (err: unknown) => {
      setSaved(false)
      setError(err instanceof Error ? err.message : String(err))
    },
  })

  const parsed = Number(input)
  const valid =
    Number.isInteger(parsed) &&
    parsed >= PAGINATION_MAX_MIN &&
    parsed <= PAGINATION_MAX_MAX
  const unchanged = parsed === (data?.pagination_max ?? -1)
  const saveDisabled = !valid || unchanged || mutation.isPending

  return (
    <div className="border border-gray-700 rounded-lg px-3 py-2.5 space-y-2">
      <div>
        <p className="text-sm text-gray-300">Normalized Feeds — Pagination Max Rows</p>
        <p className="text-xs text-gray-500">
          The maximum number of normalized rows the Normalized Feeds viewer loads
          at once. Paging, filtering, and search operate over this window in the
          browser. Range {PAGINATION_MAX_MIN.toLocaleString()}–
          {PAGINATION_MAX_MAX.toLocaleString()}. Default 1,000. Takes effect
          immediately (no restart).
        </p>
      </div>
      <div className="flex items-center gap-2">
        <input
          type="number"
          min={PAGINATION_MAX_MIN}
          max={PAGINATION_MAX_MAX}
          className="input w-32 tabular-nums"
          value={input}
          onChange={e => { setInput(e.target.value); setSaved(false); setError(null) }}
        />
        <button
          className="btn-primary text-xs"
          disabled={saveDisabled}
          onClick={() => mutation.mutate(parsed)}
        >
          {mutation.isPending ? 'Saving…' : 'Save'}
        </button>
      </div>
      {!valid && input !== '' && (
        <p className="text-xs text-red-400">
          Must be an integer between {PAGINATION_MAX_MIN.toLocaleString()} and{' '}
          {PAGINATION_MAX_MAX.toLocaleString()}.
        </p>
      )}
      {error && <p className="text-xs text-red-400">{error}</p>}
      {saved && !error && <p className="text-xs text-green-400">Saved.</p>}
    </div>
  )
}

// ── Per-watcher stored/feed event cap (issue_local_006) ──────────────────────

const WATCHER_MAX_EVENTS_MIN = 10
const WATCHER_MAX_EVENTS_MAX = 100_000

function WatcherMaxEventsSetting() {
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ['watcher-max-events'],
    queryFn: api.getWatcherMaxEvents,
  })
  const [input, setInput] = useState<string>('')
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (data?.watcher_max_events != null) setInput(String(data.watcher_max_events))
  }, [data?.watcher_max_events])

  const mutation = useMutation({
    mutationFn: (v: number) => api.setWatcherMaxEvents(v),
    onSuccess: () => {
      setSaved(true)
      setError(null)
      qc.invalidateQueries({ queryKey: ['watcher-max-events'] })
    },
    onError: (err: unknown) => {
      setSaved(false)
      setError(err instanceof Error ? err.message : String(err))
    },
  })

  const parsed = Number(input)
  const valid =
    Number.isInteger(parsed) &&
    parsed >= WATCHER_MAX_EVENTS_MIN &&
    parsed <= WATCHER_MAX_EVENTS_MAX
  const unchanged = parsed === (data?.watcher_max_events ?? -1)
  const saveDisabled = !valid || unchanged || mutation.isPending

  return (
    <div className="border border-gray-700 rounded-lg px-3 py-2.5 space-y-2">
      <div>
        <p className="text-sm text-gray-300">Watchers — Stored Events Max</p>
        <p className="text-xs text-gray-500">
          The maximum number of triggered events stored (and served on the public
          feed) per watcher. Older events beyond this cap are pruned. Range{' '}
          {WATCHER_MAX_EVENTS_MIN.toLocaleString()}–
          {WATCHER_MAX_EVENTS_MAX.toLocaleString()}. Default 1,000. Takes effect
          on the next watcher evaluation (no restart).
        </p>
      </div>
      <div className="flex items-center gap-2">
        <input
          type="number"
          min={WATCHER_MAX_EVENTS_MIN}
          max={WATCHER_MAX_EVENTS_MAX}
          className="input w-32 tabular-nums"
          value={input}
          onChange={e => { setInput(e.target.value); setSaved(false); setError(null) }}
        />
        <button
          className="btn-primary text-xs"
          disabled={saveDisabled}
          onClick={() => mutation.mutate(parsed)}
        >
          {mutation.isPending ? 'Saving…' : 'Save'}
        </button>
      </div>
      {!valid && input !== '' && (
        <p className="text-xs text-red-400">
          Must be an integer between {WATCHER_MAX_EVENTS_MIN.toLocaleString()} and{' '}
          {WATCHER_MAX_EVENTS_MAX.toLocaleString()}.
        </p>
      )}
      {error && <p className="text-xs text-red-400">{error}</p>}
      {saved && !error && <p className="text-xs text-green-400">Saved.</p>}
    </div>
  )
}

// ── Branding logo (prompts-045) ──────────────────────────────────────────────
const LOGO_ACCEPT = 'image/png,image/jpeg,image/webp,image/gif'
const LOGO_ALLOWED_TYPES = ['image/png', 'image/jpeg', 'image/webp', 'image/gif']
const LOGO_MAX_BYTES = 2 * 1024 * 1024

export function LogoSetting() {
  const qc = useQueryClient()
  const { data } = useQuery({ queryKey: ['logo-info'], queryFn: api.getLogoInfo })
  const [file, setFile] = useState<File | null>(null)
  const [error, setError] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const hasLogo = data?.has_logo ?? false

  const refresh = () => qc.invalidateQueries({ queryKey: ['logo-info'] })

  const uploadMut = useMutation({
    mutationFn: (f: File) => api.uploadLogo(f),
    onSuccess: () => {
      setError(null)
      setFile(null)
      if (fileRef.current) fileRef.current.value = ''
      refresh()
    },
    onError: (e: unknown) => setError(e instanceof Error ? e.message : String(e)),
  })
  const deleteMut = useMutation({
    mutationFn: () => api.deleteLogo(),
    onSuccess: () => { setError(null); refresh() },
    onError: (e: unknown) => setError(e instanceof Error ? e.message : String(e)),
  })

  const pickFile = (f: File | null) => {
    setError(null)
    if (f && !LOGO_ALLOWED_TYPES.includes(f.type)) {
      setError('Unsupported image type. Allowed: PNG, JPEG, WebP, GIF.')
      setFile(null)
      return
    }
    if (f && f.size > LOGO_MAX_BYTES) {
      setError('Image exceeds the 2 MB limit.')
      setFile(null)
      return
    }
    setFile(f)
  }

  return (
    <div className="border border-gray-700 rounded-lg px-3 py-2.5 space-y-2">
      <div>
        <p className="text-sm text-gray-300">Branding Logo</p>
        <p className="text-xs text-gray-500">
          Replace the default app icon shown in the sidebar and on the sign-in screen.
          PNG, JPEG, WebP, or GIF up to 2&nbsp;MB. Takes effect immediately.
        </p>
      </div>

      <div className="flex items-center gap-3">
        <BrandLogo hasLogo={hasLogo} size={40} />
        <div className="flex-1">
          <input
            ref={fileRef}
            type="file"
            accept={LOGO_ACCEPT}
            className="input"
            onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
          />
        </div>
      </div>

      {error && <p className="text-xs text-red-400">{error}</p>}

      <div className="flex justify-end gap-2">
        {hasLogo && (
          <button
            className="btn-ghost text-xs"
            disabled={deleteMut.isPending}
            onClick={() => deleteMut.mutate()}
          >
            <Trash2 className="w-3.5 h-3.5" />
            {deleteMut.isPending ? 'Removing…' : 'Remove'}
          </button>
        )}
        <button
          className="btn-primary text-xs"
          disabled={!file || uploadMut.isPending}
          onClick={() => file && uploadMut.mutate(file)}
        >
          <Upload className="w-3.5 h-3.5" />
          {uploadMut.isPending ? 'Uploading…' : 'Upload'}
        </button>
      </div>
    </div>
  )
}

// ── Listener Tab ──────────────────────────────────────────────────────────────
export function ListenerTab() {
  const qc = useQueryClient()
  const { data: listener, isLoading } = useQuery<ListenerConfig>({
    queryKey: ['listener'],
    queryFn: api.getListener,
  })

  const currentEnabled = listener?.enabled ?? true

  const save = useMutation({
    mutationFn: (body: ListenerConfig) => api.updateListener(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['listener'] }),
  })

  if (isLoading) return <div className="text-sm text-gray-500">Loading...</div>

  const origin = `${window.location.origin}${getAppBasePrefix()}`

  return (
    <div className="card space-y-5">
      <h3 className="text-sm font-semibold text-gray-200">Push Listener (API Endpoint)</h3>

      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm text-gray-300">Status</p>
          <p className="text-xs text-gray-500">Enable or disable the push listener</p>
        </div>
        <Toggle
          checked={currentEnabled}
          onChange={enabled => save.mutate({ enabled })}
        />
      </div>

      <div className="border-t border-gray-800 pt-4 space-y-2">
        <p className="text-sm text-gray-300">Endpoint</p>
        <p className="text-xs text-gray-600">
          POST any JSON (a single object or an array of objects) to{' '}
          <span className="font-mono text-gray-400">{origin}/api/ingest/listener</span>.
          Events are indexed into a feed named after your username when
          authentication is enabled; otherwise the request is anonymous and
          indexed as{' '}
          <span className="font-mono text-gray-400">Received Feed &lt;epoch&gt;</span>.
        </p>
        <p className="text-xs text-gray-600">
          To push into an explicitly-named feed instead, POST to{' '}
          <span className="font-mono text-gray-400">{origin}/api/ingest/push/&lt;source_name&gt;</span>.
        </p>
      </div>

      <div className="border-t border-gray-800 pt-4">
        <p className="text-sm font-medium text-gray-300 mb-3">Field Configuration</p>
        <SourceFieldsPanel sourceType="listener" sourceName="listener" />
      </div>
    </div>
  )
}

// ── External API Tab ──────────────────────────────────────────────────────────

/**
 * Per-section "Refresh all" control (prompts-056). Triggers a single
 * backend batch refresh for one source kind, then invalidates that
 * section's query so the list reflects newly-ingested entries. A failure
 * on one source never aborts the batch — the summary reports succeeded /
 * failed counts.
 *
 * prompts-060: the in-flight state and last summary are read from the global
 * MutationCache (useRefreshAll / useRefreshAllBusy / useRefreshAllResult) so
 * "Refreshing…" and the summary survive a Configuration sub-tab unmount.
 * Settle-time section invalidation is registered in main.tsx.
 */
function RefreshAllButton({ kind }: { kind: RefreshKind }) {
  const refreshAll = useRefreshAll()
  const busy = useRefreshAllBusy(kind)
  const { summary, error } = useRefreshAllResult(kind)

  return (
    <div className="flex items-center gap-3 flex-wrap">
      <button
        type="button"
        className="btn-secondary text-xs flex items-center gap-1.5"
        onClick={() => refreshAll.mutate({ kind })}
        disabled={busy}
      >
        <RefreshCw className={busy ? 'w-3.5 h-3.5 animate-spin' : 'w-3.5 h-3.5'} />
        {busy ? 'Refreshing…' : 'Refresh all'}
      </button>
      {!busy && summary && (
        <span className="text-xs text-gray-400" data-testid="refresh-all-summary">
          {summary.total === 0
            ? 'No sources configured.'
            : `Refreshed ${summary.succeeded}/${summary.total}` +
              (summary.failed > 0 ? ` — ${summary.failed} failed` : '')}
        </span>
      )}
      {!busy && error && (
        <span className="text-xs text-red-400" role="alert">{error}</span>
      )}
    </div>
  )
}

export function ApiTab() {
  const { data: sources = [], isLoading } = useQuery({
    queryKey: ['api-pull'],    queryFn: api.getApiPull,
  })

  if (isLoading) return <div className="text-sm text-gray-500">Loading...</div>

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-4 gap-3 flex-wrap">
        <h3 className="text-sm font-semibold text-gray-200">External API Sources</h3>
        <RefreshAllButton kind="api-pull" />
      </div>
      <SourceList
        sources={sources}
        queryKey="api-pull"
        sourceType="api-pull"
        onAdd={api.addApiPull}
        onUpdate={api.updateApiPull}
        onDelete={api.deleteApiPull}
        onRefresh={api.refreshApiPull}
        onPreview={api.previewApiPullSource}
        onConfirmPreview={api.confirmSourcePreview}
        onCancelPreview={api.cancelSourcePreview}
      />
    </div>
  )
}

// ── External RSS Tab ──────────────────────────────────────────────────────────

function RssTab() {
  const { data: sources = [], isLoading } = useQuery({
    queryKey: ['rss-pull'],
    queryFn: api.getRssPull,
  })

  if (isLoading) return <div className="text-sm text-gray-500">Loading...</div>

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-4 gap-3 flex-wrap">
        <h3 className="text-sm font-semibold text-gray-200">External RSS Sources</h3>
        <RefreshAllButton kind="rss-pull" />
      </div>
      <SourceList
        sources={sources}
        queryKey="rss-pull"
        sourceType="rss-pull"
        onAdd={api.addRssPull}
        onUpdate={api.updateRssPull}
        onDelete={api.deleteRssPull}
        onRefresh={api.refreshRssPull}
        onPreview={api.previewRssPullSource}
        onConfirmPreview={api.confirmSourcePreview}
        onCancelPreview={api.cancelSourcePreview}
      />
    </div>
  )
}

// ── Local Feed Tab ────────────────────────────────────────────────────────────

function LocalFeedTab() {
  const [sourceName, setSourceName] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [result, setResult] = useState<IngestResponse | null>(null)
  const [preview, setPreview] = useState<PreviewResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const [previewing, setPreviewing] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  // Upload-bytes progress (prompts-021A item 2). Populated by the
  // XHR-based uploadMultipartWithProgress helper in api/client.ts while
  // the initial multipart POST is in flight; reset to null once the
  // request resolves (success or failure) so the UI hides the bar.
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const jobQuery = useJobProgress(jobId)

  // When the job completes, surface the counters as the result badge.
  useEffect(() => {
    const j = jobQuery.data
    if (!j) return
    if (j.state === 'done') {
      setResult({
        inserted:   j.counters.inserted   ?? 0,
        skipped:    (j.counters.duplicates ?? 0) + (j.counters.discarded ?? 0),
        errors:     [],
        total_read: j.counters.total_read,
        duplicates: j.counters.duplicates,
        discarded:  j.counters.discarded,
      })
      // Hold the progress bar briefly for the user to see the final state.
      const t = setTimeout(() => {
        setJobId(null)
        setFile(null)
        if (fileRef.current) fileRef.current.value = ''
      }, 1500)
      return () => clearTimeout(t)
    }
    if (j.state === 'error') {
      setError(j.error_msg || 'Ingest failed')
    }
  }, [jobQuery.data])

  const handleUpload = async () => {
    if (!sourceName.trim() || !file) return
    setError(null)
    setResult(null)
    setPreview(null)
    setJobId(null)
    setUploadProgress({ loaded: 0, total: file.size, pct: 0 })
    setPreviewing(true)
    try {
      const res = await api.previewLocalFeed(
        sourceName.trim(),
        file,
        p => setUploadProgress(p),
      )
      setPreview(res)
    } catch (e) {
      setError(String(e))
    } finally {
      setPreviewing(false)
      setUploadProgress(null)
    }
  }

  const handleConfirm = async () => {
    if (!preview) return
    setPending(true)
    setError(null)
    try {
      const res = await api.confirmPreview(preview.preview_id, true)
      setPreview(null)
      if (isJobHandle(res)) {
        setJobId(res.job_id)
      } else {
        setResult(res)
        setFile(null)
        if (fileRef.current) fileRef.current.value = ''
      }
    } catch (e) {
      setError(String(e))
    } finally {
      setPending(false)
    }
  }

  const handleCancelPreview = () => {
    setPreview(null)
  }

  return (
    <div className="card space-y-4">
      <div className="flex items-center gap-2">
        <Upload className="w-4 h-4 text-gray-400" />
        <h3 className="text-sm font-semibold text-gray-200">Local Feed Upload</h3>
      </div>
      <p className="text-xs text-gray-500">
        Upload a <span className="font-mono">.json</span>, <span className="font-mono">.csv</span>,{' '}
        <span className="font-mono">.xml</span>, or NDJSON file. Format is auto-detected.
        After upload you will see a verification table with a sample of the parsed entries —
        click <span className="text-gray-300">Confirm &amp; Ingest</span> to persist them.
        Duplicate entries are silently skipped.
      </p>

      <div className="space-y-3">
        <div>
          <label className="label">Source Name *</label>
          <input
            className="input font-mono"
            placeholder="e.g. my_upload"
            value={sourceName}
            onChange={e => setSourceName(e.target.value.replace(/\s+/g, '_').toLowerCase())}
          />
        </div>
        <div>
          <label className="label">Feed File *</label>
          <input
            ref={fileRef}
            className="input"
            type="file"
            accept=".json,.csv,.xml,.ndjson,.txt,.gz,.zip,application/json,text/csv,text/xml,application/xml,application/gzip,application/zip"
            onChange={e => { setFile(e.target.files?.[0] ?? null); setPreview(null); setResult(null) }}
          />
          {file && (
            <p className="text-xs text-gray-500 mt-1">
              {file.name} ({(file.size / 1024).toFixed(1)} KB)
            </p>
          )}
          <p className="text-[11px] text-gray-500 mt-1">
            Accepts JSON, NDJSON, CSV, XML, plus <code>.gz</code> and
            single-member <code>.zip</code> wrappers (auto-decompressed).
          </p>
        </div>
      </div>

      {error && <p className="text-xs text-red-400">{error}</p>}
      {result && <IngestResultBadge result={result} />}

      {/* Upload-bytes progress (prompts-021A item 2). Shown only while a
          multipart POST is in flight (uploadProgress is non-null), and
          hidden once the preview response is received. The numeric bar
          matches the style of JobProgressBar for visual consistency. */}
      {uploadProgress && (
        <div className="rounded-lg border border-brand-800/40 bg-brand-900/10 p-3 space-y-2">
          <div className="flex items-center justify-between text-xs">
            <span className="font-mono uppercase tracking-wide text-brand-200">
              uploading
            </span>
            <span className="text-gray-400 tabular-nums">
              {uploadProgress.total > 0
                ? `${(uploadProgress.loaded / 1024).toFixed(1)} / ${(uploadProgress.total / 1024).toFixed(1)} KB (${uploadProgress.pct}%)`
                : `${(uploadProgress.loaded / 1024).toFixed(1)} KB`}
            </span>
          </div>
          <div className="h-1 rounded-full bg-gray-800 overflow-hidden">
            <div
              className="h-full bg-brand-400 transition-all"
              style={{ width: uploadProgress.total > 0 ? `${uploadProgress.pct}%` : '20%' }}
            />
          </div>
        </div>
      )}

      {preview && (
        <LocalFeedPreviewPanel
          preview={preview}
          confirming={pending}
          onConfirm={handleConfirm}
          onCancel={handleCancelPreview}
        />
      )}

      {jobQuery.data && (
        <JobProgressBar job={jobQuery.data} />
      )}

      <div className="flex justify-end gap-2">
        <button
          className="btn-primary"
          disabled={!sourceName.trim() || !file || previewing || pending || !!preview || !!jobId}
          onClick={handleUpload}
        >
          <Upload className="w-3.5 h-3.5" />
          {previewing ? 'Uploading…' : 'Upload'}
        </button>
      </div>
    </div>
  )
}

// ── External Feeds Tab (internal id stays 'remote-feed') ──────────────────────

const EMPTY_REMOTE: RemoteJsonSourceDef = {
  name: '', url: '', enabled: true, continuous: false, interval_minutes: 15,
}

function RemoteFeedTab() {
  const qc = useQueryClient()
  const { data: sources = [], isLoading } = useQuery<RemoteJsonSourceDef[]>({
    queryKey: ['remote-json-pull'],
    queryFn: api.getRemoteJsonPull,
  })

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['remote-json-pull'] })
    api.reloadScheduler().catch(() => {})
  }

  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState<RemoteJsonSourceDef>(EMPTY_REMOTE)
  const [editingName, setEditingName] = useState<string | null>(null)
  const [editDraft, setEditDraft] = useState<RemoteJsonSourceDef>(EMPTY_REMOTE)
  const [expandedFields, setExpandedFields] = useState<Set<string>>(new Set())
  const [refreshResults, setRefreshResults] = useState<Record<string, IngestResponse>>({})
  const sourceRefresh = useSourceRefresh()
  const refreshingSources = useRefreshingSources()
  const [preview, setPreview] = useState<PreviewResponse | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)

  const addMut = useMutation({
    mutationFn: api.addRemoteJsonPull,
    onSuccess: () => { invalidate(); setAdding(false); setDraft(EMPTY_REMOTE) },
  })
  const previewMut = useMutation({
    mutationFn: api.previewRemoteJsonPullSource,
    onSuccess: (res) => { setPreview(res); setPreviewError(null) },
    onError: (e) => setPreviewError(String(e)),
  })
  const confirmPreviewMut = useMutation({
    mutationFn: (id: string) => api.confirmSourcePreview(id),
    onSuccess: () => {
      invalidate()
      setPreview(null)
      setAdding(false)
      setDraft(EMPTY_REMOTE)
    },
    onError: (e) => setPreviewError(String(e)),
  })
  const updateMut = useMutation({
    mutationFn: ({ name, s }: { name: string; s: RemoteJsonSourceDef }) =>
      api.updateRemoteJsonPull(name, s),
    onSuccess: () => { invalidate(); setEditingName(null) },
  })
  const deleteMut = useMutation({
    mutationFn: api.deleteRemoteJsonPull,
    onSuccess: invalidate,
  })
  const toggleMut = useMutation({
    mutationFn: ({ name, enabled }: { name: string; enabled: boolean }) => {
      const src = sources.find(s => s.name === name)!
      return api.updateRemoteJsonPull(name, { ...src, enabled })
    },
    onSuccess: invalidate,
  })

  const handleRefresh = (name: string) => {
    if (refreshingSources.has(refreshId('remote-json-pull', name))) return
    sourceRefresh.mutate(
      { kind: 'remote-json-pull', name },
      {
        onSuccess: (result) => {
          setRefreshResults(prev => ({ ...prev, [name]: result }))
          setTimeout(() => setRefreshResults(prev => {
            const next = { ...prev }; delete next[name]; return next
          }), 5000)
        },
        onError: () => {
          setRefreshResults(prev => ({ ...prev, [name]: { inserted: 0, skipped: 0, errors: ['fetch failed'] } }))
        },
      },
    )
  }

  const toggleFields = (name: string) => setExpandedFields(prev => {
    const next = new Set(prev)
    if (next.has(name)) next.delete(name); else next.add(name)
    return next
  })

  if (isLoading) return <div className="text-sm text-gray-500">Loading...</div>

  return (
    <div className="card space-y-4">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <h3 className="text-sm font-semibold text-gray-200">External Feed Pull Sources</h3>
        <RefreshAllButton kind="remote-json-pull" />
      </div>
      <p className="text-xs text-gray-500">
        Each source fetches a remote JSON, NDJSON, CSV, or XML URL. Enable <span className="text-gray-300">Continuous</span> to
        pull on a schedule — only new entries are indexed (duplicates are skipped automatically).
        Manual refresh is always available.
      </p>

      {sources.length === 0 && !adding && (
        <p className="text-xs text-gray-600 px-1 py-1">No sources configured.</p>
      )}

      <div className="space-y-3">
        {sources.map(src => (
          <div key={src.name} className="rounded-lg border border-gray-700 bg-gray-800/50 overflow-hidden">
            {editingName === src.name ? (
              <div className="p-3 space-y-3">
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="label">Name (read-only)</label>
                    <input className="input opacity-50" value={editDraft.name} disabled />
                  </div>
                  <div>
                    <label className="label">URL</label>
                    <input className="input" value={editDraft.url}
                      onChange={e => setEditDraft(d => ({ ...d, url: e.target.value }))} />
                  </div>
                </div>
                <div className="flex items-center gap-4">
                  <div className="flex items-center gap-2">
                    <Toggle
                      checked={editDraft.continuous}
                      onChange={v => setEditDraft(d => ({ ...d, continuous: v }))}
                    />
                    <span className="text-sm text-gray-300">Continuous pull</span>
                  </div>
                  {editDraft.continuous && (
                    <div className="flex items-center gap-2">
                      <label className="label mb-0">Every</label>
                      <input
                        className="input w-20"
                        type="number"
                        min={1}
                        value={editDraft.interval_minutes ?? 15}
                        onChange={e => setEditDraft(d => ({ ...d, interval_minutes: Number(e.target.value) }))}
                      />
                      <span className="text-sm text-gray-400">min</span>
                    </div>
                  )}
                </div>
                <div className="flex gap-2 justify-end">
                  <button className="btn-ghost" onClick={() => setEditingName(null)}>
                    <X className="w-3.5 h-3.5" /> Cancel
                  </button>
                  <button className="btn-primary" disabled={updateMut.isPending}
                    onClick={() => updateMut.mutate({ name: src.name, s: editDraft })}>
                    <Check className="w-3.5 h-3.5" /> Save
                  </button>
                </div>
              </div>
            ) : (
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
                  {src.continuous
                    ? <span className="text-xs bg-brand-900/40 text-brand-300 border border-brand-700/30 rounded px-1.5 py-0.5 shrink-0">
                        {src.interval_minutes ?? 15}m
                      </span>
                    : <span className="text-xs text-gray-600 shrink-0">manual</span>
                  }
                  {refreshResults[src.name] && (
                    <span className="text-xs text-green-400 shrink-0 font-mono">
                      +{refreshResults[src.name].inserted}
                      {typeof refreshResults[src.name].duplicates === 'number' && refreshResults[src.name].duplicates! > 0 && (
                        <span className="text-gray-500"> / {refreshResults[src.name].duplicates} dup</span>
                      )}
                      {typeof refreshResults[src.name].discarded === 'number' && refreshResults[src.name].discarded! > 0 && (
                        <span className="text-gray-500"> / {refreshResults[src.name].discarded} disc</span>
                      )}
                      {typeof refreshResults[src.name].duplicates !== 'number' && refreshResults[src.name].skipped > 0 && (
                        <span className="text-gray-500"> / {refreshResults[src.name].skipped} skip</span>
                      )}
                    </span>
                  )}
                  <button
                    className="btn-ghost p-1"
                    title="Manual refresh"
                    disabled={refreshingSources.has(refreshId('remote-json-pull', src.name))}
                    onClick={() => handleRefresh(src.name)}
                  >
                    <RefreshCw className={clsx('w-3.5 h-3.5', refreshingSources.has(refreshId('remote-json-pull', src.name)) && 'animate-spin')} />
                  </button>
                  <button className="btn-ghost p-1"
                    onClick={() => { setEditingName(src.name); setEditDraft({ ...src }) }}>
                    <Pencil className="w-3.5 h-3.5" />
                  </button>
                  <button className="btn-ghost p-1 text-red-400 hover:text-red-300"
                    onClick={() => deleteMut.mutate(src.name)}>
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                  <button
                    className="btn-ghost p-1"
                    title="Field configuration"
                    onClick={() => toggleFields(src.name)}
                  >
                    {expandedFields.has(src.name)
                      ? <ChevronUp className="w-3.5 h-3.5" />
                      : <ChevronDown className="w-3.5 h-3.5" />}
                  </button>
                </div>
                {expandedFields.has(src.name) && (
                  <div className="border-t border-gray-700 px-3 py-3">
                    <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
                      Field Configuration — {src.name}
                    </p>
                    <SourceFieldsPanel sourceType="remote-json-pull" sourceName={src.name} />
                  </div>
                )}
              </>
            )}
          </div>
        ))}

        {adding ? (
          <div className="rounded-lg border border-brand-700/40 bg-brand-900/10 p-3 space-y-3">
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="label">Name *</label>
                <input className="input font-mono" placeholder="my_remote_feed" value={draft.name}
                  onChange={e => setDraft(d => ({ ...d, name: e.target.value.replace(/\s+/g, '_').toLowerCase() }))} />
              </div>
              <div>
                <label className="label">URL *</label>
                <input className="input" placeholder="https://example.com/feed.json" value={draft.url}
                  onChange={e => setDraft(d => ({ ...d, url: e.target.value }))} />
              </div>
            </div>
            <div className="flex items-center gap-4">
              <div className="flex items-center gap-2">
                <Toggle
                  checked={draft.continuous}
                  onChange={v => setDraft(d => ({ ...d, continuous: v }))}
                />
                <span className="text-sm text-gray-300">Continuous pull</span>
              </div>
              {draft.continuous && (
                <div className="flex items-center gap-2">
                  <label className="label mb-0">Every</label>
                  <input
                    className="input w-20"
                    type="number"
                    min={1}
                    value={draft.interval_minutes ?? 15}
                    onChange={e => setDraft(d => ({ ...d, interval_minutes: Number(e.target.value) }))}
                  />
                  <span className="text-sm text-gray-400">min</span>
                </div>
              )}
            </div>
            {addMut.isError && <p className="text-xs text-red-400">{String(addMut.error)}</p>}
            {previewMut.isError && <p className="text-xs text-red-400">{String(previewMut.error)}</p>}
            <div className="flex gap-2 justify-end">
              <button className="btn-ghost" onClick={() => { setAdding(false); setDraft(EMPTY_REMOTE) }}>
                <X className="w-3.5 h-3.5" /> Cancel
              </button>
              <button
                className="btn-primary"
                disabled={!draft.name || !draft.url || addMut.isPending || previewMut.isPending}
                onClick={() => previewMut.mutate(draft)}
              >
                <Check className="w-3.5 h-3.5" />
                {previewMut.isPending ? 'Fetching preview...' : 'Preview'}
              </button>
            </div>
          </div>
        ) : (
          <button className="btn-secondary w-full justify-center" onClick={() => setAdding(true)}>
            <Plus className="w-3.5 h-3.5" /> Add Source
          </button>
        )}
      </div>
      {preview && (
        <SourcePreviewModal
          preview={preview}
          confirming={confirmPreviewMut.isPending}
          error={previewError}
          onConfirm={() => confirmPreviewMut.mutate(preview.preview_id)}
          onCancel={() => {
            api.cancelSourcePreview(preview.preview_id).catch(() => { /* best-effort */ })
            setPreview(null)
            setPreviewError(null)
          }}
        />
      )}
    </div>
  )
}

// ── Global Field Defaults Tab ─────────────────────────────────────────────────

function GlobalFieldsTab() {
  const { data: fields } = useQuery<FieldsConfig>({
    queryKey: ['fields'],
    queryFn: api.getFields,
  })
  const { data: ingestAllData } = useQuery({
    queryKey: ['ingest-all-fields'],
    queryFn: api.getIngestAllFields,
  })
  const { data: flattenData } = useQuery({
    queryKey: ['flatten-depth'],
    queryFn: api.getFlattenDepth,
  })
  const qc = useQueryClient()
  const toggleIngestAll = useMutation({
    mutationFn: (v: boolean) => api.setIngestAllFields(v),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['ingest-all-fields'] }) },
  })
  const updateFlatten = useMutation({
    mutationFn: (v: number) => api.setFlattenDepth(v),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['flatten-depth'] }) },
  })
  const [flattenInput, setFlattenInput] = useState<string>('')
  // Keep local input in sync with server value
  useEffect(() => {
    if (flattenData?.flatten_max_depth != null) {
      setFlattenInput(String(flattenData.flatten_max_depth))
    }
  }, [flattenData?.flatten_max_depth])

  const flattenSaveDisabled = (() => {
    const n = Number(flattenInput)
    if (!Number.isInteger(n) || n < 1 || n > 10) return true
    return n === (flattenData?.flatten_max_depth ?? -1)
  })()

  return (
    <div className="card space-y-4">
      <div>
        <h3 className="text-sm font-semibold text-gray-200">Global Field Defaults</h3>
        <p className="text-xs text-gray-500 mt-1">
          These settings apply to all sources that do not have per-source field overrides configured.
          Per-source overrides are set by expanding the chevron on each source row.
        </p>
      </div>

      {/* Ingest All Fields toggle */}
      <div className="flex items-center justify-between border border-gray-700 rounded-lg px-3 py-2.5">
        <div>
          <p className="text-sm text-gray-300">Ingest All Fields</p>
          <p className="text-xs text-gray-500">
            When enabled, ALL fields in incoming records are stored — field filtering is bypassed.
          </p>
        </div>
        <Toggle
          checked={ingestAllData?.ingest_all_fields ?? false}
          onChange={v => toggleIngestAll.mutate(v)}
        />
      </div>

      {/* Flatten depth (prompts-015) */}
      <div className="flex items-center justify-between border border-gray-700 rounded-lg px-3 py-2.5">
        <div>
          <p className="text-sm text-gray-300">Nested JSON Flatten Depth</p>
          <p className="text-xs text-gray-500">
            How many levels of nested JSON keys to flatten when ingesting feeds like NVD.
            Beyond this depth, values are stored as JSON strings. Range 1–10. Default 5.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <input
            type="number"
            min={1}
            max={10}
            className="input w-16 text-center tabular-nums"
            value={flattenInput}
            onChange={e => setFlattenInput(e.target.value)}
          />
          <button
            className="btn-primary text-xs"
            disabled={flattenSaveDisabled || updateFlatten.isPending}
            onClick={() => updateFlatten.mutate(Number(flattenInput))}
          >
            {updateFlatten.isPending ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>

      {fields
        ? <FieldList config={fields} />
        : <p className="text-xs text-gray-500">Loading fields…</p>}
    </div>
  )
}
