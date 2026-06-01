/**
 * Shared normalizer-run state (prompts-040).
 *
 * Every action that triggers a normalizer run — Settings "Run Now", the Smart
 * Mappings active-card "Run", and the Normalized Feeds viewer "Run & Apply" —
 * goes through a single react-query mutation keyed by NORMALIZER_RUN_KEY.
 *
 * Why a shared mutation instead of component-local `useState`:
 *   - The mutation lives in the app-global MutationCache, so its in-flight
 *     state survives a route/sub-tab unmount. Navigating away mid-run no longer
 *     loses the "running" indicator.
 *   - All run buttons read the running flag from `useIsMutating(NORMALIZER_RUN_KEY)`
 *     rather than their own `isPending`, giving one consistent global lock: any
 *     in-flight run disables every run button everywhere (no concurrent runs).
 *
 * Cache invalidation after a run is registered once via `setMutationDefaults`
 * in main.tsx (see NORMALIZER_RUN_INVALIDATE_KEYS) so the refresh fires even if
 * the component that started the run has already unmounted — mutation default
 * callbacks run independently of any mounted observer.
 */
import { useMutation, useIsMutating } from '@tanstack/react-query'
import { api } from '../api/client'

/** Shared mutation key for every normalizer-run action. */
export const NORMALIZER_RUN_KEY = ['normalizer-run'] as const

/**
 * Query keys invalidated when any normalizer run settles. Consumed by the
 * `setMutationDefaults(NORMALIZER_RUN_KEY, …)` registration in main.tsx.
 */
export const NORMALIZER_RUN_INVALIDATE_KEYS: readonly (readonly string[])[] = [
  ['normalized-entries'],
  ['normalizer-runs'],
  ['normalizer-summary'],
  ['normalizer-config'],
  ['smart-active'],
  ['consolidated-active'],
]

/**
 * True while any normalizer run (from any button) is in flight. Drives the
 * disabled state and running indicator on every run button.
 */
export function useNormalizerRunning(): boolean {
  return useIsMutating({ mutationKey: NORMALIZER_RUN_KEY }) > 0
}

/**
 * Run Now / re-apply via the mode-aware endpoint (POST /normalizer/run).
 * Used by Settings "Run Now" and the viewer "Run & Apply" button.
 */
export function useRunNow() {
  return useMutation({
    mutationKey: NORMALIZER_RUN_KEY,
    mutationFn: () => api.runNormalizer(),
  })
}

/**
 * Re-apply the active consolidated mapping (POST /smart-mappings/active/run).
 * Used by the Smart Mappings active-card "Run" button.
 */
export function useRunActive() {
  return useMutation({
    mutationKey: NORMALIZER_RUN_KEY,
    mutationFn: () => api.smartMappings.runActive(),
  })
}
