import { Routes, Route, Navigate } from 'react-router-dom'
import ProtectedLayout from './components/ProtectedLayout'
import Viewer from './pages/Viewer'
import Configuration from './pages/Configuration'
import Normalizer from './pages/Normalizer'
import Account from './pages/Account'
import About from './pages/About'
import Login from './pages/Login'
import { useAuth } from './auth/useAuth'
import { KNOWN_ROUTES } from './utils/basePrefix'

// Routes use RELATIVE paths (no leading slash) so the application produces
// document-relative <a href> values when no base prefix is configured.
// React Router v6 resolves these against the router's basename (which is
// set from the runtime app-base-prefix in main.tsx — itself resolved via
// the three-tier precedence in utils/basePrefix.ts).
//
// KNOWN_ROUTES is the single source of truth shared with the auto-detect
// logic in utils/basePrefix.ts. It includes 'login', which is rendered
// OUTSIDE the sidebar shell. The in-shell SHELL_ROUTES are derived by
// excluding 'login'; each maps to a page component below. Adding a new
// in-shell route REQUIRES adding the slug to KNOWN_ROUTES so reverse-proxy
// alias auto-detection continues to recognise it as a route-suffix.
type ShellRoute = Exclude<(typeof KNOWN_ROUTES)[number], 'login'>

const SHELL_ROUTES = KNOWN_ROUTES.filter((r): r is ShellRoute => r !== 'login')

const PAGE_COMPONENTS: Record<ShellRoute, React.ComponentType> = {
  viewer: Viewer,
  configuration: Configuration,
  normalizer: Normalizer,
  account: Account,
  about: About,
}

// Route-level access guards (prompts-046). Hiding the sidebar links is not
// sufficient — a normal user can still type an admin URL directly. These
// wrappers bounce unauthorised navigation back to /viewer (the one page every
// authenticated user can always reach).
//
// The redirect target is the ABSOLUTE in-router path "/viewer" (not a relative
// "viewer", which from e.g. /configuration would resolve to the nonexistent
// /configuration/viewer). React Router applies the router basename to absolute
// paths, so this stays correct under a reverse-proxy base prefix.
//
// In open mode (auth disabled) isAdmin is true and authEnabled is false, so:
//   - RequireAdmin lets every page through (the app is fully open), and
//   - RequireAuthEnabled redirects the account page to /viewer (there is no
//     signed-in user to manage when auth is off).
const ADMIN_ONLY_ROUTES = new Set<ShellRoute>(['configuration', 'normalizer'])
const AUTH_ENABLED_ROUTES = new Set<ShellRoute>(['account'])

function RequireAdmin({ children }: { children: React.ReactElement }) {
  const { isAdmin } = useAuth()
  return isAdmin ? children : <Navigate to="/viewer" replace />
}

function RequireAuthEnabled({ children }: { children: React.ReactElement }) {
  const { authEnabled } = useAuth()
  return authEnabled ? children : <Navigate to="/viewer" replace />
}

export { RequireAdmin, RequireAuthEnabled }

function guard(slug: ShellRoute, element: React.ReactElement): React.ReactElement {
  if (ADMIN_ONLY_ROUTES.has(slug)) return <RequireAdmin>{element}</RequireAdmin>
  if (AUTH_ENABLED_ROUTES.has(slug)) return <RequireAuthEnabled>{element}</RequireAuthEnabled>
  return element
}

export default function App() {
  return (
    <Routes>
      {/* Login is rendered outside the sidebar shell. */}
      <Route path="login" element={<Login />} />

      {/* Everything else lives inside the authenticated shell. */}
      <Route element={<ProtectedLayout />}>
        <Route index element={<Navigate to="viewer" replace />} />
        {SHELL_ROUTES.map((slug) => {
          const Component = PAGE_COMPONENTS[slug]
          return <Route key={slug} path={slug} element={guard(slug, <Component />)} />
        })}
      </Route>
    </Routes>
  )
}
