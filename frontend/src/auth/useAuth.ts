/**
 * useAuth hook (prompts-045).
 *
 * Separate module so it is the file's only export (react-refresh rule).
 */
import { useContext } from 'react'
import { AuthContext, type AuthContextValue } from './context'

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (ctx === null) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return ctx
}
