/**
 * Focused tests for SmartProposalConfirmModal's model dropdown source
 * (prompts-036).
 *
 * The proposal dropdown is now populated from each provider's DISCOVERED
 * catalog (`available_models`) rather than `tested_models`, so a model is
 * selectable as soon as it is discovered — no green Test Connection required.
 * A bad model surfaces at proposal request/response time, not pre-flight.
 *
 * Covered:
 *   1. Dropdown lists "provider · model" from available_models even when
 *      tested_models is empty.
 *   2. The empty-state hint shows only when NO provider has any discovered
 *      model.
 *   3. Selecting a discovered model threads it into createJob({provider, model}).
 */
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, it, expect, vi, beforeEach } from 'vitest'

import type { LLMProviderSummary, SmartJobHandle } from '../api/client'

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...actual,
    api: {
      llm: {
        listProviders: vi.fn(),
      },
      smartMappings: {
        createJob: vi.fn(),
      },
    },
  }
})

import SmartProposalConfirmModal from '../components/SmartProposalConfirmModal'
import { api } from '../api/client'

function renderModal(sources: string[] = ['feedA']) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <SmartProposalConfirmModal
        sources={sources}
        onClose={() => {}}
        onCreated={() => {}}
      />
    </QueryClientProvider>,
  )
}

const handle: SmartJobHandle = {
  job_id: 'j1',
  sources: ['feedA'],
  field_scope: 'all',
  state: 'queued',
}

beforeEach(() => {
  vi.mocked(api.llm.listProviders).mockReset()
  vi.mocked(api.smartMappings.createJob).mockReset()
  vi.mocked(api.smartMappings.createJob).mockResolvedValue(handle)
})

describe('SmartProposalConfirmModal model dropdown (prompts-036)', () => {
  it('populates the dropdown from available_models even when tested_models is empty', async () => {
    const providers: LLMProviderSummary[] = [
      {
        name: 'cdtnew',
        kind: 'openai_compatible',
        model: 'gpt-oss:120b',
        has_api_key: true,
        skip_tls_verify: true,
        tested_models: [], // empty — would have left the old dropdown blank
        available_models: ['gpt-oss:120b', 'llama3.1:70b'],
      },
    ]
    vi.mocked(api.llm.listProviders).mockResolvedValue(providers)

    renderModal()

    const select = (await screen.findByLabelText('Model')) as HTMLSelectElement
    // Configured-default + the two discovered models.
    await within(select).findByRole('option', { name: 'cdtnew · gpt-oss:120b' })
    within(select).getByRole('option', { name: 'cdtnew · llama3.1:70b' })
    within(select).getByRole('option', { name: 'Configured default' })
    // The empty-state hint must NOT appear when discovered models exist.
    expect(screen.queryByText(/No discovered models yet/i)).toBeNull()
  })

  it('shows the empty-state hint only when no provider has any discovered model', async () => {
    const providers: LLMProviderSummary[] = [
      {
        name: 'p1',
        kind: 'openai',
        model: 'gpt-4o',
        has_api_key: true,
        skip_tls_verify: false,
        tested_models: ['gpt-4o'], // tested but NOT discovered → not offered
        available_models: [],
      },
    ]
    vi.mocked(api.llm.listProviders).mockResolvedValue(providers)

    renderModal()

    await screen.findByText(/No discovered models yet/i)
    const select = screen.getByLabelText('Model') as HTMLSelectElement
    // Only the configured-default option — the tested-but-undiscovered model
    // is intentionally absent.
    expect(within(select).queryByRole('option', { name: /p1 ·/ })).toBeNull()
  })

  it('threads the selected discovered model into createJob', async () => {
    const providers: LLMProviderSummary[] = [
      {
        name: 'cdtnew',
        kind: 'openai_compatible',
        model: 'gpt-oss:120b',
        has_api_key: true,
        skip_tls_verify: true,
        available_models: ['gpt-oss:120b', 'llama3.1:70b'],
      },
    ]
    vi.mocked(api.llm.listProviders).mockResolvedValue(providers)

    renderModal(['feedA'])

    const select = (await screen.findByLabelText('Model')) as HTMLSelectElement
    // Wait for the discovered options to render before selecting.
    await within(select).findByRole('option', { name: 'cdtnew · llama3.1:70b' })
    // Pick the second discovered model (index 1 in modelOptions).
    fireEvent.change(select, { target: { value: '1' } })

    // A feed must be selected before the job can be generated.
    fireEvent.click(screen.getByRole('button', { name: 'feedA' }))

    fireEvent.click(screen.getByRole('button', { name: /Generate proposal/i }))

    await waitFor(() => expect(api.smartMappings.createJob).toHaveBeenCalledTimes(1))
    expect(api.smartMappings.createJob).toHaveBeenCalledWith(
      expect.objectContaining({
        sources: ['feedA'],
        provider: 'cdtnew',
        model: 'llama3.1:70b',
      }),
    )
  })
})
