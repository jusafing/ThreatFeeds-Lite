/**
 * Typed API client — all fetch calls go through here.
 *
 * URL contract (prompts-019):
 *   - When the application has NO base prefix configured (the default), all
 *     URLs produced by this client are DOCUMENT-RELATIVE: BASE is the bare
 *     string "api" and fetch() resolves it against the document's <base href>.
 *   - When a non-empty prefix is configured, BASE becomes "<prefix>/api"
 *     (root-anchored). This is the forcing path used only when relative URLs
 *     cannot work (e.g. proxy environments that demand absolute paths).
 *
 * The runtime prefix is read from <meta name="app-base-prefix">, which the
 * backend injects only when a non-empty prefix is configured. See
 * frontend/src/utils/basePrefix.ts.
 */
import { getAppBasePrefix } from '../utils/basePrefix'

const _prefix = getAppBasePrefix()
const BASE = _prefix ? `${_prefix}/api` : 'api'

/**
 * URL for the branding logo image (prompts-045). Uses the same relative BASE
 * as the API client so it resolves under any reverse-proxy prefix. Pass a
 * cache-buster (e.g. a counter bumped after upload/delete) to force the
 * browser to refetch.
 */
export function logoSrc(cacheBust?: number | string): string {
  const suffix = cacheBust != null ? `?v=${cacheBust}` : ''
  return `${BASE}/app/logo${suffix}`
}

// prompts-045: global 401 handler. The AuthProvider registers a callback so a
// 401 from ANY request (e.g. an expired session) drops the cached user and
// bounces the SPA to /login. Kept module-level so the plain `request` helper
// and the XHR upload helper share one hook.
let onUnauthorized: (() => void) | null = null
export function setUnauthorizedHandler(fn: (() => void) | null): void {
  onUnauthorized = fn
}
function _notifyUnauthorized(): void {
  if (onUnauthorized) onUnauthorized()
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    // Always send the session cookie (same-origin in prod; CORS allows
    // credentials for the dev server, see backend CORS config).
    credentials: 'include',
    ...options,
  })
  if (!res.ok) {
    if (res.status === 401) _notifyUnauthorized()
    const text = await res.text()
    throw new Error(`${res.status} ${res.statusText}: ${text}`)
  }
  // 204 No Content: empty body, return undefined cast to T so callers
  // using request<void> (e.g. DELETE endpoints) don't choke on json().
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

// ── Multipart upload with progress (prompts-021A item 2) ─────────────────────

export interface UploadProgress {
  loaded: number
  total: number
  /** Integer 0–100. 0 when total is unknown. */
  pct: number
}

/**
 * POST a multipart/form-data body and report upload-byte progress.
 *
 * Built on XMLHttpRequest because the Fetch API does not expose
 * request-body upload progress in any current browser (the Streams
 * spec for request bodies is still partial and not portable). This
 * helper is intentionally scoped to multipart uploads only — every
 * other endpoint continues to use the `fetch`-based `request` helper.
 *
 * Resolves with the parsed JSON response on 2xx, rejects with an Error
 * carrying status code and response body on any other outcome (including
 * network failure and timeout).
 */
export function uploadMultipartWithProgress<T>(
  path: string,
  form: FormData,
  onProgress?: (p: UploadProgress) => void,
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${BASE}${path}`, true)
    // Send the session cookie with the multipart upload (prompts-045).
    xhr.withCredentials = true

    if (onProgress) {
      xhr.upload.addEventListener('progress', (ev: ProgressEvent) => {
        if (ev.lengthComputable) {
          const loaded = ev.loaded
          const total = ev.total
          const pct = total > 0 ? Math.min(100, Math.round((loaded / total) * 100)) : 0
          onProgress({ loaded, total, pct })
        } else {
          onProgress({ loaded: ev.loaded, total: 0, pct: 0 })
        }
      })
    }

    xhr.addEventListener('load', () => {
      const ok = xhr.status >= 200 && xhr.status < 300
      if (!ok) {
        if (xhr.status === 401) _notifyUnauthorized()
        reject(new Error(`${xhr.status} ${xhr.statusText}: ${xhr.responseText}`))
        return
      }
      try {
        const parsed = JSON.parse(xhr.responseText) as T
        resolve(parsed)
      } catch (e) {
        reject(new Error(`Failed to parse response body: ${String(e)}`))
      }
    })
    xhr.addEventListener('error', () => reject(new Error('Network error during upload')))
    xhr.addEventListener('abort', () => reject(new Error('Upload aborted')))

    xhr.send(form)
  })
}

// ── Types ──────────────────────────────────────────────────────────────────

export interface Entry {
  id?: number
  source: string
  ingested_at: string
  indicator?: string
  indicator_type?: string
  threat_type?: string
  severity?: string
  confidence?: number
  title?: string
  description?: string
  tags?: string
  tlp?: string
  published_at?: string
  cve_id?: string
  cvss_score?: number
  mitre_attack_id?: string
  malware_family?: string
  actor?: string
  country?: string
  ingest_mode?: string
  [key: string]: unknown
}

export interface ActiveJobInfo {
  job_id: string
  kind: string
  step: JobStep
  processed: number
  total: number
}

export interface SummaryItem {
  source: string
  count: number
  last_ingested_at?: string | null
  last_total_read?: number | null
  last_inserted?: number | null
  last_duplicates?: number | null
  last_discarded?: number | null
  last_job_state?: 'done' | 'error' | null
  active_jobs?: ActiveJobInfo[]
}

// prompts-039: one normalizer run-history row from GET /normalizer/runs.
export interface RunHistoryRow {
  id: number
  started_at: string
  trigger: 'manual' | 'schedule' | 'reapply'
  mode: string | null
  proposal_id: number | null
  proposal_name: string | null
  sources: string[]
  status: string
  processed: number
  inserted: number
  errors: number
  warning: string | null
}

export interface IngestResponse {
  inserted: number
  skipped: number
  errors: string[]
  total_read?: number
  duplicates?: number
  discarded?: number
}

export interface RefreshAllItem {
  name: string
  ok: boolean
  inserted?: number
  duplicates?: number
  skipped?: number
  errors?: string[]
  error?: string
}

export interface RefreshAllResult {
  kind: string
  total: number
  succeeded: number
  failed: number
  results: RefreshAllItem[]
}

export interface PreviewResponse {
  preview_id: string
  source_name: string
  format: string
  total: number
  sample: Record<string, unknown>[]
  expires_in_seconds: number
}

// ── Background job tracking ────────────────────────────────────────────────

export type JobState = 'queued' | 'running' | 'done' | 'error'
export type JobStep = 'fetching' | 'parsing' | 'normalising' | 'inserting' | 'done'

export interface Job {
  id: string
  source: string
  kind: string
  state: JobState
  step: JobStep
  processed: number
  total: number
  counters: {
    total_read?: number
    inserted?: number
    duplicates?: number
    discarded?: number
  }
  first_ingest: boolean
  started_at: number
  finished_at: number | null
  error_msg: string | null
}

export interface JobHandle {
  job_id: string
}

export function isJobHandle(x: unknown): x is JobHandle {
  return !!x && typeof x === 'object' && 'job_id' in (x as Record<string, unknown>)
}

export interface FieldDef {
  name: string
  description?: string
  enabled?: boolean
}

export interface FieldsConfig {
  core_fields: FieldDef[]
  custom_fields: FieldDef[]
}

export interface SourceDef {
  name: string
  enabled: boolean
  url: string
  interval_minutes?: number
  headers?: Record<string, string>
  fields?: Record<string, unknown>
}

export interface RemoteJsonSourceDef {
  name: string
  enabled: boolean
  url: string
  continuous: boolean
  interval_minutes?: number
  fields?: Record<string, unknown>
}

// Alias for renamed source type
export type RemoteFeedSourceDef = RemoteJsonSourceDef

// Threat-intel catalogue (prompts-042) — a curated default feed plus live state
export interface ThreatIntelCatalogItem {
  name: string
  title: string
  kind: 'rss_pull' | 'remote_json_pull'
  url: string
  info: string
  default_interval_minutes: number
  enabled: boolean
  continuous: boolean
  interval_minutes: number
}

// Payload sent to bulk-apply catalogue toggles
export interface ThreatIntelToggle {
  name: string
  enabled: boolean
  continuous: boolean
  interval_minutes?: number
}

export interface ListenerConfig {
  enabled: boolean
  fields?: Record<string, unknown>
}

// ── Viewer ─────────────────────────────────────────────────────────────────

export interface EntriesParams {
  source?: string
  search?: string
  severity?: string
  indicator_type?: string
  threat_type?: string
  ingest_mode?: string
  limit?: number
  offset?: number
}

// ── Auth (prompts-045) ───────────────────────────────────────────────────────

export type UserRole = 'admin' | 'normal' | 'sender'

export interface AuthUser {
  id: number
  username: string
  role: UserRole
  enabled: boolean
  created_at?: string
  /**
   * True when the password is a generated default (first-run bootstrap or
   * --reset-admin-password) that must be changed before any other action
   * (prompts-047).
   */
  must_change_password?: boolean
}

export interface CreateUserPayload {
  username: string
  password: string
  role: UserRole
}

/**
 * Password policy published by GET /api/auth/status (prompts-046).
 * The backend (_validate_password) is the source of truth; the SPA mirrors
 * these rules for inline validation only.
 */
export interface PasswordPolicy {
  min_length: number
  required_classes: number
  max_bytes: number
}

export interface AuthStatus {
  auth_enabled: boolean
  password_policy?: PasswordPolicy
}

export const api = {
  // Health
  health: () => request<{ status: string; version: string }>('/health'),

  // Auth (prompts-045)
  auth: {
    status: () => request<AuthStatus>('/auth/status'),
    me: () => request<{ user: AuthUser }>('/auth/me'),
    login: (username: string, password: string) =>
      request<{ user: AuthUser }>('/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      }),
    logout: () => request<{ status: string }>('/auth/logout', { method: 'POST' }),
    changePassword: (current_password: string, new_password: string) =>
      request<{ status: string }>('/auth/password', {
        method: 'PUT',
        body: JSON.stringify({ current_password, new_password }),
      }),
    // Admin: user management
    listUsers: () => request<AuthUser[]>('/auth/users'),
    createUser: (payload: CreateUserPayload) =>
      request<AuthUser>('/auth/users', { method: 'POST', body: JSON.stringify(payload) }),
    setUserRole: (id: number, role: UserRole) =>
      request<AuthUser>(`/auth/users/${id}/role`, {
        method: 'PUT',
        body: JSON.stringify({ role }),
      }),
    setUserEnabled: (id: number, enabled: boolean) =>
      request<AuthUser>(`/auth/users/${id}/enabled`, {
        method: 'PUT',
        body: JSON.stringify({ enabled }),
      }),
    resetUserPassword: (id: number, new_password: string) =>
      request<{ status: string }>(`/auth/users/${id}/password`, {
        method: 'PUT',
        body: JSON.stringify({ new_password }),
      }),
    deleteUser: (id: number) =>
      request<{ status: string; id: number }>(`/auth/users/${id}`, { method: 'DELETE' }),
  },

  // Viewer
  getEntries: (params: EntriesParams = {}) => {
    const q = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') q.set(k, String(v))
    })
    return request<Entry[]>(`/viewer/entries?${q}`)
  },
  getSummary: (opts: { includeActive?: boolean } = {}) =>
    request<SummaryItem[]>(`/viewer/summary${opts.includeActive ? '?include_active=true' : ''}`),

  // Fields
  getFields: () => request<FieldsConfig>('/fields'),
  toggleCoreField: (name: string, enabled: boolean) =>
    request<FieldDef>(`/fields/core/${name}/enabled`, {
      method: 'PUT',
      body: JSON.stringify({ enabled }),
    }),
  addCustomField: (field: FieldDef) =>
    request<FieldDef>('/fields/custom', { method: 'POST', body: JSON.stringify(field) }),
  updateCustomField: (name: string, field: FieldDef) =>
    request<FieldDef>(`/fields/custom/${name}`, { method: 'PUT', body: JSON.stringify(field) }),
  deleteCustomField: (name: string) =>
    request<{ deleted: string }>(`/fields/custom/${name}`, { method: 'DELETE' }),

  // Sources — listener
  getListener: () => request<ListenerConfig>('/sources/listener'),
  updateListener: (body: ListenerConfig) =>
    request<ListenerConfig>('/sources/listener', { method: 'PUT', body: JSON.stringify(body) }),

  // Sources — API pull
  getApiPull: () => request<SourceDef[]>('/sources/api-pull'),
  addApiPull: (source: SourceDef) =>
    request<SourceDef>('/sources/api-pull', { method: 'POST', body: JSON.stringify(source) }),
  updateApiPull: (name: string, source: SourceDef) =>
    request<SourceDef>(`/sources/api-pull/${name}`, { method: 'PUT', body: JSON.stringify(source) }),
  deleteApiPull: (name: string) =>
    request<{ deleted: string }>(`/sources/api-pull/${name}`, { method: 'DELETE' }),

  // Sources — RSS pull
  getRssPull: () => request<SourceDef[]>('/sources/rss-pull'),
  addRssPull: (source: SourceDef) =>
    request<SourceDef>('/sources/rss-pull', { method: 'POST', body: JSON.stringify(source) }),
  updateRssPull: (name: string, source: SourceDef) =>
    request<SourceDef>(`/sources/rss-pull/${name}`, { method: 'PUT', body: JSON.stringify(source) }),
  deleteRssPull: (name: string) =>
    request<{ deleted: string }>(`/sources/rss-pull/${name}`, { method: 'DELETE' }),

  // Sources — Remote JSON pull
  getRemoteJsonPull: () => request<RemoteJsonSourceDef[]>('/sources/remote-json-pull'),
  addRemoteJsonPull: (source: RemoteJsonSourceDef) =>
    request<RemoteJsonSourceDef>('/sources/remote-json-pull', { method: 'POST', body: JSON.stringify(source) }),
  updateRemoteJsonPull: (name: string, source: RemoteJsonSourceDef) =>
    request<RemoteJsonSourceDef>(`/sources/remote-json-pull/${name}`, { method: 'PUT', body: JSON.stringify(source) }),
  deleteRemoteJsonPull: (name: string) =>
    request<{ deleted: string }>(`/sources/remote-json-pull/${name}`, { method: 'DELETE' }),

  // Threat-intel catalogue (prompts-042)
  getThreatIntelCatalog: () =>
    request<ThreatIntelCatalogItem[]>('/sources/threat-intel-catalog'),
  saveThreatIntelSources: (toggles: ThreatIntelToggle[]) =>
    request<ThreatIntelCatalogItem[]>('/sources/threat-intel', {
      method: 'PUT',
      body: JSON.stringify(toggles),
    }),

  // Per-source field config
  getSourceFields: (sourceType: string, name: string) =>
    request<FieldsConfig>(`/sources/${sourceType}/${name}/fields`),
  putSourceFields: (sourceType: string, name: string, config: FieldsConfig) =>
    request<FieldsConfig>(`/sources/${sourceType}/${name}/fields`, { method: 'PUT', body: JSON.stringify(config) }),
  getListenerFields: () => request<FieldsConfig>('/sources/listener/fields'),
  putListenerFields: (config: FieldsConfig) =>
    request<FieldsConfig>('/sources/listener/fields', { method: 'PUT', body: JSON.stringify(config) }),

  // Control
  resetDb: () => request<{ reset: string[]; message: string }>('/control/reset-db', { method: 'POST' }),
  resetSource: (name: string) =>
    request<{ reset: string[]; message: string }>(`/control/reset-source/${name}`, { method: 'POST' }),
  refreshApiPull: (name: string) =>
    request<IngestResponse>(`/control/refresh/api-pull/${name}`, { method: 'POST' }),
  refreshRssPull: (name: string) =>
    request<IngestResponse>(`/control/refresh/rss-pull/${name}`, { method: 'POST' }),
  refreshRemoteJsonPull: (name: string) =>
    request<IngestResponse>(`/control/refresh/remote-json-pull/${name}`, { method: 'POST' }),
  // Alias for renamed remote feed
  refreshRemoteFeedPull: (name: string) =>
    request<IngestResponse>(`/control/refresh/remote-json-pull/${name}`, { method: 'POST' }),
  // Per-section "refresh all" — refresh every configured source of one kind.
  refreshAllApiPull: () =>
    request<RefreshAllResult>('/control/refresh/api-pull', { method: 'POST' }),
  refreshAllRssPull: () =>
    request<RefreshAllResult>('/control/refresh/rss-pull', { method: 'POST' }),
  refreshAllRemoteJsonPull: () =>
    request<RefreshAllResult>('/control/refresh/remote-json-pull', { method: 'POST' }),

  // Scheduler reload
  reloadScheduler: () => request<{ status: string }>('/scheduler/reload', { method: 'POST' }),

  // Ingest — local file upload (multipart) — supports JSON, NDJSON, CSV, XML
  uploadLocalFeed: async (sourceName: string, file: File): Promise<IngestResponse> => {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch(`${BASE}/ingest/local/${sourceName}`, { method: 'POST', body: form, credentials: 'include' })
    if (!res.ok) {
      if (res.status === 401) _notifyUnauthorized()
      throw new Error(`${res.status} ${res.statusText}`)
    }
    return res.json()
  },
  // Back-compat alias
  uploadLocalJson: async (sourceName: string, file: File): Promise<IngestResponse> => {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch(`${BASE}/ingest/local/${sourceName}`, { method: 'POST', body: form, credentials: 'include' })
    if (!res.ok) {
      if (res.status === 401) _notifyUnauthorized()
      throw new Error(`${res.status} ${res.statusText}`)
    }
    return res.json()
  },

  // Preview — two-step local ingest.
  // Accepts an optional onProgress callback (prompts-021A item 2) so the
  // FE can render a real upload-bytes progress bar during the multipart
  // POST. When omitted, behaviour is unchanged from prompts-019.
  previewLocalFeed: async (
    sourceName: string,
    file: File,
    onProgress?: (p: UploadProgress) => void,
  ): Promise<PreviewResponse> => {
    const form = new FormData()
    form.append('file', file)
    return uploadMultipartWithProgress<PreviewResponse>(
      `/ingest/preview/local/${sourceName}`,
      form,
      onProgress,
    )
  },
  confirmPreview: (previewId: string, background: boolean = false) =>
    request<IngestResponse | JobHandle>(
      `/ingest/preview/confirm/${previewId}${background ? '?background=true' : ''}`,
      { method: 'POST' },
    ),

  // Source-preview (pull sources only) — fetch+parse without persisting
  previewApiPullSource: (source: SourceDef) =>
    request<PreviewResponse>('/sources/preview/api-pull', { method: 'POST', body: JSON.stringify(source) }),
  previewRssPullSource: (source: SourceDef) =>
    request<PreviewResponse>('/sources/preview/rss-pull', { method: 'POST', body: JSON.stringify(source) }),
  previewRemoteJsonPullSource: (source: RemoteJsonSourceDef) =>
    request<PreviewResponse>('/sources/preview/remote-json-pull', { method: 'POST', body: JSON.stringify(source) }),
  confirmSourcePreview: (previewId: string, background: boolean = false) =>
    request<IngestResponse | JobHandle>(
      `/sources/preview/confirm/${previewId}${background ? '?background=true' : ''}`,
      { method: 'POST' },
    ),
  cancelSourcePreview: (previewId: string) =>
    request<{ cancelled: boolean }>(`/sources/preview/cancel/${previewId}`, { method: 'POST' }),

  // Ingest — remote URL
  ingestRemote: (sourceName: string, url: string) =>
    request<IngestResponse>(`/ingest/remote/${sourceName}`, {
      method: 'POST',
      body: JSON.stringify({ url }),
    }),

  // Fields — ingest-all toggle
  getIngestAllFields: () => request<{ ingest_all_fields: boolean }>('/fields/ingest-all'),
  setIngestAllFields: (value: boolean) =>
    request<{ ingest_all_fields: boolean }>('/fields/ingest-all', {
      method: 'PUT',
      body: JSON.stringify({ ingest_all_fields: value }),
    }),

  // Fields — flatten depth (prompts-015)
  getFlattenDepth: () => request<{ flatten_max_depth: number }>('/fields/flatten-depth'),
  setFlattenDepth: (value: number) =>
    request<{ flatten_max_depth: number }>('/fields/flatten-depth', {
      method: 'PUT',
      body: JSON.stringify({ flatten_max_depth: value }),
    }),

  // Application — base URL prefix (prompts-017)
  getAppBasePrefix: () => request<{ app_base_prefix: string }>('/app/base-prefix'),
  setAppBasePrefix: (value: string) =>
    request<{ app_base_prefix: string; restart_required: boolean }>('/app/base-prefix', {
      method: 'PUT',
      body: JSON.stringify({ app_base_prefix: value }),
    }),

  // Application — Normalized viewer pagination cap (prompts-043)
  getPaginationMax: () => request<{ pagination_max: number }>('/app/pagination-max'),
  setPaginationMax: (value: number) =>
    request<{ pagination_max: number }>('/app/pagination-max', {
      method: 'PUT',
      body: JSON.stringify({ pagination_max: value }),
    }),

  // Application — per-watcher stored/feed event cap (issue_local_006)
  getWatcherMaxEvents: () => request<{ watcher_max_events: number }>('/app/watcher-max-events'),
  setWatcherMaxEvents: (value: number) =>
    request<{ watcher_max_events: number }>('/app/watcher-max-events', {
      method: 'PUT',
      body: JSON.stringify({ watcher_max_events: value }),
    }),

  // Application — branding logo (prompts-045)
  getLogoInfo: () => request<{ has_logo: boolean }>('/app/logo-info'),
  uploadLogo: async (file: File): Promise<{ logo_path: string; has_logo: boolean }> => {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch(`${BASE}/app/logo`, {
      method: 'POST',
      body: form,
      credentials: 'include',
    })
    if (!res.ok) {
      if (res.status === 401) _notifyUnauthorized()
      throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`)
    }
    return res.json()
  },
  deleteLogo: () => request<{ has_logo: boolean }>('/app/logo', { method: 'DELETE' }),

  // Normalizer
  getNormalizerConfig: () => request<Record<string, unknown>>('/normalizer/config'),
  updateNormalizerConfig: (cfg: Record<string, unknown>) =>
    request<Record<string, unknown>>('/normalizer/config', {
      method: 'PUT',
      body: JSON.stringify(cfg),
    }),
  runNormalizer: () => request<Record<string, unknown>>('/normalizer/run', { method: 'POST' }),
  getNormalizedEntries: (params: { source?: string; search?: string; limit?: number; offset?: number; mapping_version_id?: number } = {}) => {
    const q = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') q.set(k, String(v))
    })
    return request<Record<string, unknown>[]>(`/normalizer/entries?${q}`)
  },
  getNormalizerSummary: () => request<SummaryItem[]>('/normalizer/summary'),
  // prompts-039: normalizer run history (manual / schedule / reapply runs).
  getRunHistory: (limit = 200) =>
    request<RunHistoryRow[]>(`/normalizer/runs?limit=${limit}`),

  // Jobs
  getJob: (id: string) => request<Job>(`/jobs/${id}`),
  listActiveJobs: () => request<Job[]>('/jobs?active=true'),

  // Smart mappings (prompts-021E)
  smartMappings: {
    dryRun: (body: SmartDryRunRequest) =>
      request<SmartDryRunResponse>('/smart-mappings/dry-run', {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    createJob: (body: SmartJobRequest) =>
      request<SmartJobHandle>('/smart-mappings/jobs', {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    getJob: (id: string) => request<Job>(`/smart-mappings/jobs/${id}`),
    listProposals: (params: {
      source?: string
      status?: SmartProposalStatus
      // prompts-021E-4: when omitted, backend defaults to outcome='pending_review'.
      // Pass 'all' to bypass the filter, or a specific outcome value.
      outcome?: SmartProposalOutcome | 'all'
      limit?: number
      // prompts-034: archive filter. 'active' (default) hides archived rows,
      // 'all' shows both, 'only' shows archived rows only.
      archived?: 'active' | 'all' | 'only'
    } = {}) => {
      const q = new URLSearchParams()
      Object.entries(params).forEach(([k, v]) => {
        if (v !== undefined && v !== null && v !== '') q.set(k, String(v))
      })
      const qs = q.toString()
      return request<SmartProposal[]>(`/smart-mappings/proposals${qs ? `?${qs}` : ''}`)
    },
    getProposal: (id: number) => request<SmartProposal>(`/smart-mappings/proposals/${id}`),
    approve: (id: number, body: SmartApproveRequest = {}) =>
      request<SmartApproveResponse>(`/smart-mappings/proposals/${id}/approve`, {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    reject: (id: number, body: SmartRejectRequest = {}) =>
      request<{ proposal_id: number; status: 'rejected' }>(`/smart-mappings/proposals/${id}/reject`, {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    // prompts-032 Phase D: return an operator-rejected proposal to the review
    // queue. 409 when the proposal is not 'rejected' or was auto-discarded.
    reenable: (id: number, body: SmartRejectRequest = {}) =>
      request<{ proposal_id: number; status: 'pending' }>(`/smart-mappings/proposals/${id}/reenable`, {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    // prompts-032 Phase D: the active consolidated mapping summary (or null)
    // backing the active-mapping card above the proposal list.
    getActive: () => request<{ active: ActiveConsolidatedMapping | null }>('/smart-mappings/active'),
    // prompts-038: re-apply the active consolidated mapping on demand — clears
    // and re-normalizes the mapping's feeds, then runs the normalizer. Returns
    // the run counters (processed/inserted/errors) plus reset_rows.
    runActive: () =>
      request<{ reset_rows: number; processed?: number; inserted?: number; errors?: number }>(
        '/smart-mappings/active/run',
        { method: 'POST' },
      ),
    // prompts-034: archive a proposal (any status). Hides it from default
    // views without deleting it. Optional note recorded for provenance.
    archive: (id: number, body: { note?: string } = {}) =>
      request<{ proposal_id: number; archived: true }>(`/smart-mappings/proposals/${id}/archive`, {
        method: 'POST',
        body: JSON.stringify(body),
      }),
  },

  // LLM providers (prompts-021D-2, expanded in 022 step 5)
  llm: {
    getConfig: () => request<LLMConfig>('/llm/config'),
    /**
     * Update the top-level LLM toggles only.
     *
     * 022 step 4 narrowed the backend: PUT /api/llm/config now accepts
     * ONLY {enabled, default_provider}; the providers list is managed via
     * addProvider / updateProvider / deleteProvider below. Sending any
     * other key (including the historical 'providers') returns 400.
     *
     * The parameter type accepts the legacy full :type:`LLMConfig` shape
     * as well so call sites that still build the wide payload keep
     * compiling until Step 7 swaps them over; the backend will reject
     * any extra keys at runtime.
     */
    setConfig: (cfg: LLMConfigUpdate | LLMConfig) =>
      request<LLMConfig>('/llm/config', {
        method: 'PUT',
        body: JSON.stringify(cfg),
      }),
    listProviders: () => request<LLMProviderSummary[]>('/llm/providers'),
    /**
     * Append a new provider to llm-providers.yaml. Backend enforces the
     * identifier regex (^[A-Za-z0-9_-]{1,40}$) and uniqueness against
     * existing names. 400 on validation error.
     */
    addProvider: (provider: LLMProviderConfig) =>
      request<LLMProviderSummary>('/llm/providers', {
        method: 'POST',
        body: JSON.stringify(provider),
      }),
    /**
     * Replace one provider in-place. The path name is authoritative; a
     * body name that differs is ignored. Write-only api_key semantics
     * still apply: send "***" (or omit) to retain the stored key.
     */
    updateProvider: (name: string, provider: LLMProviderConfig) =>
      request<LLMProviderSummary>(
        `/llm/providers/${encodeURIComponent(name)}`,
        { method: 'PUT', body: JSON.stringify(provider) },
      ),
    /** Delete a provider; clears default_provider when it pointed here. */
    deleteProvider: (name: string) =>
      request<void>(`/llm/providers/${encodeURIComponent(name)}`, {
        method: 'DELETE',
      }),
    /**
     * Test an already-persisted provider. Returns the canonical
     * run_provider_test transcript (status + details[] + models + sample).
     * Provider/transport errors are captured into details[] with
     * aggregate status='error' (NOT raised as HTTP 502).
     */
    testProvider: (name: string) =>
      request<LLMTestRunResult>(
        `/llm/providers/${encodeURIComponent(name)}/test`,
        { method: 'POST' },
      ),
    /**
     * Test a *draft* provider that has not been persisted yet. Used by
     * the Add LLM wizard so the operator can validate config before
     * clicking 'Add LLM'. The YAML file is never touched. Same canonical
     * transcript shape as testProvider.
     */
    testProviderDraft: (provider: LLMProviderConfig) =>
      request<LLMTestRunResult>('/llm/providers/test', {
        method: 'POST',
        body: JSON.stringify(provider),
      }),
    /**
     * prompts-027: Discover models ONLY against a draft provider (no
     * probe). Returns LLMDiscoverResult. Used by the Add Provider
     * wizard's "Connect to provider" button (stage 2).
     */
    discoverDraft: (provider: LLMProviderConfig) =>
      request<LLMDiscoverResult>('/llm/providers/discover', {
        method: 'POST',
        body: JSON.stringify(provider),
      }),
    /**
     * prompts-027: Discover models ONLY against an already-persisted
     * provider. Used by the persisted ProviderCard's "Discover Models"
     * button to refresh the per-provider list. The caller is responsible
     * for persisting the returned ``models`` to ``available_models``
     * via :func:`updateProvider`.
     */
    discoverProvider: (name: string) =>
      request<LLMDiscoverResult>(
        `/llm/providers/${encodeURIComponent(name)}/discover`,
        { method: 'POST' },
      ),
  },

  // Mapping versions (prompts-021F)
  mappings: {
    listVersions: (source?: string) => {
      const q = source ? `?source=${encodeURIComponent(source)}` : ''
      return request<MappingVersion[]>(`/normalizer/mappings/versions${q}`)
    },
    getVersion: (id: number) =>
      request<MappingVersionDetail>(`/normalizer/mappings/versions/${id}`),
    activate: (id: number) =>
      request<MappingVersionActivateResponse>(
        `/normalizer/mappings/versions/${id}/activate`,
        { method: 'POST' },
      ),
    diff: (fromId: number, toId: number) =>
      request<MappingVersionDiffResponse>(
        `/normalizer/mappings/diff?from=${fromId}&to=${toId}`,
      ),
  },

  // Watchers (issue_local_006) — admin CRUD + triggered-event reader.
  watchers: {
    list: () => request<Watcher[]>('/watchers'),
    get: (id: string) => request<Watcher>(`/watchers/${encodeURIComponent(id)}`),
    create: (body: WatcherInput) =>
      request<Watcher>('/watchers', { method: 'POST', body: JSON.stringify(body) }),
    update: (id: string, body: WatcherInput) =>
      request<Watcher>(`/watchers/${encodeURIComponent(id)}`, {
        method: 'PUT',
        body: JSON.stringify(body),
      }),
    setEnabled: (id: string, enabled: boolean) =>
      request<Watcher>(`/watchers/${encodeURIComponent(id)}/enabled`, {
        method: 'PUT',
        body: JSON.stringify({ enabled }),
      }),
    remove: (id: string) =>
      request<void>(`/watchers/${encodeURIComponent(id)}`, { method: 'DELETE' }),
    events: (id: string, params: { limit?: number; offset?: number } = {}) => {
      const q = new URLSearchParams()
      Object.entries(params).forEach(([k, v]) => {
        if (v !== undefined && v !== null) q.set(k, String(v))
      })
      const qs = q.toString()
      return request<WatcherEventsPage>(
        `/watchers/${encodeURIComponent(id)}/events${qs ? `?${qs}` : ''}`,
      )
    },
    metaFeeds: () => request<{ feeds: string[] }>('/watchers/meta/feeds'),
    metaFields: (dataset: WatcherDataset = 'all') =>
      request<{ fields: string[] }>(
        `/watchers/meta/fields?dataset=${encodeURIComponent(dataset)}`,
      ),
  },
}

// ── Smart mappings types ───────────────────────────────────────────────────

export type SmartProposalStatus = 'pending' | 'approved' | 'rejected' | 'error'

export type SmartProposalOutcome =
  | 'pending_review'
  | 'auto_applied'
  | 'discarded_below_threshold'
  | 'approved'
  | 'rejected'
  | 'error'

export type SmartTriggerReason = 'manual' | 'schedule' | 'on_new_feed'

// prompts-032: scope of raw fields presented to the LLM for a consolidated
// request. 'configured' = only fields enabled in feed-fields.yaml.
export type SmartFieldScope = 'all' | 'configured'

export interface SmartScoreBreakdown {
  coverage_before: number
  coverage_after: number
  coverage_delta: number
  raw_field_population?: Record<string, number>
}

export interface SmartDryRunRequest {
  source: string
  sample_size?: number
}

export interface SmartDryRunResponse {
  source: string
  sample_size: number
  raw_fields: string[]
  canonical_fields: string[]
  prompt_system: string
  prompt_user: string
}

export interface SmartJobRequest {
  // prompts-032: consolidated/global manual flow. One request → one proposal
  // spanning the selected feeds.
  sources: string[]
  provider?: string
  // prompts-034: optional per-proposal model override. When omitted the
  // provider's configured default model is used. prompts-036: the UI offers
  // each provider's DISCOVERED models (provider.available_models) — a green
  // Test Connection is no longer required.
  model?: string
  sample_size?: number
  field_scope?: SmartFieldScope
}

export interface SmartJobHandle {
  job_id: string
  sources: string[]
  field_scope: SmartFieldScope
  // prompts-034: echoes the chosen provider (null = configured default).
  provider?: string | null
  // prompts-034: echoes the clamped sample size used for the job.
  sample_size?: number
  // prompts-034: echoes the chosen model override (null = provider default).
  model?: string | null
  state: string
}

export interface SmartProposal {
  id: number
  source_name: string
  provider_name: string | null
  model: string | null
  sample_size: number
  raw_fields: string[]
  mapping: Record<string, string>
  prompt_system: string
  prompt_user: string
  llm_response_raw: string
  // prompts-037: raw LLM HTTP exchange for the error/detail card.
  // `llm_request_raw` is the full request (method/url/redacted headers/body);
  // `llm_response_json` is the WHOLE HTTP response envelope (not just the
  // extracted `llm_response_raw` content). Absent on pre-v6 rows.
  llm_request_raw?: string
  llm_response_json?: string
  status: SmartProposalStatus
  created_at: string
  decided_at: string | null
  decided_by_note: string | null
  // prompts-021E-3 / 021E-4 fields
  trigger_reason?: SmartTriggerReason
  score?: number | null
  score_breakdown?: SmartScoreBreakdown | null
  outcome?: SmartProposalOutcome
  auto_applied?: boolean
  // prompts-021G follow-up: id of the mapping_versions row produced by
  // approve / auto-apply. NULL/undefined for pending/rejected/discarded
  // proposals; the Activity tab uses this to deep-link to Mapping versions.
  mapping_version_id?: number | null
  // prompts-032: consolidated (multi-feed) proposals. source_name is the
  // sentinel "__consolidated__"; the real feed list lives in `sources`.
  sources?: string[]
  field_scope?: SmartFieldScope
  consolidated_version_id?: number | null
  // prompts-034: proposal lifecycle. `proposal_name` is a stable human-facing
  // label ("Proposal-<UTC timestamp>"); absent on pre-v5 rows (fall back to
  // a synthesised label). `archived` hides the row from default views.
  proposal_name?: string
  archived?: boolean
}

export interface SmartApproveRequest {
  note?: string
  set_mode_manual?: boolean
}

// prompts-032 Phase D: summary of the single active consolidated mapping,
// surfaced by GET /api/smart-mappings/active and rendered as the active card.
export interface ActiveConsolidatedMapping {
  id: number
  sources: string[]
  field_count: number
  field_scope: SmartFieldScope | null
  proposal_id: number | null
  proposal_name?: string | null
  created_at: string
  note: string | null
  // prompts-039: the full {raw_field: canonical} map, for the expanded card.
  mapping?: Record<string, string>
}

export interface SmartApproveResponse {
  proposal_id: number
  source: string
  added: Array<{ raw_field: string; canonical: string }>
  skipped_conflicts: Array<{
    raw_field: string
    existing_canonical: string
    proposal_canonical: string
  }>
  mode: string
  mode_changed: boolean
  hint?: string
  // prompts-021F additions
  mapping_version_id?: number
  reset_rows?: number
  auto_applied?: boolean
  outcome?: SmartProposalOutcome
}

export interface SmartRejectRequest {
  note?: string
}

// ── LLM provider types (prompts-021D-2, expanded in 022 step 5) ────────────

export type LLMProviderKind = 'openai' | 'anthropic' | 'ollama' | 'openai_compatible'

export interface LLMProvider {
  name: string
  kind: LLMProviderKind
  base_url?: string
  /**
   * Write-only. GET responses return "***" when a key is set, "" otherwise.
   * On PUT, send "***" to retain the stored key; any other string replaces it.
   * Never bind this directly to a visible input value.
   */
  api_key?: string
  model?: string
  timeout_seconds?: number
  max_retries?: number
  skip_tls_verify?: boolean
  /**
   * prompts-027: persisted list of models last returned by the
   * "Discover Models" button on the persisted ProviderCard. Lets the
   * default-model dropdown render on first paint without forcing the
   * operator to click Discover every time the page loads. Not a secret;
   * never redacted. Absent / [] on legacy records.
   */
  available_models?: string[]
}

/**
 * Shape of the body accepted by POST /api/llm/providers and
 * PUT /api/llm/providers/{name} (022 step 4). Same fields as
 * :type:`LLMProvider`; named separately so call-sites can be explicit
 * about whether they hold a redacted-from-disk record or a
 * to-be-persisted request body.
 */
export type LLMProviderConfig = LLMProvider

export interface LLMConfig {
  enabled: boolean
  default_provider: string | null
  providers: LLMProvider[]
}

/**
 * Subset of LLMConfig accepted by PUT /api/llm/config after 022 step 4.
 * The providers list moved to dedicated CRUD endpoints; sending it here
 * is rejected by the backend with 400.
 */
export interface LLMConfigUpdate {
  enabled?: boolean
  default_provider?: string | null
}

export interface LLMProviderSummary {
  name: string
  kind: LLMProviderKind
  model: string | null
  has_api_key: boolean
  skip_tls_verify: boolean
  // prompts-034: models that have a successful Test Connection on record for
  // this provider. Still recorded on a green probe but, as of prompts-036, no
  // longer the proposal-dropdown source. Default [] on legacy records.
  tested_models?: string[]
  // prompts-036: discovered model catalog for this provider. Drives the
  // per-proposal model dropdown in Smart Mappings (a green Test is no longer
  // required — bad models surface at proposal request/response time). Default
  // [] on providers that have not been discovered yet.
  available_models?: string[]
}

/**
 * One row of the Test Connection transcript produced by
 * backend/llm/test_runner.run_provider_test. Rendered by the Test
 * Details modal (022 step 6). Every field is best-effort: a step that
 * never made it to the wire still has its label, error, and
 * duration_ms populated, but url/status_code may be null.
 */
export interface LLMTestStepDetail {
  step: string
  method: string | null
  url: string | null
  headers_redacted: Record<string, string> | null
  request_body: string | null
  status_code: number | null
  response_body: string | null
  duration_ms: number
  error: string | null
  // prompts-061: non-blocking advisory for a step that otherwise succeeded
  // (error === null). e.g. the model catalog was empty but the completion
  // probe passed. Optional for backward compatibility with older payloads.
  warning?: string | null
}

/**
 * Canonical Test Connection response shape returned by both
 * POST /api/llm/providers/{name}/test and POST /api/llm/providers/test
 * (022 step 4). Always 200 OK from the HTTP layer; aggregate failure
 * is signalled by `status === 'error'` and surfaced through `details[]`.
 */
export interface LLMTestRunResult {
  status: 'ok' | 'error'
  details: LLMTestStepDetail[]
  models: string[] | null
  sample: string | null
}

/**
 * prompts-027: response shape of POST /api/llm/providers/discover and
 * POST /api/llm/providers/{name}/discover. Same as
 * :type:`LLMTestRunResult` minus the ``sample`` field — discover does
 * NOT run the ``complete`` smoke probe.
 */
export interface LLMDiscoverResult {
  status: 'ok' | 'error'
  details: LLMTestStepDetail[]
  models: string[] | null
}

/**
 * issue_local_02: build a minimal LLMTestRunResult from a thrown error string
 * (network/4xx/5xx that never produced a structured payload) so the
 * "View test details" link + TestDetailsModal can open on error-only paths,
 * not just when the server returned a transcript. A single synthetic step
 * carries the error message.
 */
export function synthesizeErrorTestResult(
  error: string,
  step = 'test',
): LLMTestRunResult {
  return {
    status: 'error',
    details: [
      {
        step,
        method: null,
        url: null,
        headers_redacted: null,
        request_body: null,
        status_code: null,
        response_body: null,
        duration_ms: 0,
        error,
        warning: null,
      },
    ],
    models: null,
    sample: null,
  }
}

/**
 * Legacy Test Connection shape kept ONLY so existing call sites in
 * LLMProvidersTab + its tests keep compiling until Steps 6+7 swap them
 * over to :type:`LLMTestRunResult`. Do NOT use in new code.
 *
 * @deprecated 022 step 5 — replaced by LLMTestRunResult; will be
 * removed once the tab refactor in step 7 lands.
 */
export interface LLMTestResult {
  status: 'ok'
  method: 'list_models' | 'complete'
  models?: string[]
  sample?: string
}

// ── Mapping versions (prompts-021F) ────────────────────────────────────────

export type MappingVersionOrigin = 'migration' | 'proposal' | 'manual'

export interface MappingVersion {
  id: number
  source_name: string
  origin: MappingVersionOrigin
  source_proposal_id: number | null
  active: number | boolean
  created_at: string
  note: string | null
  mapping: Record<string, string>
}

export interface MappingVersionDiff {
  added: Array<{ raw_field: string; canonical: string }>
  removed: Array<{ raw_field: string; canonical: string }>
  changed: Array<{ raw_field: string; from: string; to: string }>
}

export interface MappingVersionDetail {
  version: MappingVersion
  active: MappingVersion | null
  diff: MappingVersionDiff
}

export interface MappingVersionActivateResponse {
  version_id: number
  source: string
  reset_rows: number
}

export interface MappingVersionDiffResponse {
  from: { id: number; source_name: string }
  to: { id: number; source_name: string }
  diff: MappingVersionDiff
}

// ── Watchers (issue_local_006) ─────────────────────────────────────────────

export type WatcherSeverity = 'low' | 'medium' | 'high' | 'critical'
export type WatcherDataset = 'all' | 'raw' | 'normalized'
export type WatcherMode = 'realtime' | 'scheduled'
export type WatcherFormat = 'json' | 'csv' | 'xml'
export type WatcherMatchType = 'exact' | 'wildcard' | 'regex'

export interface WatcherCondition {
  field: string
  value: string
  match_type: WatcherMatchType
}

export interface WatcherInput {
  name: string
  severity: WatcherSeverity
  dataset: WatcherDataset
  feeds: string[]
  conditions: WatcherCondition[]
  mode: WatcherMode
  interval_sec: number
  format: WatcherFormat
  max_feed_events: number
  enabled: boolean
}

export interface Watcher extends WatcherInput {
  id: string
  trigger_count: number
  created_at: string
  updated_at: string
}

export interface WatcherEvent {
  id: number
  watcher_id: string
  dataset: string
  source_entry_id: number
  source_name: string | null
  triggered_at: string
  event: Record<string, unknown>
}

export interface WatcherEventsPage {
  events: WatcherEvent[]
  total: number
}
