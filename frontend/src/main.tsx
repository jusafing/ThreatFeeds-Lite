import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App'
import { AuthProvider } from './auth/AuthContext'
import { getAppBasePrefix } from './utils/basePrefix'
import { api } from './api/client'
import {
  NORMALIZER_RUN_KEY,
  NORMALIZER_RUN_INVALIDATE_KEYS,
} from './hooks/useNormalizerRun'
import {
  EXTERNAL_REFRESH_KEY,
  EXTERNAL_REFRESH_ALL_KEY,
  EXTERNAL_REFRESH_INVALIDATE,
  type RefreshKind,
  type SourceRefreshVars,
  type RefreshAllVars,
} from './hooks/useExternalRefresh'
import './index.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 5_000,
    },
  },
})

// prompts-040: refresh the run-affected queries whenever any normalizer run
// settles. Registered as a mutation default (not a per-component onSettled) so
// the invalidation fires even if the component that triggered the run has
// already unmounted — e.g. the operator navigated to another section mid-run.
queryClient.setMutationDefaults(NORMALIZER_RUN_KEY, {
  onSettled: () => {
    for (const queryKey of NORMALIZER_RUN_INVALIDATE_KEYS) {
      queryClient.invalidateQueries({ queryKey: [...queryKey] })
    }
  },
})

// prompts-060: persist External-sources refresh state across section changes.
// Per-source and "Refresh all" mutations live in the global MutationCache so
// their in-flight state survives a Configuration sub-tab unmount. Settle-time
// invalidation of the affected section list is registered here (not as a
// per-component onSettled) so the list refreshes even when the operator
// navigated away mid-refresh and the triggering component is unmounted.
function invalidateRefreshKind(kind: RefreshKind | undefined) {
  if (!kind) return
  queryClient.invalidateQueries({ queryKey: [...EXTERNAL_REFRESH_INVALIDATE[kind]] })
  if (kind === 'remote-json-pull') {
    api.reloadScheduler().catch(() => {})
  }
}

queryClient.setMutationDefaults(EXTERNAL_REFRESH_KEY, {
  onSettled: (_data, _error, variables) => {
    invalidateRefreshKind((variables as SourceRefreshVars | undefined)?.kind)
  },
})

queryClient.setMutationDefaults(EXTERNAL_REFRESH_ALL_KEY, {
  onSettled: (_data, _error, variables) => {
    invalidateRefreshKind((variables as RefreshAllVars | undefined)?.kind)
  },
})

// When the app is deployed behind a reverse proxy under a sub-path, the
// backend injects the active prefix into index.html. The router uses it as
// basename so all <Link to="..."> URLs resolve correctly.
const basename = getAppBasePrefix() || undefined

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter basename={basename}>
        <AuthProvider>
          <App />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
)
