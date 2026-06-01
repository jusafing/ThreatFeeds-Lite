/**
 * Shared in-flight smart-proposal job state (prompts-049).
 *
 * A newly requested Smart Mapping proposal runs as a background job that only
 * becomes a persisted proposal row once it finishes. The "Processing" row that
 * represents the running job used to live in component-local `useState` in
 * SmartMappings, so navigating to another section unmounted the page and lost
 * it — on return the job was invisible until (and unless) it happened to land
 * as a real proposal row.
 *
 * Like `useNormalizerRun` (which moved run state into the app-global cache so it
 * survives a route/sub-tab unmount), this hook keeps the active job handles in
 * the app-global React Query cache under SMART_ACTIVE_JOBS_KEY with
 * staleTime/gcTime: Infinity. Because the QueryClient is created once in
 * main.tsx, the entry is not garbage-collected when SmartMappings unmounts, so
 * the handles — and therefore the Processing rows and their polling — are
 * reconstructed on return.
 *
 * Scope is the SPA session: the state survives in-app navigation but not a full
 * browser reload (matching the reported scenario). A job that finishes while the
 * user is away is healed on return — the ProcessingRow polls GET /jobs/{id} once,
 * sees the terminal state (or a 404 once the server-side job TTL evicts it), and
 * removes itself while refreshing the proposals list.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query'
import type { SmartJobHandle } from '../api/client'

export const SMART_ACTIVE_JOBS_KEY = ['smart-active-jobs'] as const

export interface SmartActiveJobs {
  /** Currently-tracked in-flight (or just-failed) job handles, newest first. */
  activeJobs: SmartJobHandle[]
  /** Track a newly started job (deduped by job_id). */
  add: (handle: SmartJobHandle) => void
  /** Stop tracking a job (on success, manual dismiss, or self-heal). */
  remove: (jobId: string) => void
}

export function useSmartActiveJobs(): SmartActiveJobs {
  const qc = useQueryClient()

  const { data = [] } = useQuery<SmartJobHandle[]>({
    queryKey: SMART_ACTIVE_JOBS_KEY,
    // No network fetch — this is a client-only store. The queryFn just returns
    // whatever is already cached so the entry exists and components subscribe.
    queryFn: () => qc.getQueryData<SmartJobHandle[]>(SMART_ACTIVE_JOBS_KEY) ?? [],
    initialData: [],
    staleTime: Infinity,
    gcTime: Infinity,
  })

  const add = (handle: SmartJobHandle) =>
    qc.setQueryData<SmartJobHandle[]>(SMART_ACTIVE_JOBS_KEY, (prev = []) => [
      handle,
      ...prev.filter((j) => j.job_id !== handle.job_id),
    ])

  const remove = (jobId: string) =>
    qc.setQueryData<SmartJobHandle[]>(SMART_ACTIVE_JOBS_KEY, (prev = []) =>
      prev.filter((j) => j.job_id !== jobId),
    )

  return { activeJobs: data, add, remove }
}
