/**
 * Tests for the shared normalizer-run hook (prompts-040).
 *
 * The key guarantee: every run button reads its disabled/running state from a
 * single global flag (useNormalizerRunning → useIsMutating(NORMALIZER_RUN_KEY)),
 * so a run started by ONE consumer is reflected by EVERY other consumer, even
 * across separate components. This is what keeps the lock consistent and makes
 * it survive navigation (the mutation lives in the app-global MutationCache).
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...actual,
    api: {
      runNormalizer: vi.fn(),
      smartMappings: { runActive: vi.fn() },
    },
  }
})

import { api } from '../api/client'
import { useRunNow, useRunActive, useNormalizerRunning } from '../hooks/useNormalizerRun'

// Two independent consumers under one client: A starts a run, B only observes.
function Starter() {
  const runNow = useRunNow()
  return (
    <button onClick={() => runNow.mutate()}>start-a</button>
  )
}

function ActiveStarter() {
  const runActive = useRunActive()
  return (
    <button onClick={() => runActive.mutate()}>start-b</button>
  )
}

function Observer() {
  const running = useNormalizerRunning()
  return <span data-testid="observer">{running ? 'running' : 'idle'}</span>
}

function renderHarness() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <Starter />
      <ActiveStarter />
      <Observer />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.runNormalizer).mockReset()
  vi.mocked(api.smartMappings.runActive).mockReset()
})

describe('useNormalizerRun shared global lock', () => {
  it('reflects a Run Now start to an independent observer, then clears', async () => {
    let resolveRun: (v: Record<string, unknown>) => void = () => {}
    vi.mocked(api.runNormalizer).mockReturnValue(
      new Promise<Record<string, unknown>>((res) => {
        resolveRun = res
      }),
    )
    renderHarness()
    expect(screen.getByTestId('observer')).toHaveTextContent('idle')

    fireEvent.click(screen.getByText('start-a'))
    await waitFor(() => expect(screen.getByTestId('observer')).toHaveTextContent('running'))

    resolveRun({ status: 'ok' })
    await waitFor(() => expect(screen.getByTestId('observer')).toHaveTextContent('idle'))
  })

  it('also reflects an active-mapping Run via the same shared flag', async () => {
    let resolveRun: (v: { reset_rows: number }) => void = () => {}
    vi.mocked(api.smartMappings.runActive).mockReturnValue(
      new Promise<{ reset_rows: number }>((res) => {
        resolveRun = res
      }),
    )
    renderHarness()

    fireEvent.click(screen.getByText('start-b'))
    await waitFor(() => expect(screen.getByTestId('observer')).toHaveTextContent('running'))

    resolveRun({ reset_rows: 0 })
    await waitFor(() => expect(screen.getByTestId('observer')).toHaveTextContent('idle'))
  })
})
