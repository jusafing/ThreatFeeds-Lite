/**
 * External-sources Refresh persistence across section changes (prompts-060).
 *
 * Every External refresh control (per-source and "Refresh all") routes through
 * a react-query mutation in the app-global MutationCache instead of
 * component-local useState. Unmounting the Configuration sub-tab mid-refresh
 * (the operator navigates to another section) and remounting it must restore
 * the in-progress indicator, because a synchronous backend POST is still
 * running. Settle-time invalidation of the section list must fire even if the
 * triggering component was unmounted.
 */
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...actual,
    api: {
      ...actual.api,
      getApiPull: vi.fn(),
      refreshApiPull: vi.fn(),
      refreshAllApiPull: vi.fn(),
    },
  }
})

import { api } from '../api/client'
import { ApiTab } from '../pages/Configuration'
import {
  EXTERNAL_REFRESH_KEY,
  EXTERNAL_REFRESH_ALL_KEY,
  EXTERNAL_REFRESH_INVALIDATE,
  type RefreshKind,
  type SourceRefreshVars,
  type RefreshAllVars,
} from '../hooks/useExternalRefresh'

interface Deferred<T> {
  promise: Promise<T>
  resolve: (v: T) => void
}
function deferred<T>(): Deferred<T> {
  let resolve!: (v: T) => void
  const promise = new Promise<T>((r) => { resolve = r })
  return { promise, resolve }
}

/** App-global client that mirrors main.tsx: persists across nav + settle invalidation. */
function makeClient() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const invalidate = (kind: RefreshKind | undefined) => {
    if (!kind) return
    qc.invalidateQueries({ queryKey: [...EXTERNAL_REFRESH_INVALIDATE[kind]] })
  }
  qc.setMutationDefaults([...EXTERNAL_REFRESH_KEY], {
    onSettled: (_d, _e, vars) => invalidate((vars as SourceRefreshVars | undefined)?.kind),
  })
  qc.setMutationDefaults([...EXTERNAL_REFRESH_ALL_KEY], {
    onSettled: (_d, _e, vars) => invalidate((vars as RefreshAllVars | undefined)?.kind),
  })
  return qc
}

function renderTab(qc: QueryClient) {
  return render(
    <QueryClientProvider client={qc}>
      <ApiTab />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('External Refresh persistence (prompts-060)', () => {
  it('restores the per-source spinner after the tab unmounts and remounts mid-refresh', async () => {
    vi.mocked(api.getApiPull).mockResolvedValue([
      { name: 'feed-a', url: 'https://x/a', enabled: true, interval_minutes: 15 },
    ])
    const d = deferred<{ inserted: number; skipped: number; errors: string[] }>()
    vi.mocked(api.refreshApiPull).mockReturnValue(d.promise)

    const qc = makeClient()
    const view = renderTab(qc)

    const btn = await screen.findByTitle('Manual refresh')
    fireEvent.click(btn)
    // Refresh is in flight → control is disabled.
    await waitFor(() => expect(screen.getByTitle('Manual refresh')).toBeDisabled())

    // Navigate away (unmount) then back (remount) under the same client.
    view.unmount()
    renderTab(qc)

    // The in-progress indicator is restored from the global MutationCache.
    expect(await screen.findByTitle('Manual refresh')).toBeDisabled()

    // Settle → control re-enables and the section list is invalidated/refetched.
    await act(async () => {
      d.resolve({ inserted: 3, skipped: 0, errors: [] })
      await d.promise
    })
    await waitFor(() => expect(screen.getByTitle('Manual refresh')).not.toBeDisabled())
    // getApiPull: initial + post-remount + settle-invalidation refetch.
    await waitFor(() => expect(vi.mocked(api.getApiPull).mock.calls.length).toBeGreaterThanOrEqual(3))
  })

  it('restores the "Refresh all" indicator after unmount and remount mid-refresh', async () => {
    vi.mocked(api.getApiPull).mockResolvedValue([])
    const d = deferred<{
      kind: string; total: number; succeeded: number; failed: number; results: [] }>()
    vi.mocked(api.refreshAllApiPull).mockReturnValue(d.promise)

    const qc = makeClient()
    const view = renderTab(qc)

    fireEvent.click(await screen.findByRole('button', { name: /Refresh all/i }))
    expect(await screen.findByRole('button', { name: /Refreshing…/i })).toBeInTheDocument()

    view.unmount()
    renderTab(qc)

    // "Refreshing…" is restored from the global cache, not lost on unmount.
    expect(await screen.findByRole('button', { name: /Refreshing…/i })).toBeInTheDocument()

    await act(async () => {
      d.resolve({ kind: 'api_pull', total: 2, succeeded: 2, failed: 0, results: [] })
      await d.promise
    })
    expect(await screen.findByTestId('refresh-all-summary')).toHaveTextContent('Refreshed 2/2')
  })
})
