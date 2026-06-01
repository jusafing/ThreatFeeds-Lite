/**
 * Tests for the Normalizer Settings panel (prompts-032 Phase E).
 *
 * Covers:
 *   1. The mode dropdown offers the smart (consolidated) option.
 *   2. mode='smart' + no active consolidated mapping renders the Q5 warning.
 *   3. mode='smart' + an active consolidated mapping hides the warning.
 *   4. mode='auto' never shows the smart warning.
 */
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, it, expect, vi, beforeEach } from 'vitest'

import type { ActiveConsolidatedMapping } from '../api/client'

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...actual,
    api: {
      getNormalizerConfig: vi.fn(),
      updateNormalizerConfig: vi.fn(),
      runNormalizer: vi.fn(),
      smartMappings: {
        getActive: vi.fn(),
      },
    },
  }
})

import Normalizer from '../pages/Normalizer'
import { api } from '../api/client'

function makeActive(overrides: Partial<ActiveConsolidatedMapping> = {}): ActiveConsolidatedMapping {
  return {
    id: 3,
    sources: ['feed-a', 'feed-b'],
    field_count: 4,
    field_scope: 'configured',
    proposal_id: 9,
    created_at: '2026-01-01T00:00:00Z',
    note: null,
    ...overrides,
  }
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <Normalizer />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.updateNormalizerConfig).mockReset()
  vi.mocked(api.getNormalizerConfig).mockReset()
  vi.mocked(api.smartMappings.getActive).mockReset()
  vi.mocked(api.smartMappings.getActive).mockResolvedValue({ active: null })
})

describe('Normalizer Settings — smart mode', () => {
  it('offers the smart (consolidated) mode option', async () => {
    vi.mocked(api.getNormalizerConfig).mockResolvedValue({ mode: 'auto', enabled: true, interval_minutes: 10 })
    renderPage()
    const option = await screen.findByRole('option', { name: /Smart \(consolidated mapping\)/i })
    expect(option).toBeInTheDocument()
  })

  it('warns when mode=smart and no consolidated mapping is active', async () => {
    vi.mocked(api.getNormalizerConfig).mockResolvedValue({ mode: 'smart', enabled: true, interval_minutes: 10 })
    vi.mocked(api.smartMappings.getActive).mockResolvedValue({ active: null })
    renderPage()
    expect(
      await screen.findByText(/no consolidated mapping is active/i),
    ).toBeInTheDocument()
  })

  it('hides the warning when an active consolidated mapping exists', async () => {
    vi.mocked(api.getNormalizerConfig).mockResolvedValue({ mode: 'smart', enabled: true, interval_minutes: 10 })
    vi.mocked(api.smartMappings.getActive).mockResolvedValue({ active: makeActive() })
    renderPage()
    // Wait for the mode dropdown to settle on smart, then assert no banner.
    await screen.findByRole('option', { name: /Smart \(consolidated mapping\)/i })
    await waitFor(() => {
      expect(screen.queryByText(/no consolidated mapping is active/i)).not.toBeInTheDocument()
    })
  })

  it('does not warn in auto mode even without an active mapping', async () => {
    vi.mocked(api.getNormalizerConfig).mockResolvedValue({ mode: 'auto', enabled: true, interval_minutes: 10 })
    vi.mocked(api.smartMappings.getActive).mockResolvedValue({ active: null })
    renderPage()
    await screen.findByRole('option', { name: /Smart \(consolidated mapping\)/i })
    expect(screen.queryByText(/no consolidated mapping is active/i)).not.toBeInTheDocument()
  })
})
