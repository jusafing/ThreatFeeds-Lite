/**
 * RefreshAllButton tests via ApiTab (prompts-056).
 *
 * Each External section (External Feeds, External RSS, External API) gets a
 * "Refresh all" button that calls the per-section batch endpoint and shows a
 * succeeded/total summary. A batch with a failed source still reports a
 * summary (the backend never aborts the batch).
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...actual,
    api: {
      getApiPull: vi.fn(),
      refreshAllApiPull: vi.fn(),
    },
  }
})

vi.mock('../components/SourceList', () => ({
  default: () => <div data-testid="source-list" />,
}))

import { api } from '../api/client'
import { ApiTab } from '../pages/Configuration'

function renderTab() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <ApiTab />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('RefreshAllButton (prompts-056)', () => {
  it('refreshes all sources and shows a succeeded/total summary', async () => {
    vi.mocked(api.getApiPull).mockResolvedValue([])
    vi.mocked(api.refreshAllApiPull).mockResolvedValue({
      kind: 'api_pull',
      total: 2,
      succeeded: 2,
      failed: 0,
      results: [
        { name: 'a', ok: true, inserted: 1 },
        { name: 'b', ok: true, inserted: 0 },
      ],
    })
    renderTab()

    const btn = await screen.findByRole('button', { name: /Refresh all/i })
    fireEvent.click(btn)

    await waitFor(() => {
      expect(api.refreshAllApiPull).toHaveBeenCalledTimes(1)
    })
    expect(await screen.findByTestId('refresh-all-summary')).toHaveTextContent(
      'Refreshed 2/2',
    )
  })

  it('reports failed sources in the summary without throwing', async () => {
    vi.mocked(api.getApiPull).mockResolvedValue([])
    vi.mocked(api.refreshAllApiPull).mockResolvedValue({
      kind: 'api_pull',
      total: 3,
      succeeded: 2,
      failed: 1,
      results: [
        { name: 'a', ok: true },
        { name: 'b', ok: false, error: 'boom' },
        { name: 'c', ok: true },
      ],
    })
    renderTab()

    fireEvent.click(await screen.findByRole('button', { name: /Refresh all/i }))

    expect(await screen.findByTestId('refresh-all-summary')).toHaveTextContent(
      'Refreshed 2/3 — 1 failed',
    )
  })

  it('shows a no-sources message when the section is empty', async () => {
    vi.mocked(api.getApiPull).mockResolvedValue([])
    vi.mocked(api.refreshAllApiPull).mockResolvedValue({
      kind: 'api_pull', total: 0, succeeded: 0, failed: 0, results: [],
    })
    renderTab()

    fireEvent.click(await screen.findByRole('button', { name: /Refresh all/i }))

    expect(await screen.findByTestId('refresh-all-summary')).toHaveTextContent(
      'No sources configured.',
    )
  })
})
