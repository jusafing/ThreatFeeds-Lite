/**
 * Focused tests for the per-card Discover-then-Probe staging added in
 * prompts-027 step 5. The companion tab-level suite in
 * `llmProvidersTab.test.tsx` covers the legacy acceptance criteria and
 * the high-level gate handshake; this file exercises the ProviderCard
 * behaviour in detail:
 *
 *   1. "Default model to use" dropdown is populated from
 *      ``available_models`` on first paint (no Discover click required).
 *   2. "Discover Models" persists the discovered list by calling
 *      ``discoverProvider`` and then PUTting the new
 *      ``available_models`` array (with stored-key merge via "***").
 *   3. "Discover Models" surfaces the canonical 023 verdict inline
 *      when the upstream returns zero models.
 *   4. Editing any field after a green probe re-locks Save
 *      (probe-since-edit gate).
 *   5. Anthropic kind keeps a free-text model input (no dropdown) even
 *      when ``available_models`` is unset.
 *   6. The "No models discovered" hint replaces the dropdown when the
 *      provider has neither a discovered list nor the Anthropic branch.
 *   7. Models present on-disk but absent from ``available_models``
 *      still appear in the dropdown with a "(not in discovered list)"
 *      suffix so the operator can see the value currently in effect.
 */
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, it, expect, vi, beforeEach } from 'vitest'

import type { LLMConfig, LLMProviderSummary } from '../api/client'

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...actual,
    api: {
      llm: {
        getConfig: vi.fn(),
        setConfig: vi.fn(),
        listProviders: vi.fn(),
        testProvider: vi.fn(),
        testProviderDraft: vi.fn(),
        discoverDraft: vi.fn(),
        discoverProvider: vi.fn(),
        addProvider: vi.fn(),
        updateProvider: vi.fn(),
        deleteProvider: vi.fn(),
      },
    },
  }
})

import LLMProvidersTab from '../pages/configuration/LLMProvidersTab'
import { api } from '../api/client'

function renderTab() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <LLMProvidersTab />
    </QueryClientProvider>,
  )
}

/** prompts-038: provider cards render collapsed to a one-line summary.
 *  Expand the `p1` card before asserting on its editable body. */
async function expandP1() {
  fireEvent.click(await screen.findByRole('button', { name: 'Expand p1' }))
}

const _summary: LLMProviderSummary[] = [
  { name: 'p1', kind: 'openai', model: 'gpt-4o-mini', has_api_key: true, skip_tls_verify: false },
]

/** Builds a one-provider LLMConfig with sensible defaults; overrides
 *  on the single provider via `provider` partial. */
function makeConfig(
  provider: Partial<LLMConfig['providers'][number]> = {},
): LLMConfig {
  return {
    enabled: true,
    default_provider: 'p1',
    providers: [
      {
        name: 'p1',
        kind: 'openai',
        base_url: 'https://api.openai.com/v1',
        api_key: '***',
        model: 'gpt-4o-mini',
        timeout_seconds: 30,
        max_retries: 2,
        skip_tls_verify: false,
        available_models: ['gpt-4o-mini', 'gpt-4o'],
        ...provider,
      },
    ],
  }
}

beforeEach(() => {
  vi.mocked(api.llm.getConfig).mockReset()
  vi.mocked(api.llm.setConfig).mockReset()
  vi.mocked(api.llm.testProvider).mockReset()
  vi.mocked(api.llm.testProviderDraft).mockReset()
  vi.mocked(api.llm.discoverDraft).mockReset()
  vi.mocked(api.llm.discoverProvider).mockReset()
  vi.mocked(api.llm.listProviders).mockReset()
  vi.mocked(api.llm.addProvider).mockReset()
  vi.mocked(api.llm.updateProvider).mockReset()
  vi.mocked(api.llm.deleteProvider).mockReset()
  vi.mocked(api.llm.listProviders).mockResolvedValue(_summary)
})

describe('ProviderCard (027 step 5: Discover-then-Probe per-card surface)', () => {
  it('renders the "Default model to use" dropdown from available_models on first paint', async () => {
    vi.mocked(api.llm.getConfig).mockResolvedValue(makeConfig())
    renderTab()
    await expandP1()
    const select = await screen.findByLabelText(/Default model to use/i) as HTMLSelectElement
    expect(select.tagName).toBe('SELECT')
    // Both discovered models present as <option>s.
    expect(within(select).getByRole('option', { name: 'gpt-4o-mini' })).toBeInTheDocument()
    expect(within(select).getByRole('option', { name: 'gpt-4o' })).toBeInTheDocument()
    // The on-disk model is the initial value.
    expect(select.value).toBe('gpt-4o-mini')
    // No "Discover Models" click was required.
    expect(api.llm.discoverProvider).not.toHaveBeenCalled()
  })

  it('"Discover Models" calls discoverProvider then PUTs the new available_models list', async () => {
    vi.mocked(api.llm.getConfig).mockResolvedValue(makeConfig({ available_models: undefined }))
    vi.mocked(api.llm.discoverProvider).mockResolvedValue({
      status: 'ok',
      details: [],
      models: ['gpt-4o', 'gpt-4o-mini', 'o1-mini'],
    })
    vi.mocked(api.llm.updateProvider).mockResolvedValue({
      name: 'p1', kind: 'openai', model: 'gpt-4o-mini',
      has_api_key: true, skip_tls_verify: false,
    })
    renderTab()
    await expandP1()
    fireEvent.click(await screen.findByRole('button', { name: /Discover Models/i }))
    await waitFor(() => expect(api.llm.discoverProvider).toHaveBeenCalledWith('p1'))
    // Persist call: PUT carries the newly-discovered list and the
    // stored-key sentinel because the operator did not type a new key.
    await waitFor(() => expect(api.llm.updateProvider).toHaveBeenCalledTimes(1))
    const [name, payload] = vi.mocked(api.llm.updateProvider).mock.calls[0]
    expect(name).toBe('p1')
    expect(payload.available_models).toEqual(['gpt-4o', 'gpt-4o-mini', 'o1-mini'])
    expect(payload.api_key).toBe('***')
    // Success note rendered.
    expect(await screen.findByText(/Discovered 3 models\./i)).toBeInTheDocument()
  })

  it('"Discover Models" surfaces the inline error when the upstream returns zero models', async () => {
    vi.mocked(api.llm.getConfig).mockResolvedValue(makeConfig({ available_models: undefined }))
    // The 023 synthetic verdict: status=error with the verdict carried
    // on the last details[] entry.
    vi.mocked(api.llm.discoverProvider).mockResolvedValue({
      status: 'error',
      details: [{
        step: 'list_models',
        method: 'GET',
        url: 'https://example/v1/models',
        headers_redacted: {},
        request_body: null,
        status_code: 200,
        response_body: '{"data":[]}',
        duration_ms: 12,
        error: 'list_models returned 0 models — server reachable but no models published',
      }],
      models: [],
    })
    renderTab()
    await expandP1()
    fireEvent.click(await screen.findByRole('button', { name: /Discover Models/i }))
    const err = await screen.findByText(/list_models returned 0 models/i)
    expect(err).toBeInTheDocument()
    // No PUT — nothing was persisted.
    expect(api.llm.updateProvider).not.toHaveBeenCalled()
  })

  it('editing a field clears the probe-OK pill but keeps Save enabled (prompts-033 decision A: Save is the orchestrator)', async () => {
    vi.mocked(api.llm.getConfig).mockResolvedValue(makeConfig())
    vi.mocked(api.llm.testProviderDraft).mockResolvedValue({
      status: 'ok', details: [], models: null, sample: 'pong',
    })
    renderTab()
    await screen.findByRole('heading', { name: 'p1' })
    await expandP1()

    // Probe shows the OK pill, but Save is no longer GATED on it.
    fireEvent.click(screen.getByRole('button', { name: /Test connection/i }))
    await screen.findByTestId('card-probe-ok')
    const savesAfterProbe = screen.getAllByRole('button', { name: /^Save$/ })
    expect(savesAfterProbe[1]).not.toBeDisabled()

    // Editing the model clears the OK pill but Save STAYS enabled — Save
    // persists directly without a blocking test (prompts-036).
    const select = screen.getByLabelText(/Default model to use/i) as HTMLSelectElement
    fireEvent.change(select, { target: { value: 'gpt-4o' } })
    expect(screen.queryByTestId('card-probe-ok')).not.toBeInTheDocument()
    const savesAfterEdit = screen.getAllByRole('button', { name: /^Save$/ })
    expect(savesAfterEdit[1]).not.toBeDisabled()
  })

  it('Save with UNCHANGED base_url persists WITHOUT a connection test or discovery (prompts-036)', async () => {
    vi.mocked(api.llm.getConfig).mockResolvedValue(makeConfig())
    vi.mocked(api.llm.updateProvider).mockResolvedValue({
      name: 'p1', kind: 'openai', model: 'gpt-4o-mini',
      has_api_key: true, skip_tls_verify: false,
    })
    renderTab()
    await screen.findByRole('heading', { name: 'p1' })
    await expandP1()

    // Save directly — no probe required and no test/discovery run.
    const saves = screen.getAllByRole('button', { name: /^Save$/ })
    fireEvent.click(saves[1])
    await waitFor(() => expect(api.llm.updateProvider).toHaveBeenCalledTimes(1))
    // prompts-036: Save no longer runs a blocking connection test, and
    // discovery only runs on a base_url change (unchanged here).
    expect(api.llm.testProviderDraft).not.toHaveBeenCalled()
    expect(api.llm.discoverDraft).not.toHaveBeenCalled()
    await screen.findByText(/^Saved\.$/)
  })

  it('Save after a base_url change runs discovery THEN persists — no connection test (prompts-036)', async () => {
    vi.mocked(api.llm.getConfig).mockResolvedValue(makeConfig())
    vi.mocked(api.llm.discoverDraft).mockResolvedValue({
      status: 'ok', details: [], models: ['gpt-4o-mini', 'gpt-4o', 'o1'],
    })
    vi.mocked(api.llm.updateProvider).mockResolvedValue({
      name: 'p1', kind: 'openai', model: 'gpt-4o-mini',
      has_api_key: true, skip_tls_verify: false,
    })
    renderTab()
    await screen.findByRole('heading', { name: 'p1' })
    await expandP1()

    // Change the base URL, then Save.
    const base = screen.getByDisplayValue('https://api.openai.com/v1') as HTMLInputElement
    fireEvent.change(base, { target: { value: 'https://proxy.internal/v1' } })
    fireEvent.click(screen.getAllByRole('button', { name: /^Save$/ })[1])

    await waitFor(() => expect(api.llm.updateProvider).toHaveBeenCalledTimes(1))
    expect(api.llm.discoverDraft).toHaveBeenCalledTimes(1)
    // prompts-036: no blocking connection test runs during Save.
    expect(api.llm.testProviderDraft).not.toHaveBeenCalled()
    // Persisted payload carries the freshly-discovered model list.
    const [, payload] = vi.mocked(api.llm.updateProvider).mock.calls[0]
    expect(payload.available_models).toEqual(['gpt-4o-mini', 'gpt-4o', 'o1'])
  })

  it('Save persists even when the model would fail a connection test — bad models surface at proposal time (prompts-036)', async () => {
    vi.mocked(api.llm.getConfig).mockResolvedValue(makeConfig())
    // A probe WOULD fail, but Save must no longer run or be blocked by it.
    vi.mocked(api.llm.testProviderDraft).mockResolvedValue({
      status: 'error',
      details: [{
        step: 'complete', method: 'POST', url: 'https://api.openai.com/v1/chat',
        headers_redacted: {}, request_body: null, status_code: 401,
        response_body: '{"error":"bad key"}', duration_ms: 5,
        error: 'authentication failed (401)',
      }],
      models: null, sample: null,
    })
    vi.mocked(api.llm.updateProvider).mockResolvedValue({
      name: 'p1', kind: 'openai', model: 'gpt-4o-mini',
      has_api_key: true, skip_tls_verify: false,
    })
    renderTab()
    await screen.findByRole('heading', { name: 'p1' })
    await expandP1()

    fireEvent.click(screen.getAllByRole('button', { name: /^Save$/ })[1])
    await waitFor(() => expect(api.llm.updateProvider).toHaveBeenCalledTimes(1))
    // No blocking warning, and the test endpoint was never consulted by Save.
    expect(screen.queryByTestId('card-save-warning-p1')).not.toBeInTheDocument()
    expect(api.llm.testProviderDraft).not.toHaveBeenCalled()
    await screen.findByText(/^Saved\.$/)
  })

  it('Save aborts WITHOUT persisting when discovery fails after a base_url change (prompts-036)', async () => {
    vi.mocked(api.llm.getConfig).mockResolvedValue(makeConfig())
    vi.mocked(api.llm.discoverDraft).mockResolvedValue({
      status: 'error',
      details: [{
        step: 'list_models', method: 'GET', url: 'https://proxy/v1/models',
        headers_redacted: {}, request_body: null, status_code: 502,
        response_body: 'bad gateway', duration_ms: 9,
        error: 'upstream unreachable (502)',
      }],
      models: [],
    })
    renderTab()
    await screen.findByRole('heading', { name: 'p1' })
    await expandP1()

    const base = screen.getByDisplayValue('https://api.openai.com/v1') as HTMLInputElement
    fireEvent.change(base, { target: { value: 'https://proxy.internal/v1' } })
    fireEvent.click(screen.getAllByRole('button', { name: /^Save$/ })[1])

    const warn = await screen.findByTestId('card-save-warning-p1')
    expect(warn).toHaveTextContent(/Discovery failed/i)
    expect(warn).toHaveTextContent(/not saved/i)
    // Discovery failed → neither test nor persist ran.
    expect(api.llm.testProviderDraft).not.toHaveBeenCalled()
    expect(api.llm.updateProvider).not.toHaveBeenCalled()
  })

  it('Anthropic kind keeps the free-text model input (no dropdown) without available_models', async () => {
    vi.mocked(api.llm.getConfig).mockResolvedValue(
      makeConfig({
        name: 'p1',
        kind: 'anthropic',
        base_url: 'https://api.anthropic.com',
        model: 'claude-3-5-sonnet-20241022',
        available_models: undefined,
      }),
    )
    renderTab()
    await expandP1()
    const input = await screen.findByLabelText(/Default model to use/i) as HTMLInputElement
    expect(input.tagName).toBe('INPUT')
    expect(input.value).toBe('claude-3-5-sonnet-20241022')
  })

  it('shows the "No models discovered" hint when available_models is empty for a non-Anthropic kind', async () => {
    vi.mocked(api.llm.getConfig).mockResolvedValue(makeConfig({ available_models: undefined }))
    renderTab()
    await expandP1()
    expect(
      await screen.findByText(/No models discovered\. Click "Discover Models" to populate this list\./i),
    ).toBeInTheDocument()
    // The "Default model to use" dropdown is NOT rendered in this
    // branch (the top-level "Default provider" select and the Kind
    // select still exist — query by the specific label).
    expect(screen.queryByLabelText(/Default model to use/i)).not.toBeInTheDocument()
  })

  it('preserves an on-disk model that is missing from available_models with a "(not in discovered list)" suffix', async () => {
    vi.mocked(api.llm.getConfig).mockResolvedValue(
      makeConfig({
        model: 'legacy-model-from-yaml',
        available_models: ['gpt-4o-mini', 'gpt-4o'],
      }),
    )
    renderTab()
    await expandP1()
    const select = await screen.findByLabelText(/Default model to use/i) as HTMLSelectElement
    expect(select.value).toBe('legacy-model-from-yaml')
    expect(
      within(select).getByRole('option', { name: /legacy-model-from-yaml \(not in discovered list\)/i }),
    ).toBeInTheDocument()
  })
})

describe('ProviderCard (prompts-031: delete confirm + dropdown-after-save)', () => {
  it('Delete shows an in-card confirm (no window.confirm) and calls deleteProvider on Confirm', async () => {
    vi.mocked(api.llm.getConfig).mockResolvedValue(makeConfig())
    vi.mocked(api.llm.deleteProvider).mockResolvedValue(undefined)
    // Guard: window.confirm must NOT be used by the new flow.
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    renderTab()
    await screen.findByRole('heading', { name: 'p1' })

    // Click the card's Delete button — the destructive action is gated
    // behind an in-card confirm, not a browser dialog.
    fireEvent.click(screen.getByRole('button', { name: /Delete p1/i }))
    expect(confirmSpy).not.toHaveBeenCalled()
    expect(api.llm.deleteProvider).not.toHaveBeenCalled()

    const panel = await screen.findByTestId('delete-confirm-p1')
    // Last-provider + enabled => the LLM-disable warning is shown.
    expect(within(panel).getByText(/will also disable LLM/i)).toBeInTheDocument()

    fireEvent.click(screen.getByTestId('delete-confirm-yes-p1'))
    await waitFor(() => expect(api.llm.deleteProvider).toHaveBeenCalledWith('p1'))
    confirmSpy.mockRestore()
  })

  it('Delete confirm can be cancelled without calling deleteProvider', async () => {
    vi.mocked(api.llm.getConfig).mockResolvedValue(makeConfig())
    renderTab()
    await screen.findByRole('heading', { name: 'p1' })

    fireEvent.click(screen.getByRole('button', { name: /Delete p1/i }))
    await screen.findByTestId('delete-confirm-p1')
    fireEvent.click(screen.getByRole('button', { name: /^Cancel$/ }))
    expect(screen.queryByTestId('delete-confirm-p1')).not.toBeInTheDocument()
    expect(api.llm.deleteProvider).not.toHaveBeenCalled()
  })

  it('keeps the discovered-models dropdown after a successful Save', async () => {
    // The card hydrates from GET /config; after Save the parent refetches
    // /config, which must still carry available_models so the <select>
    // does not collapse to the free-text fallback (prompts-031 change 1).
    vi.mocked(api.llm.getConfig).mockResolvedValue(makeConfig())
    vi.mocked(api.llm.updateProvider).mockResolvedValue({
      name: 'p1', kind: 'openai', model: 'gpt-4o-mini',
      has_api_key: true, skip_tls_verify: false,
    })
    renderTab()
    await screen.findByRole('heading', { name: 'p1' })
    await expandP1()

    // Save directly (prompts-036: no probe required to unlock Save).
    const saves = screen.getAllByRole('button', { name: /^Save$/ })
    fireEvent.click(saves[1])
    await waitFor(() => expect(api.llm.updateProvider).toHaveBeenCalledTimes(1))

    // The dropdown remains a <select> populated from available_models.
    const select = await screen.findByLabelText(/Default model to use/i) as HTMLSelectElement
    expect(select.tagName).toBe('SELECT')
    expect(within(select).getByRole('option', { name: 'gpt-4o' })).toBeInTheDocument()
  })
})
