/**
 * Tests for the Watchers page (issue_local_006).
 *
 * The API client `watchers` group is mocked so the Summary list, the create
 * form, and the enable toggle are driven deterministically without a network.
 */
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...actual,
    api: {
      ...actual.api,
      watchers: {
        list: vi.fn(),
        get: vi.fn(),
        create: vi.fn(),
        update: vi.fn(),
        setEnabled: vi.fn(),
        remove: vi.fn(),
        events: vi.fn(),
        metaFeeds: vi.fn(),
        metaFields: vi.fn(),
      },
    },
  }
})

import { api, Watcher } from '../api/client'
import Watchers from '../pages/Watchers'

function watcher(over: Partial<Watcher> = {}): Watcher {
  return {
    id: 'critical-cves',
    name: 'Critical CVEs',
    severity: 'critical',
    dataset: 'normalized',
    feeds: [],
    conditions: [{ field: 'cve_id', value: 'CVE-2024-*', match_type: 'wildcard' }],
    mode: 'realtime',
    interval_sec: 120,
    format: 'json',
    max_feed_events: 10,
    enabled: true,
    trigger_count: 3,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
    ...over,
  }
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <Watchers />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.watchers.metaFeeds).mockResolvedValue({ feeds: ['feed-a', 'feed-b'] })
  vi.mocked(api.watchers.metaFields).mockResolvedValue({ fields: ['cve_id', 'severity'] })
  vi.mocked(api.watchers.events).mockResolvedValue({ events: [], total: 0 })
})

describe('Watchers page (issue_local_006)', () => {
  it('lists watchers with trigger count and a public feed URL on the Summary tab', async () => {
    vi.mocked(api.watchers.list).mockResolvedValue([watcher()])
    renderPage()

    expect(await screen.findByText('Critical CVEs')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
    const link = screen.getByRole('link', { name: /feed\/watcher\/critical-cves/ })
    expect(link.getAttribute('href')).toContain('/feed/watcher/critical-cves/')
  })

  it('shows an empty state when there are no watchers', async () => {
    vi.mocked(api.watchers.list).mockResolvedValue([])
    renderPage()
    expect(await screen.findByText(/No watchers yet/i)).toBeInTheDocument()
  })

  it('toggles a watcher enabled state via the API', async () => {
    vi.mocked(api.watchers.list).mockResolvedValue([watcher({ enabled: true })])
    vi.mocked(api.watchers.setEnabled).mockResolvedValue(watcher({ enabled: false }))
    renderPage()

    await screen.findByText('Critical CVEs')
    fireEvent.click(screen.getByRole('switch'))
    await waitFor(() =>
      expect(api.watchers.setEnabled).toHaveBeenCalledWith('critical-cves', false),
    )
  })

  it('creates a watcher from the Configuration tab form', async () => {
    vi.mocked(api.watchers.list).mockResolvedValue([])
    vi.mocked(api.watchers.create).mockResolvedValue(watcher({ id: 'new-one', name: 'New One' }))
    renderPage()

    // Switch to Configuration tab.
    fireEvent.click(screen.getByRole('button', { name: 'Configuration' }))
    fireEvent.click(await screen.findByRole('button', { name: /Add Watcher/i }))

    const nameInput = await screen.findByText('Name')
    const input = nameInput.parentElement!.querySelector('input')!
    fireEvent.change(input, { target: { value: 'New One' } })

    fireEvent.click(screen.getByRole('button', { name: /Create watcher/i }))
    await waitFor(() => expect(api.watchers.create).toHaveBeenCalled())
    const payload = vi.mocked(api.watchers.create).mock.calls[0][0]
    expect(payload.name).toBe('New One')
  })

  it('blocks creation when the name duplicates an existing watcher', async () => {
    vi.mocked(api.watchers.list).mockResolvedValue([watcher({ name: 'Dup' })])
    renderPage()

    fireEvent.click(screen.getByRole('button', { name: 'Configuration' }))
    fireEvent.click(await screen.findByRole('button', { name: /Add Watcher/i }))

    const nameLabel = await screen.findByText('Name')
    const input = nameLabel.parentElement!.querySelector('input')!
    fireEvent.change(input, { target: { value: 'Dup' } })

    expect(await screen.findByText(/already exists/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Create watcher/i })).toBeDisabled()
  })
})
