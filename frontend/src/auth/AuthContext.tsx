/**
 * Authentication provider (prompts-045).
 *
 * Bootstraps the SPA's auth state:
 *   1. GET /api/auth/status — is enforcement on?
 *        - auth_enabled === false → the app is fully OPEN. No login screen;
 *          every nav item shows; the UI behaves as an admin-equivalent.
 *        - auth_enabled === true  → fetch the current user via /api/auth/me.
 *   2. A module-level 401 handler (registered with the API client) drops the
 *      cached user on any expired/invalid session so ProtectedLayout bounces
 *      the user to /login.
 *
 * `isAdmin` is true when auth is disabled (open app) OR the user's role is
 * 'admin'. `isAuthenticated` is true when auth is disabled OR a user is set.
 *
 * The context object and useAuth hook live in sibling modules (context.ts,
 * useAuth.ts) so this file exports only the <AuthProvider> component.
 */
import { useEffect, useState, useCallback, type ReactNode } from 'react'
import { api, setUnauthorizedHandler, type PasswordPolicy } from '../api/client'
import { AuthContext, type AuthContextValue } from './context'
import { DEFAULT_PASSWORD_POLICY } from '../utils/passwordPolicy'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [loading, setLoading] = useState(true)
  const [authEnabled, setAuthEnabled] = useState(false)
  const [user, setUser] = useState<AuthContextValue['user']>(null)
  const [passwordPolicy, setPasswordPolicy] =
    useState<PasswordPolicy>(DEFAULT_PASSWORD_POLICY)

  const loadMe = useCallback(async () => {
    try {
      const { user } = await api.auth.me()
      setUser(user)
    } catch {
      // 401 / network → treat as logged out.
      setUser(null)
    }
  }, [])

  const bootstrap = useCallback(async () => {
    setLoading(true)
    try {
      const { auth_enabled, password_policy } = await api.auth.status()
      setAuthEnabled(auth_enabled)
      setPasswordPolicy(password_policy ?? DEFAULT_PASSWORD_POLICY)
      if (auth_enabled) {
        await loadMe()
      } else {
        setUser(null)
      }
    } catch {
      // Status endpoint unreachable → assume disabled so the app still renders
      // rather than trapping the user on a dead login page.
      setAuthEnabled(false)
      setUser(null)
    } finally {
      setLoading(false)
    }
  }, [loadMe])

  useEffect(() => {
    void bootstrap()
  }, [bootstrap])

  // Drop the cached user whenever any request returns 401.
  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null))
    return () => setUnauthorizedHandler(null)
  }, [])

  const login = useCallback(async (username: string, password: string) => {
    const { user } = await api.auth.login(username, password)
    setAuthEnabled(true)
    setUser(user)
  }, [])

  const logout = useCallback(async () => {
    try {
      await api.auth.logout()
    } finally {
      setUser(null)
    }
  }, [])

  const value: AuthContextValue = {
    loading,
    authEnabled,
    user,
    isAuthenticated: !authEnabled || user !== null,
    isAdmin: !authEnabled || user?.role === 'admin',
    passwordPolicy,
    login,
    logout,
    refresh: loadMe,
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
