/**
 * Tests for the Raw Feeds table default columns (issue_local_009).
 *
 * Covers:
 *   1. The "Ingested at" column renders in the 2nd position, right after the
 *      "Source" (feed) column.
 *   2. The default visible columns are derived from the server's field-presence
 *      ranking (fields that actually carry content), always pinning
 *      source + ingested_at first and capping the set at 15 columns.
 */
import { render, screen, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, it, expect, vi, beforeEach } from 'vitest'

import type { Entry, SummaryItem } from '../api/client'

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...actual,
    api: {
      getEntries: vi.fn(),
      getSummary: vi.fn(),
      getFieldPresence: vi.fn(),
    },
  }
})

import EntryTable from '../components/EntryTable'
import { api } from '../api/client'

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>)
}

const ENTRY: Entry = {
  source: 'src_a',
  ingested_at: '2025-01-01T00:00:00Z',
  indicator: '1.2.3.4',
  indicator_type: 'ipv4',
  cve_id: 'CVE-2025-0001',
  actor: 'APT-X',
} as Entry

const SUMMARY: SummaryItem[] = [{ source: 'src_a', count: 1 }]

const mock = (fn: unknown) => fn as ReturnType<typeof vi.fn>

describe('EntryTable default columns (issue_local_009)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mock(api.getEntries).mockResolvedValue([ENTRY])
    mock(api.getSummary).mockResolvedValue(SUMMARY)
    mock(api.getFieldPresence).mockResolvedValue([
      'cve_id', 'actor', 'indicator', 'indicator_type',
    ])
  })

  it('renders Ingested at as the second column, after Source', async () => {
    wrap(<EntryTable />)
    // Wait for the data row to render.
    await screen.findByText('1.2.3.4')
    const headers = screen.getAllByRole('columnheader').map(h => h.textContent?.trim())
    // Header text is lowercase (capitalization is applied via CSS).
    expect(headers[0]).toBe('source')
    expect(headers[1]).toBe('ingested at')
  })

  it('derives default visible columns from field-presence ranking', async () => {
    wrap(<EntryTable />)
    await screen.findByText('1.2.3.4')
    const headers = screen.getAllByRole('columnheader').map(h => h.textContent?.trim())
    // source + ingested_at are pinned; the ranked populated fields fill the
    // rest. Display order follows the stable schema order, not the ranking.
    expect(new Set(headers)).toEqual(
      new Set(['source', 'ingested at', 'indicator', 'indicator type', 'cve id', 'actor']),
    )
    // A non-populated schema column (e.g. tlp) is NOT shown by default.
    expect(headers).not.toContain('tlp')
    // A populated value from a ranked column is shown in the body.
    const table = screen.getByRole('table')
    expect(within(table).getByText('CVE-2025-0001')).toBeInTheDocument()
  })
})
