import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { api, Job } from '../api/client'

/**
 * Poll a background ingest job until it reaches a terminal state.
 *
 * - Polls every 1 s while queued/running.
 * - Stops polling on done/error.
 * - Invalidates the summary query when the job completes so the
 *   per-source row refreshes automatically.
 */
export function useJobProgress(jobId: string | null | undefined) {
  const qc = useQueryClient()

  const query = useQuery<Job>({
    queryKey: ['job', jobId],
    queryFn: () => api.getJob(jobId as string),
    enabled: !!jobId,
    refetchInterval: q => {
      const data = q.state.data
      if (!data) return 1000
      return data.state === 'done' || data.state === 'error' ? false : 1000
    },
  })

  useEffect(() => {
    if (!query.data) return
    if (query.data.state === 'done' || query.data.state === 'error') {
      qc.invalidateQueries({ queryKey: ['summary'] })
    }
  }, [query.data?.state, qc, query.data])

  return query
}
