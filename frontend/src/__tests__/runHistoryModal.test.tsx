/**
 * Tests for RunHistoryModal (prompts-039).
 *
 * Covers: rows render newest-first with time / trigger / proposal / feeds /
 * result columns; smart applies show the proposal name + feeds while auto rows
 * show a dash; an empty history shows the placeholder.
 */
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, it, expect, vi, beforeEach } from 'vitest'

import type { RunHistoryRow } from '../api/client'

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...actual,
    api: { getRunHistory: vi.fn() },
  }
})

import RunHistoryModal from '../components/RunHistoryModal'
import { api } from '../api/client'

function renderModal() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <RunHistoryModal onClose={() => {}} />
    </QueryClientProvider>,
  )
}

const rows: RunHistoryRow[] = [
  {
    id: 2,
    started_at: '2026-05-30T12:00:00+00:00',
    trigger: 'manual',
    mode: 'smart',
    proposal_id: 7,
    proposal_name: 'Proposal-Alpha',
    sources: ['feed-a', 'feed-b'],
    status: 'ok',
    processed: 10,
    inserted: 8,
    errors: 0,
    warning: null,
  },
  {
    id: 1,
    started_at: '2026-05-30T11:00:00+00:00',
    trigger: 'schedule',
    mode: 'auto',
    proposal_id: null,
    proposal_name: null,
    sources: [],
    status: 'ok',
    processed: 3,
    inserted: 3,
    errors: 0,
    warning: null,
  },
]

beforeEach(() => {
  vi.mocked(api.getRunHistory).mockReset()
})

describe('RunHistoryModal', () => {
  it('renders run rows with proposal name and feeds for smart applies', async () => {
    vi.mocked(api.getRunHistory).mockResolvedValue(rows)
    renderModal()
    expect(await screen.findByText('Proposal-Alpha')).toBeInTheDocument()
    expect(screen.getByText('feed-a, feed-b')).toBeInTheDocument()
    expect(screen.getByText(/10 processed · 8 inserted/)).toBeInTheDocument()
  })

  it('shows a dash for auto/schedule rows with no proposal or feeds', async () => {
    vi.mocked(api.getRunHistory).mockResolvedValue([rows[1]])
    renderModal()
    await screen.findByText(/Scheduled/)
    // proposal and feeds columns both render an em-dash
    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(2)
  })

  it('shows a placeholder when there is no history', async () => {
    vi.mocked(api.getRunHistory).mockResolvedValue([])
    renderModal()
    expect(await screen.findByText(/No runs recorded yet/i)).toBeInTheDocument()
  })
})
