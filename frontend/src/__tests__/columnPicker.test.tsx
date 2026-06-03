/**
 * Tests for the EntryTable column picker collapsibility (prompts-021A item 1).
 *
 * Covers:
 *   1. Per-source groups default to COLLAPSED when there are more than
 *      AUTOEXPAND_THRESHOLD (=2) source groups.
 *   2. Clicking a per-source group chevron expands ONLY that group.
 *   3. Per-group "Select all" toggles columns inside that group only,
 *      leaving columns from other groups untouched. (Scope label
 *      "(this group only)" is rendered on every Select all / Clear row.)
 */
import { render, screen, fireEvent, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, it, expect, vi, beforeEach } from 'vitest'

import type { Entry, SummaryItem } from '../api/client'

// Mock api before importing the component under test.
vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...actual,
    api: {
      getEntries: vi.fn(),
      getSummary: vi.fn(),
      getFieldPresence: vi.fn().mockResolvedValue([]),
    },
  }
})

import EntryTable from '../components/EntryTable'
import { api } from '../api/client'

function makeEntry(source: string, extraKey: string): Entry {
  return {
    source,
    ingested_at: '2025-01-01T00:00:00Z',
    indicator: `ind_${source}`,
    [extraKey]: `val_${source}`,
  } as Entry
}

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>)
}

describe('EntryTable column picker collapsibility', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Three sources, each contributes a distinct extra column.
    const entries: Entry[] = [
      makeEntry('src_a', 'extra_a'),
      makeEntry('src_b', 'extra_b'),
      makeEntry('src_c', 'extra_c'),
    ]
    const summary: SummaryItem[] = [
      { source: 'src_a', count: 1 },
      { source: 'src_b', count: 1 },
      { source: 'src_c', count: 1 },
    ]
    ;(api.getEntries as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(entries)
    ;(api.getSummary as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(summary)
  })

  async function openPicker() {
    wrap(<EntryTable />)
    // Click the Columns toggle to open the picker, then wait for the
    // per-source groups to render (they appear once entries arrive).
    const colsBtn = await screen.findByRole('button', { name: /columns/i })
    fireEvent.click(colsBtn)
    await screen.findByRole('button', { name: /src_a/i })
  }

  it('collapses per-source groups by default when more than 2 sources exist', async () => {
    await openPicker()

    // Schema row is expanded (>=1 schema column button visible).
    expect(screen.getByRole('button', { name: /^indicator$/ })).toBeInTheDocument()

    // Per-source extra columns are NOT visible (groups collapsed).
    expect(screen.queryByRole('button', { name: /^extra_a$/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /^extra_b$/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /^extra_c$/ })).toBeNull()

    // Scope label appears on every Select-all/Clear row.
    const scopeLabels = screen.getAllByText(/\(this group only\)/i)
    // Schema row + 3 source groups = 4 scope labels.
    expect(scopeLabels.length).toBe(4)
  })

  it('expands only the clicked source group', async () => {
    await openPicker()

    // Click the chevron-row for src_b (the button whose name includes "src_b").
    const srcBHeader = screen.getByRole('button', { name: /src_b/i })
    fireEvent.click(srcBHeader)

    // src_b's extra column now visible; others still hidden.
    expect(screen.getByRole('button', { name: /^extra_b$/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^extra_a$/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /^extra_c$/ })).toBeNull()
  })

  it('per-group Select all toggles only that group\'s columns', async () => {
    await openPicker()

    // Expand src_a and src_b.
    fireEvent.click(screen.getByRole('button', { name: /src_a/i }))
    fireEvent.click(screen.getByRole('button', { name: /src_b/i }))

    // Both extras now rendered as buttons. They start NOT-visible (extras
    // aren't in DEFAULT_VISIBLE); the button background reflects that
    // via the EyeOff icon. After Select all in src_a's row, extra_a
    // should be visible (Eye icon) and extra_b should remain unchanged.
    const extraABefore = screen.getByRole('button', { name: /^extra_a$/ })
    const extraBBefore = screen.getByRole('button', { name: /^extra_b$/ })
    // Sanity: both start hidden — Tailwind class includes bg-gray-700.
    expect(extraABefore.className).toMatch(/bg-gray-700/)
    expect(extraBBefore.className).toMatch(/bg-gray-700/)

    // Find the Select-all button inside the src_a row. The header button
    // and the "Select all" button are siblings in a flex row; query by
    // walking up from the src_a header to its parent flex row.
    const srcARow = screen.getByRole('button', { name: /src_a/i }).parentElement!
    const selectAllInA = within(srcARow).getByRole('button', { name: /select all/i })
    fireEvent.click(selectAllInA)

    // After click: extra_a now visible (amber-700 background per the
    // extras styling); extra_b still hidden.
    expect(screen.getByRole('button', { name: /^extra_a$/ }).className).toMatch(/amber-700/)
    expect(screen.getByRole('button', { name: /^extra_b$/ }).className).toMatch(/bg-gray-700/)
  })
})
