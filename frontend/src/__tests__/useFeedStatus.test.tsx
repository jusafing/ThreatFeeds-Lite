/**
 * Tests for the per-feed live status hook (issue #1).
 *
 * useFeedStatus merges two sources of truth — GET /jobs?active=true (in-flight
 * pulls) and GET /viewer/summary (last terminal outcome) — into a single
 * `statusFor(name)` resolver used by the per-feed status markers. These tests
 * pin the resolution priority and the `anyPulling` flag:
 *
 *   - an active job → 'pulling' (overrides any summary row)
 *   - summary last_job_state 'error' → 'error'
 *   - summary 'done' / last_ingested_at → 'ready'
 *   - neither → 'idle'
 */
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, it, expect, vi, beforeEach } from 'vitest'

import type { Job, SummaryItem } from '../api/client'

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...actual,
    api: {
      listActiveJobs: vi.fn(),
      getSummary: vi.fn(),
    },
  }
})

import { api } from '../api/client'
import { useFeedStatus } from '../hooks/useFeedStatus'

function makeJob(o: Partial<Job> = {}): Job {
  return {
    id: 'j1',
    source: 'feed-a',
    kind: 'remote_json',
    state: 'running',
    step: 'fetching',
    processed: 0,
    total: 0,
    counters: {},
    first_ingest: true,
    started_at: 0,
    finished_at: null,
    error_msg: null,
    ...o,
  }
}

function makeSummary(o: Partial<SummaryItem> = {}): SummaryItem {
  return {
    source: 'feed-a',
    count: 0,
    ...o,
  }
}

// Renders the resolved status + anyPulling for a fixed source name.
function Probe({ name }: { name: string }) {
  const { statusFor, anyPulling } = useFeedStatus()
  return (
    <div>
      <span data-testid="status">{statusFor(name)}</span>
      <span data-testid="any">{anyPulling ? 'yes' : 'no'}</span>
    </div>
  )
}

function renderProbe(name = 'feed-a') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <Probe name={name} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.listActiveJobs).mockReset()
  vi.mocked(api.getSummary).mockReset()
})

describe('useFeedStatus', () => {
  it('reports idle when there is no job and no summary row', async () => {
    vi.mocked(api.listActiveJobs).mockResolvedValue([])
    vi.mocked(api.getSummary).mockResolvedValue([])
    renderProbe()
    await waitFor(() =>
      expect(screen.getByTestId('status')).toHaveTextContent('idle'),
    )
    expect(screen.getByTestId('any')).toHaveTextContent('no')
  })

  it('reports pulling while a job is running, and sets anyPulling', async () => {
    vi.mocked(api.listActiveJobs).mockResolvedValue([makeJob({ state: 'running' })])
    vi.mocked(api.getSummary).mockResolvedValue([])
    renderProbe()
    await waitFor(() =>
      expect(screen.getByTestId('status')).toHaveTextContent('pulling'),
    )
    expect(screen.getByTestId('any')).toHaveTextContent('yes')
  })

  it('treats a queued job as pulling too', async () => {
    vi.mocked(api.listActiveJobs).mockResolvedValue([makeJob({ state: 'queued' })])
    vi.mocked(api.getSummary).mockResolvedValue([])
    renderProbe()
    await waitFor(() =>
      expect(screen.getByTestId('status')).toHaveTextContent('pulling'),
    )
  })

  it('an active job overrides a summary row (pulling wins over ready)', async () => {
    vi.mocked(api.listActiveJobs).mockResolvedValue([makeJob({ state: 'running' })])
    vi.mocked(api.getSummary).mockResolvedValue([
      makeSummary({ last_job_state: 'done', last_ingested_at: '2026-01-01T00:00:00Z' }),
    ])
    renderProbe()
    await waitFor(() =>
      expect(screen.getByTestId('status')).toHaveTextContent('pulling'),
    )
  })

  it('reports ready when the last job state is done', async () => {
    vi.mocked(api.listActiveJobs).mockResolvedValue([])
    vi.mocked(api.getSummary).mockResolvedValue([makeSummary({ last_job_state: 'done' })])
    renderProbe()
    await waitFor(() =>
      expect(screen.getByTestId('status')).toHaveTextContent('ready'),
    )
  })

  it('reports ready when last_ingested_at is present even without a job state', async () => {
    vi.mocked(api.listActiveJobs).mockResolvedValue([])
    vi.mocked(api.getSummary).mockResolvedValue([
      makeSummary({ last_ingested_at: '2026-01-01T00:00:00Z' }),
    ])
    renderProbe()
    await waitFor(() =>
      expect(screen.getByTestId('status')).toHaveTextContent('ready'),
    )
  })

  it('reports error when the last job state is error', async () => {
    vi.mocked(api.listActiveJobs).mockResolvedValue([])
    vi.mocked(api.getSummary).mockResolvedValue([makeSummary({ last_job_state: 'error' })])
    renderProbe()
    await waitFor(() =>
      expect(screen.getByTestId('status')).toHaveTextContent('error'),
    )
  })

  it('resolves idle for a name with no matching job or summary row', async () => {
    vi.mocked(api.listActiveJobs).mockResolvedValue([makeJob({ source: 'other' })])
    vi.mocked(api.getSummary).mockResolvedValue([makeSummary({ source: 'other' })])
    renderProbe('feed-a')
    await waitFor(() =>
      expect(screen.getByTestId('status')).toHaveTextContent('idle'),
    )
  })
})
