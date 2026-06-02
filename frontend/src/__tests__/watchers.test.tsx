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
        trigger: vi.fn(),
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
    publish_target: 'local',
    webhook_url: null,
    auth_header: null,
    auth_value: null,
    trigger_count: 3,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
    last_triggered_at: null,
    delivery_error_count: 0,
    last_delivery_error: null,
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

    // At least one condition is now mandatory.
    fireEvent.click(screen.getByRole('button', { name: /Add condition/i }))
    fireEvent.change(screen.getByPlaceholderText('value'), { target: { value: 'CVE-2025-1' } })

    fireEvent.click(screen.getByRole('button', { name: /Create watcher/i }))
    await waitFor(() => expect(api.watchers.create).toHaveBeenCalled())
    const payload = vi.mocked(api.watchers.create).mock.calls[0][0]
    expect(payload.name).toBe('New One')
    expect(payload.conditions).toHaveLength(1)
    expect(payload.conditions[0].value).toBe('CVE-2025-1')
  })

  it('blocks creation when there are zero conditions', async () => {
    vi.mocked(api.watchers.list).mockResolvedValue([])
    renderPage()

    fireEvent.click(screen.getByRole('button', { name: 'Configuration' }))
    fireEvent.click(await screen.findByRole('button', { name: /Add Watcher/i }))

    const nameLabel = await screen.findByText('Name')
    const input = nameLabel.parentElement!.querySelector('input')!
    fireEvent.change(input, { target: { value: 'No Conditions' } })

    // Save is disabled and a hint is shown until a condition is added.
    expect(screen.getByRole('button', { name: /Create watcher/i })).toBeDisabled()
    expect(screen.getByText(/At least one condition is required/i)).toBeInTheDocument()
  })

  it('shows the public feed Last Triggered value on the Summary tab', async () => {
    vi.mocked(api.watchers.list).mockResolvedValue([
      watcher({ last_triggered_at: null }),
    ])
    renderPage()
    expect(await screen.findByText('Critical CVEs')).toBeInTheDocument()
    expect(screen.getByText('never')).toBeInTheDocument()
  })

  it('deletes a watcher via an inline confirmation card', async () => {
    vi.mocked(api.watchers.list).mockResolvedValue([watcher({ name: 'Doomed' })])
    vi.mocked(api.watchers.remove).mockResolvedValue(undefined)
    renderPage()

    fireEvent.click(screen.getByRole('button', { name: 'Configuration' }))
    await screen.findByText('Doomed')

    // Clicking the trash icon shows a confirmation card, not a window.confirm.
    fireEvent.click(screen.getByTitle('Delete'))
    expect(await screen.findByText(/permanently removes its triggered events/i)).toBeInTheDocument()
    expect(api.watchers.remove).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Confirm delete' }))
    await waitFor(() => expect(api.watchers.remove).toHaveBeenCalledWith('critical-cves'))
  })

  it('copies the feed URL to the clipboard', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })
    vi.mocked(api.watchers.list).mockResolvedValue([watcher()])
    renderPage()

    await screen.findByText('Critical CVEs')
    fireEvent.click(screen.getByTitle('Copy feed URL'))
    await waitFor(() => expect(writeText).toHaveBeenCalled())
    expect(writeText.mock.calls[0][0]).toContain('/feed/watcher/critical-cves/')
  })

  it('guards against switching edit targets while the form is dirty', async () => {
    vi.mocked(api.watchers.list).mockResolvedValue([
      watcher({ id: 'one', name: 'One' }),
      watcher({ id: 'two', name: 'Two' }),
    ])
    renderPage()

    fireEvent.click(screen.getByRole('button', { name: 'Configuration' }))
    await screen.findByText('One')

    // Edit the first watcher, then make the form dirty.
    fireEvent.click(screen.getAllByTitle('Edit')[0])
    const nameLabel = await screen.findByText('Name')
    const input = nameLabel.parentElement!.querySelector('input')!
    fireEvent.change(input, { target: { value: 'One edited' } })

    // Attempting to edit the second watcher is blocked with an inline message.
    fireEvent.click(screen.getAllByTitle('Edit')[1])
    expect(await screen.findByText(/Finish or discard your current changes/i)).toBeInTheDocument()
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

describe('Watchers page (issue_local_007)', () => {
  it('manually triggers a watcher from the Summary tab', async () => {
    vi.mocked(api.watchers.list).mockResolvedValue([watcher()])
    vi.mocked(api.watchers.trigger).mockResolvedValue({
      evaluated: 12,
      triggered: 2,
      delivery: { delivered: 2, failed: 0 },
    })
    renderPage()

    await screen.findByText('Critical CVEs')
    fireEvent.click(screen.getByRole('button', { name: /Trigger/i }))
    await waitFor(() =>
      expect(api.watchers.trigger).toHaveBeenCalledWith('critical-cves'),
    )
    expect(await screen.findByText(/2 new event\(s\)/i)).toBeInTheDocument()
  })

  it('surfaces a delivery-error warning card on the Summary tab', async () => {
    vi.mocked(api.watchers.list).mockResolvedValue([
      watcher({ delivery_error_count: 3, last_delivery_error: 'HTTP 500' }),
    ])
    renderPage()

    expect(await screen.findByText(/Delivery errors/i)).toBeInTheDocument()
    expect(screen.getByText(/3 failed/i)).toBeInTheDocument()
    expect(screen.getByText(/HTTP 500/)).toBeInTheDocument()
  })

  it('reveals the destination URL field when a webhook target is chosen', async () => {
    vi.mocked(api.watchers.list).mockResolvedValue([])
    vi.mocked(api.watchers.create).mockResolvedValue(watcher())
    renderPage()

    fireEvent.click(screen.getByRole('button', { name: 'Configuration' }))
    fireEvent.click(await screen.findByRole('button', { name: /Add Watcher/i }))

    const nameLabel = await screen.findByText('Name')
    const input = nameLabel.parentElement!.querySelector('input')!
    fireEvent.change(input, { target: { value: 'Hooked' } })
    fireEvent.click(screen.getByRole('button', { name: /Add condition/i }))
    fireEvent.change(screen.getByPlaceholderText('value'), { target: { value: 'x' } })

    // Choose the Webhook publish target.
    const publishSelect = screen.getByDisplayValue('Local URL feed')
    fireEvent.change(publishSelect, { target: { value: 'webhook' } })

    // The URL field appears and save is blocked until a valid http(s) URL is set.
    const urlInput = await screen.findByPlaceholderText('https://example.com/hook')
    expect(screen.getByRole('button', { name: /Create watcher/i })).toBeDisabled()
    expect(
      screen.getByText(/valid http\(s\) destination URL is required/i),
    ).toBeInTheDocument()

    fireEvent.change(urlInput, { target: { value: 'https://hook.example/in' } })
    fireEvent.click(screen.getByRole('button', { name: /Create watcher/i }))
    await waitFor(() => expect(api.watchers.create).toHaveBeenCalled())
    const payload = vi.mocked(api.watchers.create).mock.calls[0][0]
    expect(payload.publish_target).toBe('webhook')
    expect(payload.webhook_url).toBe('https://hook.example/in')
  })
})
