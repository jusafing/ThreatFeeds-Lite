/**
 * Tests for AddProviderWizard (prompts-027 rewrite; prompts-028 amend).
 *
 * Covers the strict 4-stage operator-driven flow:
 *
 *   Stage 1 — Identify
 *     1. Name regex (^[A-Za-z0-9_-]{1,40}$) inline + disables Connect.
 *     2. Duplicate names against existingNames disable Connect.
 *     3. "Connect to provider" calls api.llm.discoverDraft with the
 *        live draft payload (NOT testProviderDraft).
 *     4. A discover failure shows inline error; stage 2 stays hidden.
 *
 *   Stage 2 — Pick model
 *     5. A successful discover reveals the model picker.
 *     6. Anthropic gets a free-text input instead of a dropdown.
 *     7. The "Test Model" button is disabled until a model is chosen.
 *
 *   Stage 3 — Probe
 *     8. "Test Model" calls api.llm.testProviderDraft with the model.
 *     9. A probe failure shows inline error; stage 4 stays hidden.
 *
 *   Stage 4 — Commit
 *    10. "Add Provider" appears once a model is selected (probe
 *        optional; prompts-055) — and not before discover.
 *    11. Editing a stage-1 field after discover hides stage 4.
 *    12. "Add Provider" calls api.llm.addProvider with the payload
 *        and invokes onAdded + onClose.
 *    13. addProvider error renders inline without closing the wizard.
 *
 *   Misc
 *    14. View test details opens TestDetailsModal after Connect.
 *    15. base_url hint visible only for openai_compatible kind.
 *
 *   prompts-028 — discover dropdown decoupled from aggregate status
 *    16. A 200 with a non-empty model list but status==='error'
 *        STILL reveals the dropdown (the regression fix).
 *    17. A 200 empty catalog (status==='error', 0 models) shows an
 *        amber note + a free-text Model input, no red error, and the
 *        operator can probe + reach stage 4 with a typed model.
 *    18. A genuine transport failure (401, no models, non-2xx) still
 *        shows the red error and keeps stage 2 hidden (unchanged).
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'

import type { LLMDiscoverResult, LLMTestRunResult } from '../api/client'

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...actual,
    api: {
      llm: {
        discoverDraft: vi.fn(),
        testProviderDraft: vi.fn(),
        addProvider: vi.fn(),
      },
    },
  }
})

import AddProviderWizard from '../components/AddProviderWizard'
import { api } from '../api/client'

// ── Fixtures ────────────────────────────────────────────────────────────────

const discoverOk: LLMDiscoverResult = {
  status: 'ok',
  details: [
    {
      step: 'list_models',
      method: 'GET',
      url: 'https://api.openai.com/v1/models',
      headers_redacted: { Authorization: '***REDACTED***' },
      request_body: null,
      status_code: 200,
      response_body: '{"data":[{"id":"gpt-4o"}]}',
      duration_ms: 42,
      error: null,
    },
  ],
  models: ['gpt-4o', 'gpt-4o-mini'],
}

const discoverFail: LLMDiscoverResult = {
  status: 'error',
  details: [
    {
      step: 'list_models',
      method: 'GET',
      url: 'https://api.openai.com/v1/models',
      headers_redacted: {},
      request_body: null,
      status_code: 401,
      response_body: '{"error":"unauthorized"}',
      duration_ms: 12,
      error: 'LLMProviderError: provider returned HTTP 401',
    },
  ],
  models: null,
}

const discoverAnthropicOk: LLMDiscoverResult = {
  status: 'ok',
  details: [
    {
      step: 'list_models',
      method: null,
      url: null,
      headers_redacted: {},
      request_body: null,
      status_code: null,
      response_body: '',
      duration_ms: 0,
      error: 'no public list endpoint (anthropic)',
    },
  ],
  models: null,
}

// prompts-028 regression fixture: the upstream returned HTTP 200 with a
// usable model list, but the backend aggregate status is 'error' (e.g. a
// downstream step verdict). The dropdown MUST still surface — this is the
// exact regression the operator hit ("HTTP 200 but Discovery failed").
const discover200ModelsButStatusError: LLMDiscoverResult = {
  status: 'error',
  details: [
    {
      step: 'list_models',
      method: 'GET',
      url: 'http://localhost:11434/v1/models',
      headers_redacted: {},
      request_body: null,
      status_code: 200,
      response_body: '{"data":[{"id":"llama3"},{"id":"mistral"}]}',
      duration_ms: 18,
      error: null,
    },
  ],
  models: ['llama3', 'mistral'],
}

// prompts-028 empty-catalog fixture: server reachable (HTTP 200) but
// published 0 models. Backend reports status='error' with the benign
// "0 models published" verdict; the wizard shows an amber note + a
// free-text model input rather than a red "Discover failed".
const discover200EmptyCatalog: LLMDiscoverResult = {
  status: 'error',
  details: [
    {
      step: 'list_models',
      method: 'GET',
      url: 'http://localhost:8000/v1/models',
      headers_redacted: {},
      request_body: null,
      status_code: 200,
      response_body: '{"data":[]}',
      duration_ms: 9,
      error:
        'list_models returned 0 models — server reachable but no models published',
    },
  ],
  models: [],
}

const probeOk: LLMTestRunResult = {
  status: 'ok',
  details: [
    {
      step: 'list_models',
      method: 'GET', url: 'https://x/models',
      headers_redacted: {}, request_body: null,
      status_code: 200, response_body: '{"data":[{"id":"gpt-4o"}]}',
      duration_ms: 10, error: null,
    },
    {
      step: 'complete',
      method: 'POST', url: 'https://x/chat/completions',
      headers_redacted: {}, request_body: '{"model":"gpt-4o"}',
      status_code: 200, response_body: '{"choices":[{"message":{"content":"pong"}}]}',
      duration_ms: 30, error: null,
    },
  ],
  models: ['gpt-4o', 'gpt-4o-mini'],
  sample: 'pong',
}

const probeFail: LLMTestRunResult = {
  status: 'error',
  details: [
    {
      step: 'complete',
      method: 'POST', url: 'https://x/chat/completions',
      headers_redacted: {}, request_body: null,
      status_code: 502, response_body: 'bad gateway',
      duration_ms: 5,
      error: 'LLMProviderError: provider returned HTTP 502',
    },
  ],
  models: null,
  sample: null,
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function fillRequiredFields() {
  fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'my-openai' } })
  fireEvent.change(screen.getByLabelText(/API key/i), { target: { value: 'sk-test' } })
}

async function connectToProvider() {
  fillRequiredFields()
  fireEvent.click(screen.getByRole('button', { name: /Connect to provider/i }))
}

beforeEach(() => {
  vi.mocked(api.llm.discoverDraft).mockReset()
  vi.mocked(api.llm.testProviderDraft).mockReset()
  vi.mocked(api.llm.addProvider).mockReset()
})

// ── Tests ───────────────────────────────────────────────────────────────────

describe('AddProviderWizard (027)', () => {
  it('disables "Connect to provider" until name regex passes', () => {
    render(<AddProviderWizard existingNames={[]} onClose={() => {}} onAdded={() => {}} />)
    const btn = screen.getByRole('button', { name: /Connect to provider/i }) as HTMLButtonElement
    expect(btn.disabled).toBe(true)
    fireEvent.change(screen.getByLabelText(/API key/i), { target: { value: 'sk-test' } })
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'bad name!' } })
    expect(btn.disabled).toBe(true)
    expect(screen.getByText(/Allowed: A-Z/i)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'my-openai' } })
    expect(btn.disabled).toBe(false)
  })

  it('disables "Connect to provider" when name collides with existing', () => {
    render(<AddProviderWizard existingNames={['taken']} onClose={() => {}} onAdded={() => {}} />)
    fireEvent.change(screen.getByLabelText(/API key/i), { target: { value: 'sk-test' } })
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'taken' } })
    const btn = screen.getByRole('button', { name: /Connect to provider/i }) as HTMLButtonElement
    expect(btn.disabled).toBe(true)
    expect(screen.getByText(/already exists/i)).toBeInTheDocument()
  })

  it('Connect calls discoverDraft (not testProviderDraft) with draft payload', async () => {
    vi.mocked(api.llm.discoverDraft).mockResolvedValue(discoverOk)
    render(<AddProviderWizard existingNames={[]} onClose={() => {}} onAdded={() => {}} />)
    await connectToProvider()
    await waitFor(() =>
      expect(api.llm.discoverDraft).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'my-openai',
          kind: 'openai',
          api_key: 'sk-test',
          base_url: 'https://api.openai.com/v1',
        }),
      ),
    )
    expect(api.llm.testProviderDraft).not.toHaveBeenCalled()
  })

  it('discover failure shows inline error and keeps stage 2 hidden', async () => {
    vi.mocked(api.llm.discoverDraft).mockResolvedValue(discoverFail)
    render(<AddProviderWizard existingNames={[]} onClose={() => {}} onAdded={() => {}} />)
    await connectToProvider()
    await waitFor(() =>
      expect(screen.getByTestId('discover-verdict-error')).toBeInTheDocument(),
    )
    expect(screen.getByText(/HTTP 401/i)).toBeInTheDocument()
    expect(screen.queryByTestId('stage-2')).not.toBeInTheDocument()
    expect(screen.queryByTestId('stage-4')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Test Model/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Add Provider/i })).not.toBeInTheDocument()
    // "Connect to provider" stays clickable so the operator can retry.
    expect(
      (screen.getByRole('button', { name: /Connect to provider/i }) as HTMLButtonElement)
        .disabled,
    ).toBe(false)
  })

  it('successful discover reveals the model picker + "Test Model" button', async () => {
    vi.mocked(api.llm.discoverDraft).mockResolvedValue(discoverOk)
    render(<AddProviderWizard existingNames={[]} onClose={() => {}} onAdded={() => {}} />)
    await connectToProvider()
    await waitFor(() => expect(screen.getByTestId('stage-2')).toBeInTheDocument())
    expect(screen.getByLabelText('Model')).toBeInTheDocument()
    expect(screen.getByText(/Models discovered/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Test Model/i })).toBeInTheDocument()
    // First model auto-picked.
    expect((screen.getByLabelText('Model') as HTMLSelectElement).value).toBe('gpt-4o')
    // prompts-055: stage 4 (Add Provider) is revealed by the auto-picked
    // model — no probe required.
    expect(screen.getByTestId('stage-4')).toBeInTheDocument()
  })

  it('Anthropic renders a free-text Model input instead of a dropdown', async () => {
    vi.mocked(api.llm.discoverDraft).mockResolvedValue(discoverAnthropicOk)
    render(<AddProviderWizard existingNames={[]} onClose={() => {}} onAdded={() => {}} />)
    fireEvent.change(screen.getByLabelText('Kind'), { target: { value: 'anthropic' } })
    fillRequiredFields()
    fireEvent.click(screen.getByRole('button', { name: /Connect to provider/i }))
    await waitFor(() => expect(screen.getByTestId('stage-2')).toBeInTheDocument())
    const modelInput = screen.getByLabelText('Model') as HTMLInputElement
    expect(modelInput.tagName).toBe('INPUT')
    expect(modelInput.placeholder).toMatch(/claude-3-5/)
  })

  it('"Test Model" is disabled until a model is selected', async () => {
    // Use a discover result with NO auto-pick (empty array won't happen
    // in practice — discover-ok always has >=1 model — so simulate by
    // clearing the model after auto-pick).
    vi.mocked(api.llm.discoverDraft).mockResolvedValue(discoverOk)
    render(<AddProviderWizard existingNames={[]} onClose={() => {}} onAdded={() => {}} />)
    await connectToProvider()
    await waitFor(() => expect(screen.getByTestId('stage-2')).toBeInTheDocument())
    const btn = screen.getByRole('button', { name: /Test Model/i }) as HTMLButtonElement
    // First model auto-picked → btn enabled.
    expect(btn.disabled).toBe(false)
    // Clear selection → btn disabled.
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: '' } })
    expect(btn.disabled).toBe(true)
  })

  it('"Test Model" calls testProviderDraft and on success reveals stage 4', async () => {
    vi.mocked(api.llm.discoverDraft).mockResolvedValue(discoverOk)
    vi.mocked(api.llm.testProviderDraft).mockResolvedValue(probeOk)
    render(<AddProviderWizard existingNames={[]} onClose={() => {}} onAdded={() => {}} />)
    await connectToProvider()
    await waitFor(() => expect(screen.getByTestId('stage-2')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /Test Model/i }))
    await waitFor(() =>
      expect(api.llm.testProviderDraft).toHaveBeenCalledWith(
        expect.objectContaining({ model: 'gpt-4o', name: 'my-openai' }),
      ),
    )
    await waitFor(() => expect(screen.getByTestId('stage-4')).toBeInTheDocument())
    expect(screen.getByTestId('probe-verdict-ok')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Add Provider/i })).toBeInTheDocument()
  })

  it('probe failure shows inline error but keeps stage 4 visible (probe optional)', async () => {
    vi.mocked(api.llm.discoverDraft).mockResolvedValue(discoverOk)
    vi.mocked(api.llm.testProviderDraft).mockResolvedValue(probeFail)
    render(<AddProviderWizard existingNames={[]} onClose={() => {}} onAdded={() => {}} />)
    await connectToProvider()
    await waitFor(() => expect(screen.getByTestId('stage-2')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /Test Model/i }))
    await waitFor(() =>
      expect(screen.getByTestId('probe-verdict-error')).toBeInTheDocument(),
    )
    expect(screen.getByText(/HTTP 502/i)).toBeInTheDocument()
    // prompts-055: a failed probe is informational only — Save stays
    // available because a model is selected.
    expect(screen.getByTestId('stage-4')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Add Provider/i })).toBeInTheDocument()
  })

  it('"Add Provider" is NOT in the DOM before discover but appears after (no probe)', async () => {
    vi.mocked(api.llm.discoverDraft).mockResolvedValue(discoverOk)
    render(<AddProviderWizard existingNames={[]} onClose={() => {}} onAdded={() => {}} />)
    // Before connecting.
    expect(screen.queryByRole('button', { name: /Add Provider/i })).not.toBeInTheDocument()
    await connectToProvider()
    await waitFor(() => expect(screen.getByTestId('stage-2')).toBeInTheDocument())
    // prompts-055: after discover ok + auto-pick, Save is visible without
    // any probe call.
    expect(screen.getByRole('button', { name: /Add Provider/i })).toBeInTheDocument()
    expect(api.llm.testProviderDraft).not.toHaveBeenCalled()
  })

  it('can add a provider without ever clicking Test Model (prompts-055)', async () => {
    vi.mocked(api.llm.discoverDraft).mockResolvedValue(discoverOk)
    vi.mocked(api.llm.addProvider).mockResolvedValue({
      name: 'my-openai', kind: 'openai',
      model: 'gpt-4o', has_api_key: true, skip_tls_verify: false,
    })
    const onAdded = vi.fn()
    const onClose = vi.fn()
    render(<AddProviderWizard existingNames={[]} onClose={onClose} onAdded={onAdded} />)
    await connectToProvider()
    await waitFor(() => expect(screen.getByTestId('stage-4')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /Add Provider/i }))
    await waitFor(() => expect(api.llm.addProvider).toHaveBeenCalledTimes(1))
    expect(api.llm.testProviderDraft).not.toHaveBeenCalled()
    expect(api.llm.addProvider).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'my-openai', model: 'gpt-4o' }),
    )
    expect(onAdded).toHaveBeenCalledTimes(1)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('editing a stage-1 field after a green probe hides stage 4', async () => {
    vi.mocked(api.llm.discoverDraft).mockResolvedValue(discoverOk)
    vi.mocked(api.llm.testProviderDraft).mockResolvedValue(probeOk)
    render(<AddProviderWizard existingNames={[]} onClose={() => {}} onAdded={() => {}} />)
    await connectToProvider()
    await waitFor(() => expect(screen.getByTestId('stage-2')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /Test Model/i }))
    await waitFor(() => expect(screen.getByTestId('stage-4')).toBeInTheDocument())
    // Edit the API key — full stage-1 invalidation: stages 2-4 all disappear.
    fireEvent.change(screen.getByLabelText(/API key/i), { target: { value: 'sk-new' } })
    expect(screen.queryByTestId('stage-4')).not.toBeInTheDocument()
    expect(screen.queryByTestId('stage-2')).not.toBeInTheDocument()
  })

  it('"Add Provider" calls addProvider then onAdded + onClose', async () => {
    vi.mocked(api.llm.discoverDraft).mockResolvedValue(discoverOk)
    vi.mocked(api.llm.testProviderDraft).mockResolvedValue(probeOk)
    vi.mocked(api.llm.addProvider).mockResolvedValue({
      name: 'my-openai', kind: 'openai',
      model: 'gpt-4o', has_api_key: true, skip_tls_verify: false,
    })
    const onAdded = vi.fn()
    const onClose = vi.fn()
    render(<AddProviderWizard existingNames={[]} onClose={onClose} onAdded={onAdded} />)
    await connectToProvider()
    await waitFor(() => expect(screen.getByTestId('stage-2')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /Test Model/i }))
    await waitFor(() => expect(screen.getByTestId('stage-4')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /Add Provider/i }))
    await waitFor(() => expect(api.llm.addProvider).toHaveBeenCalledTimes(1))
    expect(api.llm.addProvider).toHaveBeenCalledWith(
      expect.objectContaining({
        name: 'my-openai',
        kind: 'openai',
        api_key: 'sk-test',
        model: 'gpt-4o',
        // prompts-031 change 1: the discovered catalog is persisted so
        // the new provider's card keeps the model dropdown after Save.
        available_models: ['gpt-4o', 'gpt-4o-mini'],
      }),
    )
    expect(onAdded).toHaveBeenCalledTimes(1)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('omits available_models when adding from an empty catalog (free-text model)', async () => {
    vi.mocked(api.llm.discoverDraft).mockResolvedValue(discover200EmptyCatalog)
    vi.mocked(api.llm.testProviderDraft).mockResolvedValue(probeOk)
    vi.mocked(api.llm.addProvider).mockResolvedValue({
      name: 'my-openai', kind: 'openai',
      model: 'my-local-model', has_api_key: true, skip_tls_verify: false,
    })
    render(<AddProviderWizard existingNames={[]} onClose={() => {}} onAdded={() => {}} />)
    await connectToProvider()
    await waitFor(() => expect(screen.getByTestId('stage-2')).toBeInTheDocument())
    fireEvent.change(screen.getByLabelText('Model') as HTMLInputElement, {
      target: { value: 'my-local-model' },
    })
    fireEvent.click(screen.getByRole('button', { name: /Test Model/i }))
    await waitFor(() => expect(screen.getByTestId('stage-4')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /Add Provider/i }))
    await waitFor(() => expect(api.llm.addProvider).toHaveBeenCalledTimes(1))
    const body = vi.mocked(api.llm.addProvider).mock.calls[0][0]
    expect(body.model).toBe('my-local-model')
    expect(body.available_models).toBeUndefined()
  })

  it('addProvider error renders inline without closing', async () => {
    vi.mocked(api.llm.discoverDraft).mockResolvedValue(discoverOk)
    vi.mocked(api.llm.testProviderDraft).mockResolvedValue(probeOk)
    vi.mocked(api.llm.addProvider).mockRejectedValue(new Error('save kaboom'))
    const onClose = vi.fn()
    render(<AddProviderWizard existingNames={[]} onClose={onClose} onAdded={() => {}} />)
    await connectToProvider()
    await waitFor(() => expect(screen.getByTestId('stage-2')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /Test Model/i }))
    await waitFor(() => expect(screen.getByTestId('stage-4')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /Add Provider/i }))
    await waitFor(() => expect(screen.getByText(/save kaboom/i)).toBeInTheDocument())
    expect(onClose).not.toHaveBeenCalled()
  })

  it('View test details opens TestDetailsModal after Connect', async () => {
    vi.mocked(api.llm.discoverDraft).mockResolvedValue(discoverOk)
    render(<AddProviderWizard existingNames={[]} onClose={() => {}} onAdded={() => {}} />)
    await connectToProvider()
    await waitFor(() =>
      expect(screen.getByText(/View test details/i)).toBeInTheDocument(),
    )
    fireEvent.click(screen.getByText(/View test details/i))
    // Modal heading carries the provider label.
    expect(screen.getByText(/Test details — my-openai/i)).toBeInTheDocument()
    expect(screen.getByText(/#1 list_models/)).toBeInTheDocument()
  })

  it('shows openai_compatible base_url hint only for that kind', () => {
    render(<AddProviderWizard existingNames={[]} onClose={() => {}} onAdded={() => {}} />)
    expect(screen.queryByText(/vLLM, LM Studio/i)).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Kind'), {
      target: { value: 'openai_compatible' },
    })
    expect(screen.getByText(/vLLM, LM Studio/i)).toBeInTheDocument()
  })

  // ── prompts-028: dropdown decoupled from aggregate status ──────────────

  it('reveals the dropdown when a 200 returned models but status==="error"', async () => {
    vi.mocked(api.llm.discoverDraft).mockResolvedValue(discover200ModelsButStatusError)
    render(<AddProviderWizard existingNames={[]} onClose={() => {}} onAdded={() => {}} />)
    await connectToProvider()
    await waitFor(() => expect(screen.getByTestId('stage-2')).toBeInTheDocument())
    // Dropdown present with the discovered options — NOT a red error.
    const select = screen.getByLabelText('Model') as HTMLSelectElement
    expect(select.tagName).toBe('SELECT')
    expect(screen.getByRole('option', { name: 'llama3' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'mistral' })).toBeInTheDocument()
    // Green "Models discovered" pill, no red verdict, no bare fallback.
    expect(screen.getByTestId('discover-verdict-ok')).toBeInTheDocument()
    expect(screen.queryByTestId('discover-verdict-error')).not.toBeInTheDocument()
    expect(screen.queryByText(/Discover failed/i)).not.toBeInTheDocument()
    // First model auto-picked so Test Model is immediately usable.
    expect(select.value).toBe('llama3')
  })

  it('empty catalog (200, 0 models) shows amber note + free-text and reaches stage 4', async () => {
    vi.mocked(api.llm.discoverDraft).mockResolvedValue(discover200EmptyCatalog)
    vi.mocked(api.llm.testProviderDraft).mockResolvedValue(probeOk)
    render(<AddProviderWizard existingNames={[]} onClose={() => {}} onAdded={() => {}} />)
    await connectToProvider()
    await waitFor(() => expect(screen.getByTestId('stage-2')).toBeInTheDocument())
    // Amber empty-catalog note, NOT a red error.
    expect(screen.getByTestId('discover-empty-catalog')).toBeInTheDocument()
    expect(screen.getByTestId('discover-empty-catalog')).toHaveTextContent(
      /0 models published/i,
    )
    expect(screen.queryByTestId('discover-verdict-error')).not.toBeInTheDocument()
    expect(screen.queryByText(/Discover failed/i)).not.toBeInTheDocument()
    // Free-text input (not a dropdown) since there is no catalog.
    const modelInput = screen.getByLabelText('Model') as HTMLInputElement
    expect(modelInput.tagName).toBe('INPUT')
    expect(
      screen.getByText(/enter the model id you want to use/i),
    ).toBeInTheDocument()
    // Operator types a model, probes, and reaches stage 4.
    fireEvent.change(modelInput, { target: { value: 'my-local-model' } })
    fireEvent.click(screen.getByRole('button', { name: /Test Model/i }))
    await waitFor(() =>
      expect(api.llm.testProviderDraft).toHaveBeenCalledWith(
        expect.objectContaining({ model: 'my-local-model' }),
      ),
    )
    await waitFor(() => expect(screen.getByTestId('stage-4')).toBeInTheDocument())
  })

  it('genuine transport failure (401, no models) still shows red error + hides stage 2', async () => {
    vi.mocked(api.llm.discoverDraft).mockResolvedValue(discoverFail)
    render(<AddProviderWizard existingNames={[]} onClose={() => {}} onAdded={() => {}} />)
    await connectToProvider()
    await waitFor(() =>
      expect(screen.getByTestId('discover-verdict-error')).toBeInTheDocument(),
    )
    expect(screen.getByText(/HTTP 401/i)).toBeInTheDocument()
    expect(screen.queryByTestId('stage-2')).not.toBeInTheDocument()
    expect(screen.queryByTestId('discover-empty-catalog')).not.toBeInTheDocument()
  })
})
