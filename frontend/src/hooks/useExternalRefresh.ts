/**
 * Shared External-sources refresh state (prompts-060).
 *
 * Every "Refresh" action in the External sources sections — per-source refresh
 * (API pull, RSS pull, Remote JSON pull) and the per-section "Refresh all"
 * button — goes through a react-query mutation keyed in the app-global
 * MutationCache instead of component-local `useState`.
 *
 * Why a shared mutation instead of component-local `useState`:
 *   - The Configuration page conditionally renders only the active tab, so
 *     switching tabs (or navigating to another section) unmounts the control
 *     and previously destroyed its `refreshing` state — the spinner vanished
 *     even though the synchronous backend POST was still running. A mutation in
 *     the global MutationCache keeps its in-flight state across unmount, so a
 *     re-mounted button re-derives "am I still refreshing?" from the cache and
 *     the indicator re-appears.
 *
 * This mirrors the normalizer-run pattern in useNormalizerRun.ts (prompts-040).
 *
 * Cache invalidation after a refresh settles is registered once via
 * `setMutationDefaults` in main.tsx (see EXTERNAL_REFRESH_INVALIDATE) so the
 * affected section list refreshes even if the component that started the
 * refresh has already unmounted.
 */
import { useMutation, useMutationState } from '@tanstack/react-query'
import { api, IngestResponse, RefreshAllResult } from '../api/client'

/** Source kinds that expose a manual refresh. */
export type RefreshKind = 'api-pull' | 'rss-pull' | 'remote-json-pull'

/** Shared mutation key prefix for every per-source refresh. */
export const EXTERNAL_REFRESH_KEY = ['external-refresh'] as const
/** Shared mutation key prefix for every per-section "Refresh all". */
export const EXTERNAL_REFRESH_ALL_KEY = ['external-refresh-all'] as const

/** Variables carried by a per-source refresh mutation. */
export interface SourceRefreshVars {
  kind: RefreshKind
  name: string
}

/** Variables carried by a refresh-all mutation. */
export interface RefreshAllVars {
  kind: RefreshKind
}

/** Stable identity for a single refreshing source: `${kind}:${name}`. */
export function refreshId(kind: RefreshKind, name: string): string {
  return `${kind}:${name}`
}

/**
 * Section query key invalidated when a refresh of the given kind settles.
 * Consumed by the `setMutationDefaults` registrations in main.tsx.
 */
export const EXTERNAL_REFRESH_INVALIDATE: Record<RefreshKind, readonly string[]> = {
  'api-pull': ['api-pull'],
  'rss-pull': ['rss-pull'],
  'remote-json-pull': ['remote-json-pull'],
}

const PER_SOURCE_FN: Record<RefreshKind, (name: string) => Promise<IngestResponse>> = {
  'api-pull': api.refreshApiPull,
  'rss-pull': api.refreshRssPull,
  'remote-json-pull': api.refreshRemoteJsonPull,
}

const REFRESH_ALL_FN: Record<RefreshKind, () => Promise<RefreshAllResult>> = {
  'api-pull': api.refreshAllApiPull,
  'rss-pull': api.refreshAllRssPull,
  'remote-json-pull': api.refreshAllRemoteJsonPull,
}

/**
 * Per-source manual refresh. The mutation lives in the global cache under
 * `['external-refresh', kind, name]`, so its in-flight state survives unmount.
 */
export function useSourceRefresh() {
  return useMutation<IngestResponse, Error, SourceRefreshVars>({
    mutationKey: [...EXTERNAL_REFRESH_KEY],
    mutationFn: ({ kind, name }) => PER_SOURCE_FN[kind](name),
  })
}

/**
 * Set of currently-refreshing source identities (`${kind}:${name}`), derived
 * from the global MutationCache. A button consults this instead of a local
 * `refreshing` flag, so the spinner re-appears after re-mount while the POST is
 * still pending.
 */
export function useRefreshingSources(): Set<string> {
  const pending = useMutationState<SourceRefreshVars | undefined>({
    filters: { mutationKey: [...EXTERNAL_REFRESH_KEY], status: 'pending' },
    select: (m) => m.state.variables as SourceRefreshVars | undefined,
  })
  const ids = new Set<string>()
  for (const vars of pending) {
    if (vars) ids.add(refreshId(vars.kind, vars.name))
  }
  return ids
}

/**
 * Per-section "Refresh all". Keyed `['external-refresh-all', kind]` so the
 * "Refreshing…" indicator and the resulting summary survive unmount.
 */
export function useRefreshAll() {
  return useMutation<RefreshAllResult, Error, RefreshAllVars>({
    mutationKey: [...EXTERNAL_REFRESH_ALL_KEY],
    mutationFn: ({ kind }) => REFRESH_ALL_FN[kind](),
  })
}

/**
 * True while a "Refresh all" of the given kind is in flight anywhere — used to
 * keep the button busy/disabled across unmount.
 */
export function useRefreshAllBusy(kind: RefreshKind): boolean {
  const pending = useMutationState<RefreshAllVars | undefined>({
    filters: { mutationKey: [...EXTERNAL_REFRESH_ALL_KEY], status: 'pending' },
    select: (m) => m.state.variables as RefreshAllVars | undefined,
  })
  return pending.some((vars) => vars?.kind === kind)
}

/**
 * The most recent settled "Refresh all" summary for the given kind, re-derived
 * from the global cache so the summary persists after navigating back.
 * Returns the summary and any error message from the latest matching mutation.
 */
export function useRefreshAllResult(kind: RefreshKind): {
  summary: RefreshAllResult | null
  error: string | null
} {
  const entries = useMutationState<{
    vars: RefreshAllVars | undefined
    data: RefreshAllResult | undefined
    error: Error | null
    status: string
  }>({
    filters: { mutationKey: [...EXTERNAL_REFRESH_ALL_KEY] },
    select: (m) => ({
      vars: m.state.variables as RefreshAllVars | undefined,
      data: m.state.data as RefreshAllResult | undefined,
      error: m.state.error,
      status: m.state.status,
    }),
  })
  // Last matching settled (success or error) entry wins.
  for (let i = entries.length - 1; i >= 0; i--) {
    const e = entries[i]
    if (e.vars?.kind !== kind) continue
    if (e.status === 'success' && e.data) return { summary: e.data, error: null }
    if (e.status === 'error') return { summary: null, error: e.error?.message ?? 'Refresh failed' }
  }
  return { summary: null, error: null }
}
