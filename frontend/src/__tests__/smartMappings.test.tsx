/**
 * Tests for the Smart Mappings page + confirm modal (prompts-021E-2).
 *
 * Covers:
 *   1. Empty state renders the "no proposals yet" message.
 *   2. A proposal row renders status badge, mapping pairs, action buttons.
 *   3. Approve calls api.smartMappings.approve(id, {}) — single action,
 *      no set_mode_manual flag (prompts-056).
 *   4. Only one Approve button is rendered (the mode-flip variant is gone).
 *   5. Approve response with a hint surfaces it in the UI.
 *   6. The confirm modal calls api.smartMappings.createJob on submit.
 */
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, it, expect, vi, beforeEach } from 'vitest'

import type { SmartProposal, SummaryItem } from '../api/client'

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...actual,
    api: {
      getSummary: vi.fn(),
      smartMappings: {
        listProposals: vi.fn(),
        approve: vi.fn(),
        reject: vi.fn(),
        reenable: vi.fn(),
        getActive: vi.fn(),
        runActive: vi.fn(),
        createJob: vi.fn(),
        dryRun: vi.fn(),
        getJob: vi.fn(),
        getProposal: vi.fn(),
        archive: vi.fn(),
      },
      llm: {
        listProviders: vi.fn(),
      },
    },
  }
})

import SmartMappings from '../pages/SmartMappings'
import SmartProposalConfirmModal from '../components/SmartProposalConfirmModal'
import { api } from '../api/client'

function makeProposal(overrides: Partial<SmartProposal> = {}): SmartProposal {
  return {
    id: 1,
    source_name: 'feed-a',
    provider_name: 'openai',
    model: 'gpt-x',
    sample_size: 20,
    raw_fields: ['a', 'b'],
    mapping: { a: 'title', b: 'indicator' },
    prompt_system: '',
    prompt_user: '',
    llm_response_raw: '',
    llm_request_raw: '',
    llm_response_json: '',
    status: 'pending',
    created_at: '2025-01-01T00:00:00Z',
    decided_at: null,
    decided_by_note: null,
    ...overrides,
  }
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <SmartMappings />
    </QueryClientProvider>,
  )
}

/**
 * prompts-038: the outcome filter now defaults to {pending_review, approved}.
 * Tests asserting on rows with other outcomes (auto_applied, discarded,
 * rejected, archived, ...) must first clear the filter so all rows show.
 * Deselecting both default-selected buttons leaves an empty set = show all.
 */
async function clearOutcomeFilter() {
  fireEvent.click(await screen.findByRole('button', { name: 'pending review' }))
  fireEvent.click(screen.getByRole('button', { name: 'approved' }))
}

const summaryFixture: SummaryItem[] = [
  { source: 'feed-a', count: 10 },
  { source: 'feed-b', count: 5 },
]

beforeEach(() => {
  vi.mocked(api.getSummary).mockResolvedValue(summaryFixture)
  vi.mocked(api.llm.listProviders).mockResolvedValue([
    {
      name: 'openai',
      kind: 'openai',
      model: 'gpt-x',
      has_api_key: true,
      skip_tls_verify: false,
      tested_models: ['gpt-x', 'gpt-y'],
      available_models: ['gpt-x', 'gpt-y'],
    },
  ])
  vi.mocked(api.smartMappings.approve).mockReset()
  vi.mocked(api.smartMappings.reject).mockReset()
  vi.mocked(api.smartMappings.reenable).mockReset()
  vi.mocked(api.smartMappings.createJob).mockReset()
  vi.mocked(api.smartMappings.listProposals).mockReset()
  vi.mocked(api.smartMappings.archive).mockReset()
  vi.mocked(api.smartMappings.archive).mockResolvedValue({ proposal_id: 1, archived: true })
  // prompts-032 Phase D: default no active consolidated mapping.
  vi.mocked(api.smartMappings.getActive).mockResolvedValue({ active: null })
  vi.mocked(api.smartMappings.runActive).mockReset()
})

describe('SmartMappings page', () => {
  it('renders the empty state when no proposals exist', async () => {
    vi.mocked(api.smartMappings.listProposals).mockResolvedValue([])
    renderPage()
    expect(await screen.findByText(/No proposals yet/i)).toBeInTheDocument()
  })

  it('renders a proposal row with mapping pairs and pending action buttons', async () => {
    vi.mocked(api.smartMappings.listProposals).mockResolvedValue([makeProposal()])
    renderPage()
    // Wait for the actions row to appear — proves the proposal rendered.
    expect(await screen.findByRole('button', { name: /^Approve$/i })).toBeInTheDocument()
    // "feed-a" and "pending" also appear in dropdown <option>s; assert
    // via getAllByText with length >= 1 to avoid the multi-match error.
    expect(screen.getAllByText('feed-a').length).toBeGreaterThan(0)
    expect(screen.getAllByText(/pending/i).length).toBeGreaterThan(0)
    // mapping pairs rendered (raw → canonical)
    expect(screen.getByText('title')).toBeInTheDocument()
    expect(screen.getByText('indicator')).toBeInTheDocument()
    // prompts-056: only a single Approve action remains.
    expect(screen.queryByRole('button', { name: /set mode=manual/i })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Reject$/i })).toBeInTheDocument()
  })

  it('Approve button calls approve (no set_mode_manual flag)', async () => {
    vi.mocked(api.smartMappings.listProposals).mockResolvedValue([makeProposal()])
    vi.mocked(api.smartMappings.approve).mockResolvedValue({
      proposal_id: 1, source: 'feed-a', added: [], skipped_conflicts: [],
      mode: 'auto', mode_changed: false,
    })
    renderPage()
    const btn = await screen.findByRole('button', { name: /^Approve$/i })
    fireEvent.click(btn)
    await waitFor(() => {
      expect(api.smartMappings.approve).toHaveBeenCalledWith(1, {})
    })
  })

  it('surfaces the approve-response hint when normalizer is in auto mode', async () => {
    vi.mocked(api.smartMappings.listProposals).mockResolvedValue([makeProposal()])
    vi.mocked(api.smartMappings.approve).mockResolvedValue({
      proposal_id: 1, source: 'feed-a', added: [], skipped_conflicts: [],
      mode: 'auto', mode_changed: false,
      hint: 'Normalizer mode is auto; manual_mappings will be ignored.',
    })
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /^Approve$/i }))
    expect(
      await screen.findByText(/Normalizer mode is auto/i),
    ).toBeInTheDocument()
  })

  // prompts-033: the '__total__' summary sentinel must never appear as a feed.
  it('excludes the __total__ sentinel from the source filter', async () => {
    vi.mocked(api.getSummary).mockResolvedValue([
      { source: 'feed-a', count: 10 },
      { source: '__total__', count: 15 },
    ])
    vi.mocked(api.smartMappings.listProposals).mockResolvedValue([])
    renderPage()
    await screen.findByText(/No proposals yet/i)
    expect(screen.queryByRole('option', { name: '__total__' })).not.toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'feed-a' })).toBeInTheDocument()
  })
})

// prompts-038: active-mapping card name + RUN, default outcome filter
describe('SmartMappings page — active mapping card (prompts-038)', () => {
  const activeFixture = {
    active: {
      id: 7,
      sources: ['feed-a', 'feed-b'],
      field_count: 3,
      field_scope: 'all' as const,
      proposal_id: 42,
      proposal_name: 'Proposal-2026-05-30T12:00:00Z',
      created_at: '2026-05-30T12:00:00Z',
      note: null,
    },
  }

  it('shows the active proposal name on the card', async () => {
    vi.mocked(api.smartMappings.getActive).mockResolvedValue(activeFixture)
    vi.mocked(api.smartMappings.listProposals).mockResolvedValue([])
    renderPage()
    expect(
      await screen.findByText('Proposal-2026-05-30T12:00:00Z'),
    ).toBeInTheDocument()
  })

  it('Run button re-applies the active mapping and surfaces the counters', async () => {
    vi.mocked(api.smartMappings.getActive).mockResolvedValue(activeFixture)
    vi.mocked(api.smartMappings.listProposals).mockResolvedValue([])
    vi.mocked(api.smartMappings.runActive).mockResolvedValue({
      reset_rows: 5, processed: 5, inserted: 5, errors: 0,
    })
    renderPage()
    const runBtn = await screen.findByRole('button', { name: 'Run' })
    fireEvent.click(runBtn)
    await waitFor(() => expect(api.smartMappings.runActive).toHaveBeenCalledTimes(1))
    expect(await screen.findByText(/processed 5/i)).toBeInTheDocument()
  })

  it('defaults the outcome filter to pending + approved only', async () => {
    vi.mocked(api.smartMappings.listProposals).mockResolvedValue([
      makeProposal({ id: 1, source_name: 'feed-a', status: 'pending', outcome: 'pending_review' }),
      makeProposal({ id: 2, source_name: 'feed-b', status: 'approved', outcome: 'approved' }),
      makeProposal({ id: 3, source_name: 'feed-c', status: 'rejected', outcome: 'rejected', mapping: {} }),
    ])
    renderPage()
    // Pending row visible → exactly one Approve button (approved/rejected rows
    // have no Approve action).
    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: /^Approve$/i }).length).toBe(1),
    )
    // feed-c is only the rejected proposal's label (not a summary option), so
    // its absence proves the rejected row is filtered out by default.
    expect(screen.queryByText('feed-c')).not.toBeInTheDocument()
  })
})

// prompts-033: error-log detail popup
describe('SmartMappings page — error-log popup', () => {
  it('opens the LLM log popup from a no-mapping row', async () => {
    const p = makeProposal({
      id: 21,
      status: 'error',
      mapping: {},
      prompt_system: 'SYS-PROMPT-TEXT',
      prompt_user: 'USER-PROMPT-TEXT',
      llm_response_raw: 'RAW-MODEL-OUTPUT',
      llm_request_raw: 'POST https://h/v1/chat\n\nREQUEST-BODY-TEXT',
      llm_response_json: 'HTTP 200\n\nFULL-RESPONSE-ENVELOPE',
    })
    vi.mocked(api.smartMappings.listProposals).mockResolvedValue([p])
    renderPage()
    const link = await screen.findByRole('button', { name: /view LLM log/i })
    fireEvent.click(link)
    const dialog = await screen.findByRole('dialog')
    expect(dialog.textContent).toContain('Proposal #21')
    expect(dialog.textContent).toContain('SYS-PROMPT-TEXT')
    expect(dialog.textContent).toContain('USER-PROMPT-TEXT')
    expect(dialog.textContent).toContain('RAW-MODEL-OUTPUT')
    // prompts-037: the raw HTTP request + full response envelope are shown.
    expect(dialog.textContent).toContain('REQUEST-BODY-TEXT')
    expect(dialog.textContent).toContain('FULL-RESPONSE-ENVELOPE')
    expect(dialog.textContent).toContain('llm_request_raw')
    expect(dialog.textContent).toContain('llm_response_json')
  })

  it('exposes a View LLM log link on error rows that still parsed a mapping', async () => {
    const p = makeProposal({
      id: 22,
      status: 'error',
      mapping: { a: 'title' },
      llm_response_raw: 'PARTIAL-RAW',
    })
    vi.mocked(api.smartMappings.listProposals).mockResolvedValue([p])
    renderPage()
    const link = await screen.findByRole('button', { name: /View LLM log/i })
    fireEvent.click(link)
    expect((await screen.findByRole('dialog')).textContent).toContain('PARTIAL-RAW')
  })
})

// prompts-033: optimistic Processing row + job poll
describe('SmartMappings page — processing row', () => {
  function makeJob(state: 'running' | 'done' | 'error', error_msg: string | null = null) {
    return {
      id: 'jX', source: '__consolidated__', kind: 'smart_proposal',
      state, step: 'normalising' as const, processed: 0, total: 0,
      counters: {}, first_ingest: false, started_at: 0, finished_at: null,
      error_msg,
    }
  }

  async function generate() {
    renderPage()
    // Open the modal via the page-level button (only one before modal opens).
    // It's disabled until the summary query populates the feed list.
    const openBtn = await screen.findByRole('button', { name: /Generate proposal/i })
    await waitFor(() => expect(openBtn).not.toBeDisabled())
    fireEvent.click(openBtn)
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: 'feed-a', pressed: false }))
    fireEvent.click(within(dialog).getByRole('button', { name: /Generate proposal/i }))
  }

  it('shows an optimistic Processing row while the job runs', async () => {
    vi.mocked(api.smartMappings.listProposals).mockResolvedValue([])
    vi.mocked(api.smartMappings.createJob).mockResolvedValue({
      job_id: 'jX', sources: ['feed-a'], field_scope: 'all', state: 'running',
    })
    vi.mocked(api.smartMappings.getJob).mockResolvedValue(makeJob('running'))
    await generate()
    const row = await screen.findByTestId('processing-jX')
    expect(row.textContent).toContain('Processing')
  })

  // prompts-034 crit-4: the running row surfaces the submitted job parameters —
  // feeds, provider, model, scope, and sample size.
  it('shows the job parameters (provider + sample size) on the Processing row', async () => {
    vi.mocked(api.smartMappings.listProposals).mockResolvedValue([])
    vi.mocked(api.smartMappings.createJob).mockResolvedValue({
      job_id: 'jX', sources: ['feed-a'], provider: 'openai', sample_size: 15,
      model: 'gpt-4o-mini', field_scope: 'configured', state: 'running',
    })
    vi.mocked(api.smartMappings.getJob).mockResolvedValue(makeJob('running'))
    await generate()
    const row = await screen.findByTestId('processing-jX')
    expect(row.textContent).toContain('openai')
    expect(row.textContent).toContain('gpt-4o-mini')
    expect(row.textContent).toContain('configured')
    expect(row.textContent).toContain('sample: 15')
  })

  it('removes the Processing row and refreshes when the job completes', async () => {
    vi.mocked(api.smartMappings.listProposals).mockResolvedValue([])
    vi.mocked(api.smartMappings.createJob).mockResolvedValue({
      job_id: 'jX', sources: ['feed-a'], field_scope: 'all', state: 'running',
    })
    vi.mocked(api.smartMappings.getJob).mockResolvedValue(makeJob('done'))
    await generate()
    // The job resolves 'done' on first poll → the optimistic row disappears.
    await waitFor(() =>
      expect(screen.queryByTestId('processing-jX')).not.toBeInTheDocument(),
    )
  })

  it('keeps an errored Processing row visible with the failure message', async () => {
    vi.mocked(api.smartMappings.listProposals).mockResolvedValue([])
    vi.mocked(api.smartMappings.createJob).mockResolvedValue({
      job_id: 'jX', sources: ['feed-a'], field_scope: 'all', state: 'running',
    })
    vi.mocked(api.smartMappings.getJob).mockResolvedValue(makeJob('error', 'model exploded'))
    await generate()
    const row = await screen.findByTestId('processing-jX')
    await waitFor(() => expect(row.textContent).toContain('model exploded'))
    // Dismiss removes it.
    fireEvent.click(within(row).getByRole('button', { name: /Dismiss/i }))
    await waitFor(() =>
      expect(screen.queryByTestId('processing-jX')).not.toBeInTheDocument(),
    )
  })

  // prompts-034: a job that errors without inserting a proposal still exposes
  // its details (error_msg) via a "View details" link → shared error modal.
  it('exposes a working View details link on a job-error row', async () => {
    vi.mocked(api.smartMappings.listProposals).mockResolvedValue([])
    vi.mocked(api.smartMappings.createJob).mockResolvedValue({
      job_id: 'jX', sources: ['feed-a'], field_scope: 'all', state: 'running',
    })
    vi.mocked(api.smartMappings.getJob).mockResolvedValue(makeJob('error', 'boom: no proposal row'))
    await generate()
    const row = await screen.findByTestId('processing-jX')
    const link = await within(row).findByRole('button', { name: /View details/i })
    fireEvent.click(link)
    const dialog = await screen.findByRole('dialog')
    expect(dialog.textContent).toContain('Proposal job failed')
    expect(dialog.textContent).toContain('boom: no proposal row')
  })

  // prompts-049: an in-flight job must survive navigating away from Smart
  // Mappings and back. The active-job set lives in the app-global QueryClient
  // cache (gcTime Infinity), so unmounting + remounting the page under the
  // same client re-renders the Processing row rather than losing it.
  it('keeps the Processing row after the page unmounts and remounts', async () => {
    vi.mocked(api.smartMappings.listProposals).mockResolvedValue([])
    vi.mocked(api.smartMappings.createJob).mockResolvedValue({
      job_id: 'jX', sources: ['feed-a'], field_scope: 'all', state: 'running',
    })
    vi.mocked(api.smartMappings.getJob).mockResolvedValue(makeJob('running'))

    // Shared client = the app-global QueryClient that persists across nav.
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const view = render(
      <QueryClientProvider client={qc}>
        <SmartMappings />
      </QueryClientProvider>,
    )
    const openBtn = await screen.findByRole('button', { name: /Generate proposal/i })
    await waitFor(() => expect(openBtn).not.toBeDisabled())
    fireEvent.click(openBtn)
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: 'feed-a', pressed: false }))
    fireEvent.click(within(dialog).getByRole('button', { name: /Generate proposal/i }))
    await screen.findByTestId('processing-jX')

    // Navigate away (unmount) then back (remount) under the same client.
    view.unmount()
    render(
      <QueryClientProvider client={qc}>
        <SmartMappings />
      </QueryClientProvider>,
    )
    // The optimistic row is restored from the persisted active-job set.
    expect(await screen.findByTestId('processing-jX')).toBeInTheDocument()
  })
})

describe('SmartProposalConfirmModal', () => {
  function renderModal(onClose = vi.fn(), onCreated = vi.fn()) {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={qc}>
        <SmartProposalConfirmModal
          sources={['feed-a', 'feed-b']}
          onClose={onClose}
          onCreated={onCreated}
        />
      </QueryClientProvider>,
    )
    return { onClose, onCreated }
  }

  it('disables submit until at least one feed is selected', async () => {
    renderModal()
    const submit = screen.getByRole('button', { name: /Generate proposal/i })
    expect(submit).toBeDisabled()
    // Select a feed chip → submit enables.
    fireEvent.click(screen.getByRole('button', { name: 'feed-a', pressed: false }))
    await waitFor(() => expect(submit).not.toBeDisabled())
  })

  it('calls createJob with the consolidated payload on submit', async () => {
    vi.mocked(api.smartMappings.createJob).mockResolvedValue({
      job_id: 'j1', sources: ['feed-a'], field_scope: 'all', state: 'running',
    })
    const { onClose, onCreated } = renderModal()
    fireEvent.click(screen.getByRole('button', { name: 'feed-a', pressed: false }))
    fireEvent.click(screen.getByRole('button', { name: /Generate proposal/i }))
    await waitFor(() => {
      expect(api.smartMappings.createJob).toHaveBeenCalledWith({
        sources: ['feed-a'],
        provider: undefined,
        model: undefined,
        sample_size: 10,
        field_scope: 'all',
      })
    })
    await waitFor(() => expect(onCreated).toHaveBeenCalled())
    await waitFor(() => expect(onClose).toHaveBeenCalled())
  })

  it('passes the chosen model (provider + model) and field scope', async () => {
    vi.mocked(api.smartMappings.createJob).mockResolvedValue({
      job_id: 'j2', sources: ['feed-a', 'feed-b'], field_scope: 'configured', state: 'running',
    })
    renderModal()
    // Wait for the model dropdown to populate "provider · model" pairs from
    // each provider's available_models (prompts-036).
    await screen.findByRole('option', { name: /openai · gpt-y/i })
    fireEvent.click(screen.getByRole('button', { name: 'feed-a', pressed: false }))
    fireEvent.click(screen.getByRole('button', { name: 'feed-b', pressed: false }))
    // Option value is the index into the flattened model list; gpt-y is index 1.
    fireEvent.change(screen.getByLabelText(/Model/i), { target: { value: '1' } })
    fireEvent.click(screen.getByLabelText(/Only configured fields/i))
    fireEvent.click(screen.getByRole('button', { name: /Generate proposal/i }))
    await waitFor(() => {
      expect(api.smartMappings.createJob).toHaveBeenCalledWith({
        sources: ['feed-a', 'feed-b'],
        provider: 'openai',
        model: 'gpt-y',
        sample_size: 10,
        field_scope: 'configured',
      })
    })
  })

  // prompts-033: one-click select-all selects every feed.
  it('Select all selects every feed; clearing deselects all', async () => {
    renderModal()
    const selectAll = screen.getByRole('button', { name: /Select all/i })
    fireEvent.click(selectAll)
    await waitFor(() =>
      expect(screen.getByText(/Feeds \(2 selected\)/i)).toBeInTheDocument(),
    )
    // Button now toggles to "Clear all".
    fireEvent.click(screen.getByRole('button', { name: /Clear all/i }))
    await waitFor(() =>
      expect(screen.getByText(/Feeds \(0 selected\)/i)).toBeInTheDocument(),
    )
  })
})

// prompts-021E-4: outcome badges + audit toggle
describe('SmartMappings page — 021E-4 scoring/outcome UI', () => {
  it('renders score delta, outcome badge, and trigger_reason for an auto-applied proposal', async () => {
    const p = makeProposal({
      id: 7,
      status: 'approved',
      outcome: 'auto_applied',
      auto_applied: true,
      trigger_reason: 'on_new_feed',
      score: 0.42,
      score_breakdown: { coverage_before: 0.1, coverage_after: 0.52, coverage_delta: 0.42 },
    })
    vi.mocked(api.smartMappings.listProposals).mockResolvedValue([p])
    renderPage()
    await clearOutcomeFilter()
    // Wait for the row to render via its delta text (uniquely identifies the row).
    expect(await screen.findByText(/Δ42\.0%/)).toBeInTheDocument()
    // Outcome badge — its <span> title="auto-applied by smart-mode" disambiguates.
    expect(screen.getByTitle(/auto-applied by smart-mode/i)).toBeInTheDocument()
    expect(screen.getByText('on_new_feed')).toBeInTheDocument()
  })

  it('renders discarded badge and does not render approve buttons on non-pending rows', async () => {
    const p = makeProposal({
      id: 8,
      status: 'rejected',
      outcome: 'discarded_below_threshold',
      auto_applied: false,
      trigger_reason: 'schedule',
      score: 0.01,
    })
    vi.mocked(api.smartMappings.listProposals).mockResolvedValue([p])
    renderPage()
    await clearOutcomeFilter()
    // Title attribute uniquely identifies the discard badge.
    expect(await screen.findByTitle(/below coverage_delta threshold/i)).toBeInTheDocument()
    // Pending-only action buttons must NOT render.
    expect(screen.queryByRole('button', { name: /^Approve$/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Reject$/i })).not.toBeInTheDocument()
  })

  it('always fetches outcome="all" and filters client-side by outcome', async () => {
    const pending = makeProposal({ id: 1, status: 'pending', outcome: 'pending_review' })
    const approved = makeProposal({
      id: 2, status: 'approved', outcome: 'approved', mapping: { a: 'title' },
    })
    vi.mocked(api.smartMappings.listProposals).mockResolvedValue([pending, approved])
    renderPage()
    await clearOutcomeFilter()
    // The query always requests outcome='all' (client-side filtering) and, by
    // default, only active (non-archived) proposals.
    await waitFor(() => {
      expect(api.smartMappings.listProposals).toHaveBeenCalledWith({
        source: undefined,
        outcome: 'all',
        archived: 'active',
      })
    })
    // Both rows visible with no outcome selected.
    expect(await screen.findByRole('button', { name: /^Approve$/i })).toBeInTheDocument()
    // Select only 'approved' → the pending row (with Approve button) disappears.
    fireEvent.click(screen.getByRole('button', { name: 'approved', pressed: false }))
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /^Approve$/i })).not.toBeInTheDocument(),
    )
  })

  it('does not render outcome badge for pending_review (default state)', async () => {
    const p = makeProposal({
      outcome: 'pending_review',
      trigger_reason: 'manual',
      score: 0.15,
    })
    vi.mocked(api.smartMappings.listProposals).mockResolvedValue([p])
    renderPage()
    await screen.findByRole('button', { name: /^Approve$/i })
    // Neither outcome badge title appears for pending_review.
    expect(screen.queryByTitle(/auto-applied by smart-mode/i)).not.toBeInTheDocument()
    expect(screen.queryByTitle(/below coverage_delta threshold/i)).not.toBeInTheDocument()
  })
})

// prompts-032 Phase D: active-mapping card + re-enable flow
describe('SmartMappings page — Phase D active card + re-enable', () => {
  it('renders the active consolidated mapping card when one is active', async () => {
    vi.mocked(api.smartMappings.listProposals).mockResolvedValue([])
    vi.mocked(api.smartMappings.getActive).mockResolvedValue({
      active: {
        id: 3,
        sources: ['feed-a', 'feed-b'],
        field_count: 5,
        field_scope: 'configured',
        proposal_id: 9,
        created_at: '2025-02-02T00:00:00Z',
        note: null,
      },
    })
    renderPage()
    // prompts-038: the active card is collapsed by default; expand to reveal
    // the field/feed details and version footer.
    fireEvent.click(await screen.findByLabelText('Expand details'))
    // The feed list (title attr) only exists on the populated card — await it.
    const feedsEl = await screen.findByTitle('feed-a, feed-b')
    const card = feedsEl.closest('.card')!
    expect(card.textContent).toContain('Active consolidated mapping')
    expect(card.textContent).toContain('5')
    expect(card.textContent).toContain('configured')
    expect(card.textContent).toContain('version #3')
  })

  it('renders the empty active-card placeholder when no mapping is active', async () => {
    vi.mocked(api.smartMappings.listProposals).mockResolvedValue([])
    vi.mocked(api.smartMappings.getActive).mockResolvedValue({ active: null })
    renderPage()
    expect(
      await screen.findByText(/No active consolidated mapping yet/i),
    ).toBeInTheDocument()
  })

  it('shows Re-enable on an operator-rejected row and calls reenable', async () => {
    const p = makeProposal({ id: 11, status: 'rejected', outcome: 'rejected' })
    vi.mocked(api.smartMappings.listProposals).mockResolvedValue([p])
    vi.mocked(api.smartMappings.reenable).mockResolvedValue({
      proposal_id: 11, status: 'pending',
    })
    renderPage()
    await clearOutcomeFilter()
    const btn = await screen.findByRole('button', { name: /Re-enable/i })
    fireEvent.click(btn)
    await waitFor(() => {
      expect(api.smartMappings.reenable).toHaveBeenCalledWith(11, {})
    })
  })

  it('does not show Re-enable on an auto-discarded row', async () => {
    const p = makeProposal({
      id: 12, status: 'rejected', outcome: 'discarded_below_threshold',
    })
    vi.mocked(api.smartMappings.listProposals).mockResolvedValue([p])
    renderPage()
    await clearOutcomeFilter()
    // Wait for the row (discard badge) then assert no re-enable button.
    await screen.findByTitle(/below coverage_delta threshold/i)
    expect(screen.queryByRole('button', { name: /Re-enable/i })).not.toBeInTheDocument()
  })
})

// prompts-042: active-card feed names + Approved/Active vs Approved/Inactive.
describe('SmartMappings page — prompts-042 names + active badges', () => {
  it('lists feed names (not just a count) on the active card', async () => {
    vi.mocked(api.smartMappings.listProposals).mockResolvedValue([])
    vi.mocked(api.smartMappings.getActive).mockResolvedValue({
      active: {
        id: 8,
        sources: ['feed-a', 'feed-b'],
        field_count: 4,
        field_scope: 'all',
        proposal_id: 80,
        created_at: '2026-05-30T00:00:00Z',
        note: null,
      },
    })
    renderPage()
    fireEvent.click(await screen.findByLabelText('Expand details'))
    const feedsEl = await screen.findByTitle('feed-a, feed-b')
    const card = feedsEl.closest('.card')!
    // The names render as visible text, not only in the title attribute.
    expect(card.textContent).toContain('feed-a, feed-b')
  })

  it('badges the active-backing proposal green (Approved / Active)', async () => {
    const approved = makeProposal({
      id: 42, status: 'approved', outcome: 'approved', mapping: { a: 'title' },
    })
    vi.mocked(api.smartMappings.listProposals).mockResolvedValue([approved])
    vi.mocked(api.smartMappings.getActive).mockResolvedValue({
      active: {
        id: 9, sources: ['feed-a'], field_count: 1, field_scope: 'all',
        proposal_id: 42, created_at: '2026-05-30T00:00:00Z', note: null,
      },
    })
    renderPage()
    expect(await screen.findByText('Approved / Active')).toBeInTheDocument()
    expect(screen.queryByText('Approved / Inactive')).not.toBeInTheDocument()
  })

  it('badges a superseded approved proposal blue (Approved / Inactive)', async () => {
    const approved = makeProposal({
      id: 43, status: 'approved', outcome: 'approved', mapping: { a: 'title' },
    })
    vi.mocked(api.smartMappings.listProposals).mockResolvedValue([approved])
    vi.mocked(api.smartMappings.getActive).mockResolvedValue({
      active: {
        id: 9, sources: ['feed-a'], field_count: 1, field_scope: 'all',
        proposal_id: 99, created_at: '2026-05-30T00:00:00Z', note: null,
      },
    })
    renderPage()
    expect(await screen.findByText('Approved / Inactive')).toBeInTheDocument()
    expect(screen.queryByText('Approved / Active')).not.toBeInTheDocument()
  })
})

// prompts-034 Phase E: archive action (confirm-gated) + archived filter.
describe('SmartMappings page — Phase E archive + filter', () => {
  it('gates archive behind an in-card confirmation', async () => {
    const p = makeProposal({ id: 20, status: 'pending' })
    vi.mocked(api.smartMappings.listProposals).mockResolvedValue([p])
    renderPage()
    // Click Archive → no API call yet, confirm panel appears.
    fireEvent.click(await screen.findByTestId('archive-20'))
    expect(api.smartMappings.archive).not.toHaveBeenCalled()
    expect(screen.getByTestId('archive-confirm-20')).toBeInTheDocument()
    // Confirm → archive(id, {}) is called.
    fireEvent.click(screen.getByTestId('archive-confirm-yes-20'))
    await waitFor(() => {
      expect(api.smartMappings.archive).toHaveBeenCalledWith(20, {})
    })
  })

  it('lets the operator cancel the archive confirmation', async () => {
    const p = makeProposal({ id: 21, status: 'pending' })
    vi.mocked(api.smartMappings.listProposals).mockResolvedValue([p])
    renderPage()
    fireEvent.click(await screen.findByTestId('archive-21'))
    fireEvent.click(screen.getByRole('button', { name: /Cancel/i }))
    await waitFor(() =>
      expect(screen.queryByTestId('archive-confirm-21')).not.toBeInTheDocument(),
    )
    expect(api.smartMappings.archive).not.toHaveBeenCalled()
  })

  it('renders an archived badge (not an Archive button) for archived rows', async () => {
    const p = makeProposal({ id: 22, status: 'rejected', outcome: 'rejected', archived: true })
    vi.mocked(api.smartMappings.listProposals).mockResolvedValue([p])
    renderPage()
    await clearOutcomeFilter()
    await screen.findByText('#22', { exact: false })
    expect(screen.queryByTestId('archive-22')).not.toBeInTheDocument()
    expect(screen.getByText('archived')).toBeInTheDocument()
  })

  it('toggling "Show archived" refetches including archived proposals', async () => {
    vi.mocked(api.smartMappings.listProposals).mockResolvedValue([])
    renderPage()
    // Initial load uses the default active-only filter.
    await waitFor(() =>
      expect(api.smartMappings.listProposals).toHaveBeenCalledWith({
        source: undefined,
        outcome: 'all',
        archived: 'active',
      }),
    )
    fireEvent.click(screen.getByTestId('toggle-archived'))
    await waitFor(() =>
      expect(api.smartMappings.listProposals).toHaveBeenCalledWith({
        source: undefined,
        outcome: 'all',
        archived: 'all',
      }),
    )
  })
})
