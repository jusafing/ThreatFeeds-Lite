/**
 * Login screen (prompts-045).
 *
 * Rendered OUTSIDE the sidebar shell (see App.tsx). Only reachable when auth
 * enforcement is enabled. On success the AuthContext caches the user and we
 * navigate to the route the user originally requested (location.state.from)
 * or fall back to the viewer.
 *
 * Security: the backend returns a single generic error for any failed login
 * (bad username, bad password, disabled account), so this screen must NOT try
 * to distinguish those cases — it surfaces whatever generic message the API
 * returns.
 */
import { useState, type FormEvent } from 'react'
import { useNavigate, useLocation, Navigate } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import { useAuth } from '../auth/useAuth'
import BrandLogo from '../components/BrandLogo'

interface LocationState {
  from?: { pathname: string }
}

export default function Login() {
  const { login, authEnabled, isAuthenticated, loading: authLoading } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // location.state.from.pathname is an absolute, basename-relative path (set by
  // ProtectedLayout). The fallback is the absolute '/viewer' so navigation
  // resolves against the router basename — NOT relative to '/login'.
  const from = (location.state as LocationState | null)?.from?.pathname ?? '/viewer'

  // If auth is disabled, or the user is already authenticated, there is no
  // login to perform — send them into the app.
  if (!authLoading && (!authEnabled || isAuthenticated)) {
    return <Navigate to={from} replace />
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await login(username.trim(), password)
      navigate(from, { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="flex items-center justify-center min-h-screen bg-gray-950 px-4">
      <div className="card w-full max-w-sm">
        <div className="flex flex-col items-center gap-3 mb-6">
          <BrandLogo size={48} />
          <div className="text-center">
            <h1 className="text-base font-semibold text-gray-100">ThreatFeeds Lite</h1>
            <p className="text-xs text-gray-500">Sign in to continue</p>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label htmlFor="username" className="label">
              Username
            </label>
            <input
              id="username"
              type="text"
              autoComplete="username"
              className="input"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              disabled={submitting}
              autoFocus
              required
            />
          </div>

          <div>
            <label htmlFor="password" className="label">
              Password
            </label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              className="input"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={submitting}
              required
            />
          </div>

          {error !== null && (
            <p role="alert" className="text-xs text-red-400">
              {error}
            </p>
          )}

          <button type="submit" className="btn-primary w-full justify-center" disabled={submitting}>
            {submitting && <Loader2 className="w-4 h-4 animate-spin" />}
            {submitting ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </div>
    </div>
  )
}
