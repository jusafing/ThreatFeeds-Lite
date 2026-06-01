/**
 * Tests for the LLM Providers configuration tab (prompts-021D-2,
 * updated in 022 step 6, rewritten in 027 step 5 for the Discover-then-
 * Probe staged card).
 *
 * Covers acceptance criteria:
 *   1. Existing providers load with redacted api_key.
 *   2. "Test connection" success renders the Model OK pill (the new
 *      ProviderCard probes via testProviderDraft against the persisted
 *      record; the backend merge-stored-key branch — 027 step 1 —
 *      injects the on-disk key when api_key === "***").
 *   3. "Test connection" error renders the inline failure message.
 *   4. TLS-skip checkbox surfaces the warning text.
 *   5. enabled=true with no default_provider surfaces the gating warning.
 *   6. Top-level Save sends ONLY {enabled, default_provider} —
 *      providers list is no longer carried in PUT /api/llm/config
 *      (022 step 4).
 *   7. The api_key <input> never reflects the redacted "***" in its value.
 *   8. Per-card Save is gated on a probe-since-last-edit verdict
 *      (027 step 5) — Save remains disabled until "Test connection"
 *      succeeds with the currently-selected model.
 *   9. After unlocking via a green probe, per-card Save PUTs with
 *      api_key === "***" when no key was typed, or verbatim when
 *      the operator entered one.
 *
 * NOTE: ProviderCard-specific behaviour (Discover Models button,
 * dropdown population from available_models, probe-since-edit Save
 * invalidation) is exercised in detail in providerCard.test.tsx
 * (027 step 6). This file focuses on the legacy acceptance criteria
 * plus the gating handshake.
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
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

const redactedConfig: LLMConfig = {
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
      // 027 step 5: include a discovered list so the "Default model to
      // use" dropdown renders without an extra Discover round trip.
      available_models: ['gpt-4o-mini', 'gpt-4o'],
    },
  ],
}

const _summary: LLMProviderSummary[] = [
  { name: 'p1', kind: 'openai', model: 'gpt-4o-mini', has_api_key: true, skip_tls_verify: false },
]

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

describe('LLMProvidersTab', () => {
  it('loads existing providers and renders the redacted key indicator', async () => {
    vi.mocked(api.llm.getConfig).mockResolvedValue(redactedConfig)
    renderTab()
    expect(await screen.findByRole('heading', { name: 'p1' })).toBeInTheDocument()
    await expandP1()
    expect(screen.getByText(/stored — leave blank to keep/i)).toBeInTheDocument()
    // The password input must NOT show the redacted sentinel as its value.
    const pwd = screen.getByLabelText(/API key/i) as HTMLInputElement
    expect(pwd.value).toBe('')
    expect(pwd.type).toBe('password')
  })

  it('Test connection success renders the Model OK pill (027 step 5: probes via testProviderDraft)', async () => {
    vi.mocked(api.llm.getConfig).mockResolvedValue(redactedConfig)
    vi.mocked(api.llm.testProviderDraft).mockResolvedValue({
      status: 'ok',
      details: [],
      models: ['gpt-4o-mini', 'gpt-4o'],
      sample: 'pong',
    })
    renderTab()
    await expandP1()
    const btn = await screen.findByRole('button', { name: /Test connection/i })
    fireEvent.click(btn)
    expect(await screen.findByTestId('card-probe-ok')).toBeInTheDocument()
    // The ProviderCard probe path must call the draft endpoint with
    // the on-disk name so the backend can merge the stored key.
    const arg = vi.mocked(api.llm.testProviderDraft).mock.calls[0][0]
    expect(arg.name).toBe('p1')
    expect(arg.api_key).toBe('***')
    // Legacy testProvider path is no longer used for persisted cards.
    expect(api.llm.testProvider).not.toHaveBeenCalled()
  })

  it('Test connection renders the inline error detail on failure', async () => {
    vi.mocked(api.llm.getConfig).mockResolvedValue(redactedConfig)
    vi.mocked(api.llm.testProviderDraft).mockRejectedValue(
      new Error('502 Bad Gateway: transport: timeout'),
    )
    renderTab()
    await expandP1()
    fireEvent.click(await screen.findByRole('button', { name: /Test connection/i }))
    const err = await screen.findByTestId('card-probe-error')
    expect(err).toHaveTextContent(/transport: timeout/i)
  })

  it('enabled=true with empty default_provider surfaces the gating warning', async () => {
    vi.mocked(api.llm.getConfig).mockResolvedValue({
      ...redactedConfig,
      default_provider: null,
    })
    renderTab()
    expect(
      await screen.findByText(/default provider is required when LLM is enabled/i),
    ).toBeInTheDocument()
  })

  it('TLS-skip checkbox renders the warning text when checked', async () => {
    vi.mocked(api.llm.getConfig).mockResolvedValue({
      ...redactedConfig,
      providers: [{ ...redactedConfig.providers[0], skip_tls_verify: true }],
    })
    renderTab()
    await expandP1()
    expect(
      await screen.findByText(/TLS certificate verification is disabled/i),
    ).toBeInTheDocument()
  })

  it('Top-level Save sends only {enabled, default_provider} (no providers list)', async () => {
    vi.mocked(api.llm.getConfig).mockResolvedValue(redactedConfig)
    vi.mocked(api.llm.setConfig).mockResolvedValue(redactedConfig)
    renderTab()
    const saves = await screen.findAllByRole('button', { name: /^Save$/ })
    fireEvent.click(saves[0])
    await waitFor(() => expect(api.llm.setConfig).toHaveBeenCalled())
    const payload = vi.mocked(api.llm.setConfig).mock.calls[0][0]
    expect(payload).toEqual({ enabled: true, default_provider: 'p1' })
    expect('providers' in payload).toBe(false)
  })

  it('per-card Save (prompts-036) is enabled with a model selected and PUTs api_key="***" when no key typed', async () => {
    // prompts-033 decision A: Save is the orchestrator and no longer gated
    // on a prior green probe — it is enabled whenever a model is selected.
    // prompts-036: Save no longer runs a blocking connection test either.
    // When the operator did not type a new key, the PUT carries "***" so the
    // backend preserves the stored value.
    vi.mocked(api.llm.getConfig).mockResolvedValue(redactedConfig)
    vi.mocked(api.llm.updateProvider).mockResolvedValue({
      name: 'p1',
      kind: 'openai',
      model: 'gpt-4o-mini',
      has_api_key: true,
      skip_tls_verify: false,
    })
    renderTab()
    await screen.findByRole('heading', { name: 'p1' })
    await expandP1()

    // Per-card Save (index 1) is ENABLED without any prior probe and persists
    // directly — no connection test runs (prompts-036).
    const saves = screen.getAllByRole('button', { name: /^Save$/ })
    expect(saves[1]).not.toBeDisabled()
    fireEvent.click(saves[1])

    // Save persisted with the stored-key sentinel and never ran a test.
    await waitFor(() => expect(api.llm.updateProvider).toHaveBeenCalledTimes(1))
    expect(api.llm.testProviderDraft).not.toHaveBeenCalled()
    const [name, payload] = vi.mocked(api.llm.updateProvider).mock.calls[0]
    expect(name).toBe('p1')
    expect(payload.api_key).toBe('***')
    expect('_api_key_draft' in payload).toBe(false)
  })

  it('per-card Save sends the typed api_key verbatim after a green probe', async () => {
    // 027 step 5: typing a new key invalidates any prior probe verdict,
    // so the operator must probe AFTER typing. The probe then runs with
    // the new key (no merge-stored-key needed), and Save PUTs it verbatim.
    vi.mocked(api.llm.getConfig).mockResolvedValue(redactedConfig)
    vi.mocked(api.llm.testProviderDraft).mockResolvedValue({
      status: 'ok',
      details: [],
      models: null,
      sample: 'pong',
    })
    vi.mocked(api.llm.updateProvider).mockResolvedValue({
      name: 'p1',
      kind: 'openai',
      model: 'gpt-4o-mini',
      has_api_key: true,
      skip_tls_verify: false,
    })
    renderTab()
    await screen.findByRole('heading', { name: 'p1' })
    await expandP1()
    const pwd = screen.getByLabelText(/API key/i) as HTMLInputElement
    fireEvent.change(pwd, { target: { value: 'sk-new' } })
    // Editing invalidated any prior probe — re-probe with the new key.
    fireEvent.click(screen.getByRole('button', { name: /Test connection/i }))
    await waitFor(() => expect(api.llm.testProviderDraft).toHaveBeenCalledTimes(1))
    expect(vi.mocked(api.llm.testProviderDraft).mock.calls[0][0].api_key).toBe('sk-new')
    await screen.findByTestId('card-probe-ok')

    const saves = screen.getAllByRole('button', { name: /^Save$/ })
    fireEvent.click(saves[1])
    await waitFor(() => expect(api.llm.updateProvider).toHaveBeenCalledTimes(1))
    const [, payload] = vi.mocked(api.llm.updateProvider).mock.calls[0]
    expect(payload.api_key).toBe('sk-new')
  })

  it('per-card Delete shows an in-card confirm then calls deleteProvider (prompts-031)', async () => {
    vi.mocked(api.llm.getConfig).mockResolvedValue(redactedConfig)
    vi.mocked(api.llm.deleteProvider).mockResolvedValue(undefined as unknown as void)
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    renderTab()
    fireEvent.click(await screen.findByRole('button', { name: /Delete p1/i }))
    // No browser dialog — the confirm is now in-card.
    expect(confirmSpy).not.toHaveBeenCalled()
    expect(api.llm.deleteProvider).not.toHaveBeenCalled()
    fireEvent.click(await screen.findByTestId('delete-confirm-yes-p1'))
    await waitFor(() => expect(api.llm.deleteProvider).toHaveBeenCalledWith('p1'))
    confirmSpy.mockRestore()
  })

  it('per-card Delete is a no-op when the operator cancels the in-card confirm (prompts-031)', async () => {
    vi.mocked(api.llm.getConfig).mockResolvedValue(redactedConfig)
    renderTab()
    fireEvent.click(await screen.findByRole('button', { name: /Delete p1/i }))
    fireEvent.click(await screen.findByRole('button', { name: /^Cancel$/ }))
    expect(api.llm.deleteProvider).not.toHaveBeenCalled()
  })
})
