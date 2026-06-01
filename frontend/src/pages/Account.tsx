/**
 * Account page (prompts-046).
 *
 * Self-service for the signed-in user: shows identity (username + role) and a
 * change-password form. Replaces the former Configuration > Account tab
 * (AccountTab.tsx, removed) and is reachable from the sidebar.
 *
 * Only meaningful when auth enforcement is enabled; App.tsx wraps this route in
 * RequireAuthEnabled, which redirects to /viewer when auth is disabled. The
 * change-password form itself lives in the shared ChangePasswordCard component.
 */
import ChangePasswordCard from '../components/ChangePasswordCard'
import { useAuth } from '../auth/useAuth'

export default function Account() {
  const { user } = useAuth()

  return (
    <div className="p-6 max-w-md space-y-6">
      <div>
        <h1 className="text-lg font-semibold text-gray-100">Account</h1>
        <p className="text-sm text-gray-500">Your sign-in identity and password.</p>
      </div>

      <div className="card space-y-5">
        <div className="grid grid-cols-2 gap-3">
          <div>
            <p className="label">Username</p>
            <p className="text-sm text-gray-200 font-mono">{user?.username ?? '—'}</p>
          </div>
          <div>
            <p className="label">Role</p>
            <p className="text-sm text-gray-200 capitalize">{user?.role ?? '—'}</p>
          </div>
        </div>

        <div className="border-t border-gray-800 pt-4">
          <ChangePasswordCard mode="self" heading="Change password" />
        </div>
      </div>
    </div>
  )
}
