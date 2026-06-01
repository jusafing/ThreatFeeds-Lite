/**
 * ListenerTab tests (prompts-053).
 *
 * The Listener Endpoint tab was reworked: the dead "Listener Port" control was
 * removed, the enabled toggle now genuinely gates the generic listener route,
 * and the help text points at POST /api/ingest/listener (auto-named feeds).
 * The API client is mocked and SourceFieldsPanel is stubbed so the tab renders
 * in isolation.
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...actual,
    api: { getListener: vi.fn(), updateListener: vi.fn() },
  }
})

vi.mock('../components/SourceFieldsPanel', () => ({
  default: () => <div data-testid="fields-panel" />,
}))

import { api } from '../api/client'
import { ListenerTab } from '../pages/Configuration'

function renderTab() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <ListenerTab />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('ListenerTab (prompts-053)', () => {
  it('shows the generic listener endpoint and no port input', async () => {
    vi.mocked(api.getListener).mockResolvedValue({ enabled: true })
    renderTab()

    expect(await screen.findByText('/api/ingest/listener', { exact: false })).toBeInTheDocument()
    expect(screen.getByText(/Received Feed/)).toBeInTheDocument()
    // prompts-058: help text explains feeds are named after the sending user.
    expect(screen.getByText(/named after your username/)).toBeInTheDocument()
    // The dead "Listener Port" control must be gone.
    expect(screen.queryByText('Listener Port')).not.toBeInTheDocument()
    expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument()
  })

  it('toggling status saves only the enabled flag (no port)', async () => {
    vi.mocked(api.getListener).mockResolvedValue({ enabled: true })
    vi.mocked(api.updateListener).mockResolvedValue({ enabled: false })
    renderTab()

    await screen.findByText('/api/ingest/listener', { exact: false })
    fireEvent.click(screen.getByRole('switch'))

    await waitFor(() => {
      expect(api.updateListener).toHaveBeenCalledWith({ enabled: false })
    })
  })
})
