/**
 * Tests for the Threat-Intel catalogue card (prompts-042).
 *
 * Covers:
 *   1. Rows render title/url/info; disabled rows hide the continuous controls.
 *   2. Enabling a row reveals the Continuous-pull toggle.
 *   3. Enabling continuous reveals the interval input.
 *   4. Save sends the batched toggles and reloads the scheduler.
 *   5. Save is disabled until a change is made.
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, it, expect, vi, beforeEach } from 'vitest'

import type { ThreatIntelCatalogItem } from '../api/client'

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...actual,
    api: {
      getThreatIntelCatalog: vi.fn(),
      saveThreatIntelSources: vi.fn(),
      reloadScheduler: vi.fn(),
    },
  }
})

import ThreatIntelCatalog from '../components/ThreatIntelCatalog'
import { api } from '../api/client'

function makeItem(o: Partial<ThreatIntelCatalogItem> = {}): ThreatIntelCatalogItem {
  return {
    name: 'cisa_kev',
    title: 'CISA KEV',
    kind: 'remote_json_pull',
    url: 'https://example.com/kev.json',
    info: 'actively exploited CVEs',
    default_interval_minutes: 360,
    enabled: false,
    continuous: false,
    interval_minutes: 360,
    ...o,
  }
}

function renderCard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <ThreatIntelCatalog />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.getThreatIntelCatalog).mockReset()
  vi.mocked(api.saveThreatIntelSources).mockReset()
  vi.mocked(api.reloadScheduler).mockReset()
  vi.mocked(api.saveThreatIntelSources).mockResolvedValue([])
  vi.mocked(api.reloadScheduler).mockResolvedValue({ status: 'ok' })
})

describe('ThreatIntelCatalog', () => {
  it('renders feed title, url and info; hides continuous when disabled', async () => {
    vi.mocked(api.getThreatIntelCatalog).mockResolvedValue([makeItem()])
    renderCard()
    expect(await screen.findByText('CISA KEV')).toBeInTheDocument()
    expect(screen.getByText('https://example.com/kev.json')).toBeInTheDocument()
    expect(screen.getByText('actively exploited CVEs')).toBeInTheDocument()
    expect(screen.queryByText('Continuous pull')).not.toBeInTheDocument()
  })

  it('reveals continuous toggle when a row is enabled, then interval input', async () => {
    const user = userEvent.setup()
    vi.mocked(api.getThreatIntelCatalog).mockResolvedValue([makeItem()])
    renderCard()
    await screen.findByText('CISA KEV')

    // First switch is the row enable toggle.
    const switches = screen.getAllByRole('switch')
    await user.click(switches[0])
    expect(await screen.findByText('Continuous pull')).toBeInTheDocument()
    // Interval not shown until continuous is on.
    expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument()

    // Second switch is now the continuous toggle.
    const switchesAfter = screen.getAllByRole('switch')
    await user.click(switchesAfter[1])
    expect(await screen.findByRole('spinbutton')).toBeInTheDocument()
  })

  it('Save is disabled until a change and then sends batched toggles + reloads scheduler', async () => {
    const user = userEvent.setup()
    vi.mocked(api.getThreatIntelCatalog).mockResolvedValue([makeItem()])
    renderCard()
    await screen.findByText('CISA KEV')

    const saveBtn = screen.getByRole('button', { name: /save/i })
    expect(saveBtn).toBeDisabled()

    await user.click(screen.getAllByRole('switch')[0]) // enable
    expect(saveBtn).toBeEnabled()
    await user.click(saveBtn)

    await waitFor(() => expect(api.saveThreatIntelSources).toHaveBeenCalledTimes(1))
    expect(api.saveThreatIntelSources).toHaveBeenCalledWith([
      { name: 'cisa_kev', enabled: true, continuous: false, interval_minutes: 360 },
    ])
    await waitFor(() => expect(api.reloadScheduler).toHaveBeenCalled())
  })
})
