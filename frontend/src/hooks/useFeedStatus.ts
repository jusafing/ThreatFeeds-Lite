/**
 * Per-feed live status for the "load on enable" flow (issue #1).
 *
 * When a feed is enabled it gets an immediate background pull wrapped in a
 * job_store Job (see backend/ingestion/refresh.py). This hook surfaces that as
 * a per-source status so the UI can render a spinner while the first pull runs,
 * a green "ready" marker once it has ingested, and a red "error" marker if it
 * failed.
 *
 * Two sources of truth are combined because neither alone is sufficient before
 * a source has its own DB file:
 *
 * - GET /jobs?active=true  → reports the in-flight pull ("pulling") even on the
 *   very first run, before any summary row exists.
 * - GET /viewer/summary    → persists the last terminal outcome
 *   (last_job_state / last_ingested_at) so "ready" / "error" survive a reload
 *   and remain visible after the job has been evicted.
 *
 * Polling is adaptive: fast while any pull is in flight, slow when idle, so an
 * enabled-but-quiet catalogue does not generate constant traffic.
 */
import { useQuery } from '@tanstack/react-query'
import { api, type Job, type SummaryItem } from '../api/client'

export type FeedStatus = 'idle' | 'pulling' | 'ready' | 'error'

const ACTIVE_POLL_MS = 2000
const IDLE_POLL_MS = 20000

export interface FeedStatusMap {
  /** Resolve the live status for a single source name. */
  statusFor: (name: string) => FeedStatus
  /** True while any tracked pull is in flight. */
  anyPulling: boolean
}

function isActive(job: Job): boolean {
  return job.state === 'queued' || job.state === 'running'
}

export function useFeedStatus(): FeedStatusMap {
  const jobsQuery = useQuery({
    queryKey: ['active-jobs'],
    queryFn: api.listActiveJobs,
    refetchInterval: (query) => {
      const jobs = (query.state.data as Job[] | undefined) ?? []
      return jobs.some(isActive) ? ACTIVE_POLL_MS : IDLE_POLL_MS
    },
  })

  const summaryQuery = useQuery({
    queryKey: ['viewer-summary', 'feed-status'],
    queryFn: () => api.getSummary({ includeActive: false }),
    refetchInterval: () => {
      const jobs = (jobsQuery.data as Job[] | undefined) ?? []
      return jobs.some(isActive) ? ACTIVE_POLL_MS : IDLE_POLL_MS
    },
  })

  const activeJobs = (jobsQuery.data ?? []) as Job[]
  const summary = (summaryQuery.data ?? []) as SummaryItem[]

  const pulling = new Set(activeJobs.filter(isActive).map((j) => j.source))
  const summaryByName = new Map(summary.map((s) => [s.source, s]))

  const statusFor = (name: string): FeedStatus => {
    if (pulling.has(name)) return 'pulling'
    const row = summaryByName.get(name)
    if (!row) return 'idle'
    if (row.last_job_state === 'error') return 'error'
    if (row.last_job_state === 'done' || row.last_ingested_at) return 'ready'
    return 'idle'
  }

  return { statusFor, anyPulling: pulling.size > 0 }
}
