/**
 * Tests for the Normalized viewer (prompts-043 client-side rework).
 *
 * Covers the surviving prompts-039/040 behaviour (disabled banner, dynamic
 * columns, Run & Apply) plus the new prompts-043 controls: page-size dropdown +
 * pagination, search button, per-field unique-value filter, the collapsible
 * column picker, and per-source color chips.
 *
 * The mapping-version filter (prompts-021F) was removed from the viewer in
 * prompts-043, so its tests are gone with it.
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, it, expect, vi, beforeEach } from 'vitest'

import type { ActiveConsolidatedMapping } from '../api/client'

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...actual,
    api: {
      getPaginationMax: vi.fn(),
      getNormalizedEntries: vi.fn(),
      getNormalizerConfig: vi.fn(),
      runNormalizer: vi.fn(),
      smartMappings: {
        getActive: vi.fn(),
        runActive: vi.fn(),
      },
    },
  }
})

import NormalizedTable from '../components/NormalizedTable'
import { api } from '../api/client'

function renderTable() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <NormalizedTable />
    </QueryClientProvider>,
  )
}

type Row = Record<string, unknown>

beforeEach(() => {
  vi.mocked(api.getPaginationMax).mockReset()
  vi.mocked(api.getNormalizedEntries).mockReset()
  vi.mocked(api.getNormalizerConfig).mockReset()
  vi.mocked(api.runNormalizer).mockReset()
  vi.mocked(api.smartMappings.getActive).mockReset()

  vi.mocked(api.getPaginationMax).mockResolvedValue({ pagination_max: 1000 })
  vi.mocked(api.getNormalizedEntries).mockResolvedValue([])
  vi.mocked(api.getNormalizerConfig).mockResolvedValue({ enabled: true })
  vi.mocked(api.runNormalizer).mockResolvedValue({ status: 'ok', processed: 0, inserted: 0, errors: 0 })
  vi.mocked(api.smartMappings.getActive).mockResolvedValue({ active: null })
})

describe('NormalizedTable disabled banner (prompts-039)', () => {
  it('shows an alert when the normalizer is disabled', async () => {
    vi.mocked(api.getNormalizerConfig).mockResolvedValue({ enabled: false })
    renderTable()
    expect(await screen.findByRole('alert')).toHaveTextContent(/normalizer is currently disabled/i)
  })

  it('hides the alert when the normalizer is enabled', async () => {
    renderTable()
    await screen.findByRole('button', { name: /Refresh Table/i })
    expect(screen.queryByRole('alert')).toBeNull()
  })
})

describe('NormalizedTable dynamic columns (prompts-040)', () => {
  const activeMapping: ActiveConsolidatedMapping = {
    id: 3,
    sources: ['feed_a'],
    field_count: 2,
    field_scope: null,
    proposal_id: 7,
    proposal_name: 'Proposal-X',
    created_at: '2026-01-02T00:00:00',
    note: null,
    mapping: { src_ip: 'ip_address', threat: 'severity' },
  }

  it('falls back to the static canonical columns when no mapping is active', async () => {
    renderTable()
    expect(await screen.findByRole('columnheader', { name: 'domain' })).toBeTruthy()
  })

  it('tracks the active mapping canonical fields as columns', async () => {
    vi.mocked(api.smartMappings.getActive).mockResolvedValue({ active: activeMapping })
    renderTable()
    expect(await screen.findByRole('columnheader', { name: 'source name' })).toBeTruthy()
    expect(await screen.findByRole('columnheader', { name: 'ip address' })).toBeTruthy()
    expect(await screen.findByRole('columnheader', { name: 'severity' })).toBeTruthy()
    expect(screen.queryByRole('columnheader', { name: 'domain' })).toBeNull()
  })
})

describe('NormalizedTable Run & Apply (prompts-040)', () => {
  it('renames the refresh control to "Refresh Table"', async () => {
    renderTable()
    expect(await screen.findByRole('button', { name: /Refresh Table/i })).toBeTruthy()
  })

  it('runs the normalizer via "Run & Apply" and disables the button while running', async () => {
    let resolveRun: (v: Record<string, unknown>) => void = () => {}
    vi.mocked(api.runNormalizer).mockReturnValue(
      new Promise<Record<string, unknown>>((res) => { resolveRun = res }),
    )
    renderTable()
    const runBtn = await screen.findByRole('button', { name: /Run & Apply/i })
    fireEvent.click(runBtn)
    await waitFor(() => expect(api.runNormalizer).toHaveBeenCalledTimes(1))
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Running/i })).toBeDisabled(),
    )
    resolveRun({ status: 'ok', processed: 0, inserted: 0, errors: 0 })
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Run & Apply/i })).not.toBeDisabled(),
    )
  })
})

describe('NormalizedTable controls (prompts-043)', () => {
  function makeRows(n: number): Row[] {
    return Array.from({ length: n }, (_, i) => ({
      source_name: `s${i % 5}`,
      severity: i % 2 ? 'high' : 'low',
      country: 'US',
      normalized_at: '2026-01-01T00:00:00',
    }))
  }

  it('paginates by the selected page size', async () => {
    vi.mocked(api.getNormalizedEntries).mockResolvedValue(makeRows(120))
    renderTable()
    // Default page size 100 → 120 rows = 2 pages.
    expect(await screen.findByText(/Page 1 of 2/)).toBeInTheDocument()
    // Switch to 50 → 3 pages.
    fireEvent.change(screen.getByLabelText('Rows per page'), { target: { value: '50' } })
    expect(await screen.findByText(/Page 1 of 3/)).toBeInTheDocument()
  })

  it('advances pages with the Next control', async () => {
    vi.mocked(api.getNormalizedEntries).mockResolvedValue(makeRows(120))
    renderTable()
    await screen.findByText(/Page 1 of 2/)
    fireEvent.click(screen.getByRole('button', { name: /Next page/i }))
    expect(await screen.findByText(/Page 2 of 2/)).toBeInTheDocument()
  })

  it('applies the search term only when the Search button is clicked', async () => {
    vi.mocked(api.getNormalizedEntries).mockResolvedValue([
      { source_name: 'alpha_feed', severity: 'high' },
      { source_name: 'beta_feed', severity: 'low' },
    ])
    const { container } = renderTable()
    const beta = () => container.querySelector('[data-source-chip="beta_feed"]')
    await waitFor(() => expect(beta()).not.toBeNull())
    fireEvent.change(screen.getByLabelText('Search all fields'), { target: { value: 'alpha' } })
    // Not applied until the button is pressed.
    expect(beta()).not.toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /^Search$/i }))
    await waitFor(() => expect(beta()).toBeNull())
    expect(container.querySelector('[data-source-chip="alpha_feed"]')).not.toBeNull()
  })

  it('keeps the per-field filter bar hidden until the Filters button is toggled', async () => {
    vi.mocked(api.getNormalizedEntries).mockResolvedValue([{ source_name: 's0' }])
    renderTable()
    await screen.findByRole('button', { name: /Refresh Table/i })
    // Hidden by default.
    expect(screen.queryByLabelText('Filter by source_name')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /^Filters$/i }))
    expect(await screen.findByLabelText('Filter by source_name')).toBeTruthy()
  })

  it('filters via a per-field unique-value dropdown', async () => {
    vi.mocked(api.getNormalizedEntries).mockResolvedValue([
      { source_name: 'alpha_feed' },
      { source_name: 'beta_feed' },
    ])
    const { container } = renderTable()
    await waitFor(() =>
      expect(container.querySelector('[data-source-chip="alpha_feed"]')).not.toBeNull(),
    )
    // Open the collapsible filter bar (prompts-044: hidden by default).
    fireEvent.click(screen.getByRole('button', { name: /^Filters$/i }))
    // source_name is low-cardinality → a select once data has loaded.
    const sourceFilter = await screen.findByLabelText('Filter by source_name')
    expect(sourceFilter.tagName).toBe('SELECT')
    fireEvent.change(sourceFilter, { target: { value: 'alpha_feed' } })
    await waitFor(() =>
      expect(container.querySelector('[data-source-chip="beta_feed"]')).toBeNull(),
    )
    expect(container.querySelector('[data-source-chip="alpha_feed"]')).not.toBeNull()
  })

  it('forces a unique-value dropdown for cve_id even when high-cardinality', async () => {
    // 30 distinct cve_id values exceed LOW_CARD_THRESHOLD (25); without the
    // force list this would be a text filter, but prompts-044 forces a select.
    vi.mocked(api.smartMappings.getActive).mockResolvedValue({
      active: {
        id: 1, sources: ['f'], field_count: 1, field_scope: null,
        proposal_id: 1, proposal_name: 'p', created_at: '2026-01-01T00:00:00',
        note: null, mapping: { raw_cve: 'cve_id' },
      },
    })
    vi.mocked(api.getNormalizedEntries).mockResolvedValue(
      Array.from({ length: 30 }, (_, i) => ({
        source_name: 's0', cve_id: `CVE-2026-${1000 + i}`,
      })),
    )
    renderTable()
    await screen.findByRole('button', { name: /Refresh Table/i })
    fireEvent.click(screen.getByRole('button', { name: /^Filters$/i }))
    const cveFilter = await screen.findByLabelText('Filter by cve_id')
    expect(cveFilter.tagName).toBe('SELECT')
  })

  it('orders columns: feed first, normalized_at last', async () => {
    vi.mocked(api.smartMappings.getActive).mockResolvedValue({
      active: {
        id: 1, sources: ['f'], field_count: 3, field_scope: null,
        proposal_id: 1, proposal_name: 'p', created_at: '2026-01-01T00:00:00',
        note: null, mapping: { a: 'cve_id', b: 'published_at', c: 'country' },
      },
    })
    vi.mocked(api.getNormalizedEntries).mockResolvedValue([{ source_name: 's0' }])
    renderTable()
    await screen.findByRole('columnheader', { name: 'published at' })
    const headers = screen.getAllByRole('columnheader').map((h) => h.textContent)
    expect(headers[0]).toBe('source name')
    expect(headers[1]).toBe('published at')
    expect(headers[headers.length - 1]).toBe('normalized at')
    // published_at (priority) precedes country (unlisted → alphabetical tail).
    expect(headers.indexOf('published at')).toBeLessThan(headers.indexOf('country'))
  })

  it('drops the duplicate canonical "source" column, keeping only source_name', async () => {
    vi.mocked(api.smartMappings.getActive).mockResolvedValue({
      active: {
        id: 1, sources: ['f'], field_count: 2, field_scope: null,
        proposal_id: 1, proposal_name: 'p', created_at: '2026-01-01T00:00:00',
        note: null, mapping: { feed: 'source', sev: 'severity' },
      },
    })
    vi.mocked(api.getNormalizedEntries).mockResolvedValue([{ source_name: 's0' }])
    renderTable()
    expect(await screen.findByRole('columnheader', { name: 'source name' })).toBeTruthy()
    expect(screen.queryByRole('columnheader', { name: 'source' })).toBeNull()
  })

  it('adds all / clears all columns, always keeping the feed name', async () => {
    vi.mocked(api.getNormalizedEntries).mockResolvedValue([{ source_name: 's0', country: 'US' }])
    renderTable()
    await screen.findByRole('columnheader', { name: 'country' })
    fireEvent.click(screen.getByRole('button', { name: /^Columns$/i }))
    // Clear all → only source_name remains as a column.
    fireEvent.click(await screen.findByRole('button', { name: /^Clear all$/i }))
    await waitFor(() =>
      expect(screen.queryByRole('columnheader', { name: 'country' })).toBeNull(),
    )
    expect(screen.getByRole('columnheader', { name: 'source name' })).toBeTruthy()
    // Add all → country comes back.
    fireEvent.click(screen.getByRole('button', { name: /^Add all$/i }))
    expect(await screen.findByRole('columnheader', { name: 'country' })).toBeTruthy()
  })

  it('hides the column picker by default and toggles a column off', async () => {
    vi.mocked(api.getNormalizedEntries).mockResolvedValue([{ source_name: 's0', country: 'US' }])
    renderTable()
    // Column header present initially; picker hidden.
    expect(await screen.findByRole('columnheader', { name: 'country' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'country', pressed: true })).toBeNull()
    // Open picker, toggle 'country' off.
    fireEvent.click(screen.getByRole('button', { name: /^Columns$/i }))
    const chip = await screen.findByRole('button', { name: 'country', pressed: true })
    fireEvent.click(chip)
    await waitFor(() =>
      expect(screen.queryByRole('columnheader', { name: 'country' })).toBeNull(),
    )
  })

  it('renders distinct color chips per source', async () => {
    vi.mocked(api.getNormalizedEntries).mockResolvedValue([
      { source_name: 'alpha_feed' },
      { source_name: 'gamma_feed' },
    ])
    const { container } = renderTable()
    await waitFor(() =>
      expect(container.querySelectorAll('[data-source-chip]').length).toBe(2),
    )
    const chips = Array.from(container.querySelectorAll('[data-source-chip]')) as HTMLElement[]
    expect(chips[0].className).not.toBe(chips[1].className)
  })
})
