/**
 * User Management tab (prompts-045) — admin only.
 *
 * Lists users and provides create / enable-disable / role-change / reset-
 * password / delete actions, each backed by the /api/auth/users endpoints.
 *
 * The backend enforces the real invariants (cannot demote/disable/delete the
 * last admin; cannot act on your own role/enabled/account). This UI mirrors
 * the self-action guards by disabling those controls for the current user and
 * surfaces any backend rejection inline rather than trying to re-implement the
 * last-admin accounting client-side.
 *
 * Configuration only mounts this tab when authEnabled && isAdmin.
 */
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Trash2, KeyRound, Plus, X } from 'lucide-react'
import { api, type AuthUser, type UserRole } from '../../api/client'
import { useAuth } from '../../auth/useAuth'
import Toggle from '../../components/Toggle'
import ChangePasswordCard from '../../components/ChangePasswordCard'
import { describePasswordPolicy, validatePassword } from '../../utils/passwordPolicy'

const USERS_KEY = ['auth-users'] as const
const USERNAME_RE = /^[A-Za-z0-9._-]{1,40}$/

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

export default function UserManagementTab() {
  const qc = useQueryClient()
  const { user: self } = useAuth()
  const [actionError, setActionError] = useState<string | null>(null)
  const [resetFor, setResetFor] = useState<AuthUser | null>(null)
  // prompts-049: armed inline delete confirmation (mirrors the provider-delete
  // pattern) — the trash button arms it; the actual delete only fires from the
  // confirm panel.
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null)

  const { data: users = [], isLoading } = useQuery({
    queryKey: USERS_KEY,
    queryFn: api.auth.listUsers,
  })

  const invalidate = () => qc.invalidateQueries({ queryKey: USERS_KEY })

  const roleMut = useMutation({
    mutationFn: ({ id, role }: { id: number; role: UserRole }) => api.auth.setUserRole(id, role),
    onSuccess: () => { setActionError(null); invalidate() },
    onError: (e) => setActionError(errorMessage(e)),
  })
  const enabledMut = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      api.auth.setUserEnabled(id, enabled),
    onSuccess: () => { setActionError(null); invalidate() },
    onError: (e) => setActionError(errorMessage(e)),
  })
  const deleteMut = useMutation({
    mutationFn: (id: number) => api.auth.deleteUser(id),
    onSuccess: () => { setActionError(null); setConfirmDeleteId(null); invalidate() },
    onError: (e) => { setConfirmDeleteId(null); setActionError(errorMessage(e)) },
  })

  if (isLoading) return <div className="text-sm text-gray-500">Loading…</div>

  return (
    <div className="card space-y-5">
      <div>
        <h3 className="text-sm font-semibold text-gray-200">User Management</h3>
        <p className="text-xs text-gray-500 mt-1">
          Create accounts and manage roles. <span className="text-gray-300">Admin</span> users have
          full access; <span className="text-gray-300">Normal</span> users have read-only (viewer)
          access; <span className="text-gray-300">Sender</span> accounts can only push feeds to the
          listener endpoint. You cannot change your own role, disable, or delete your own account.
        </p>
      </div>

      {actionError !== null && (
        <p role="alert" className="text-xs text-red-400">{actionError}</p>
      )}

      <div className="space-y-2">
        {users.map((u) => {
          const isSelf = self?.id === u.id
          const armed = confirmDeleteId === u.id
          return (
            <div
              key={u.id}
              className="rounded-lg border border-gray-700 bg-gray-800/50"
            >
              <div className="flex items-center gap-3 px-3 py-2.5">
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-mono font-medium text-gray-200 truncate">
                    {u.username}
                    {isSelf && <span className="ml-2 text-[10px] text-gray-500">(you)</span>}
                  </p>
                  <p className="text-xs text-gray-500">
                    {u.enabled ? 'Active' : 'Disabled'}
                  </p>
                </div>

                {/* Role selector */}
                <select
                  className="input w-28 text-xs"
                  value={u.role}
                  disabled={isSelf || roleMut.isPending}
                  onChange={(e) => roleMut.mutate({ id: u.id, role: e.target.value as UserRole })}
                >
                  <option value="admin">admin</option>
                  <option value="normal">normal</option>
                  <option value="sender">sender</option>
                </select>

                {/* Enabled toggle */}
                <div className="flex items-center gap-1.5">
                  <Toggle
                    checked={u.enabled}
                    disabled={isSelf || enabledMut.isPending}
                    onChange={(enabled) => enabledMut.mutate({ id: u.id, enabled })}
                  />
                </div>

                <button
                  className="btn-ghost p-1"
                  title="Reset password"
                  onClick={() => { setActionError(null); setResetFor(u) }}
                >
                  <KeyRound className="w-3.5 h-3.5" />
                </button>
                <button
                  className="btn-ghost p-1 text-red-400 hover:text-red-300 disabled:opacity-40"
                  title={isSelf ? 'You cannot delete your own account' : 'Delete user'}
                  disabled={isSelf || deleteMut.isPending}
                  onClick={() => { setActionError(null); setConfirmDeleteId(u.id) }}
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>

              {armed && (
                <div
                  className="border-t border-red-500/40 bg-red-500/5 rounded-b-lg px-3 py-2.5 space-y-2"
                  data-testid={`delete-confirm-${u.id}`}
                  role="alertdialog"
                  aria-label={`Confirm delete ${u.username}`}
                >
                  <p className="text-xs text-gray-200">
                    Delete user "<span className="font-mono">{u.username}</span>"? This cannot be
                    undone.
                  </p>
                  <div className="flex items-center gap-2">
                    <button
                      className="btn-danger flex items-center gap-1.5"
                      onClick={() => deleteMut.mutate(u.id)}
                      disabled={deleteMut.isPending}
                      data-testid={`delete-confirm-yes-${u.id}`}
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                      {deleteMut.isPending ? 'Deleting…' : 'Confirm delete'}
                    </button>
                    <button
                      className="btn-secondary"
                      onClick={() => setConfirmDeleteId(null)}
                      disabled={deleteMut.isPending}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>

      <CreateUserForm onCreated={invalidate} onError={setActionError} />

      {resetFor !== null && (
        <ResetPasswordModal
          user={resetFor}
          onClose={() => setResetFor(null)}
        />
      )}
    </div>
  )
}

// ── Create user ───────────────────────────────────────────────────────────────

function CreateUserForm({
  onCreated,
  onError,
}: {
  onCreated: () => void
  onError: (msg: string | null) => void
}) {
  const { passwordPolicy } = useAuth()
  const [open, setOpen] = useState(false)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [role, setRole] = useState<UserRole>('normal')

  const mutation = useMutation({
    mutationFn: () => api.auth.createUser({ username, password, role }),
    onSuccess: () => {
      onError(null)
      setUsername('')
      setPassword('')
      setConfirm('')
      setRole('normal')
      setOpen(false)
      onCreated()
    },
    onError: (e) => onError(errorMessage(e)),
  })

  function reset() {
    setOpen(false)
    setUsername('')
    setPassword('')
    setConfirm('')
    onError(null)
  }

  if (!open) {
    return (
      <button className="btn-secondary w-full justify-center" onClick={() => setOpen(true)}>
        <Plus className="w-3.5 h-3.5" /> Add User
      </button>
    )
  }

  const usernameValid = USERNAME_RE.test(username)
  const policyError = password !== '' ? validatePassword(password, passwordPolicy) : null
  const confirmError =
    confirm !== '' && password !== confirm ? 'Passwords do not match.' : null
  const canSubmit =
    usernameValid &&
    password !== '' &&
    confirm !== '' &&
    policyError === null &&
    confirmError === null &&
    !mutation.isPending

  return (
    <div className="rounded-lg border border-brand-700/40 bg-brand-900/10 p-3 space-y-3">
      <p className="text-sm font-medium text-gray-300">New user</p>
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label htmlFor="new-username" className="label">Username</label>
          <input
            id="new-username"
            className="input font-mono"
            placeholder="analyst"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
        </div>
        <div>
          <label htmlFor="new-user-role" className="label">Role</label>
          <select
            id="new-user-role"
            className="input"
            value={role}
            onChange={(e) => setRole(e.target.value as UserRole)}
          >
            <option value="normal">normal</option>
            <option value="admin">admin</option>
            <option value="sender">sender</option>
          </select>
        </div>
      </div>
      <div>
        <label htmlFor="new-user-password" className="label">Password</label>
        <input
          id="new-user-password"
          type="password"
          autoComplete="new-password"
          className="input"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <p className="text-xs text-gray-500 mt-1">{describePasswordPolicy(passwordPolicy)}</p>
      </div>
      <div>
        <label htmlFor="new-user-confirm" className="label">Confirm password</label>
        <input
          id="new-user-confirm"
          type="password"
          autoComplete="new-password"
          className="input"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
        />
      </div>
      {username !== '' && !usernameValid && (
        <p className="text-xs text-red-400">
          Username must be 1–40 chars of letters, digits, &apos;.&apos;, &apos;_&apos; or &apos;-&apos;.
        </p>
      )}
      {policyError !== null && <p className="text-xs text-red-400">{policyError}</p>}
      {confirmError !== null && <p className="text-xs text-red-400">{confirmError}</p>}
      <div className="flex justify-end gap-2">
        <button className="btn-ghost" onClick={reset}>
          <X className="w-3.5 h-3.5" /> Cancel
        </button>
        <button className="btn-primary" disabled={!canSubmit} onClick={() => mutation.mutate()}>
          <Plus className="w-3.5 h-3.5" />
          {mutation.isPending ? 'Creating…' : 'Create'}
        </button>
      </div>
    </div>
  )
}

// ── Reset password ──────────────────────────────────────────────────────────

function ResetPasswordModal({
  user,
  onClose,
}: {
  user: AuthUser
  onClose: () => void
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="card w-full max-w-sm space-y-4">
        <div className="flex items-center justify-between">
          <h4 className="text-sm font-semibold text-gray-200">
            Reset password — <span className="font-mono">{user.username}</span>
          </h4>
          <button className="btn-ghost p-1" onClick={onClose}>
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Admin reset: no current-password field, no reuse constraint. The
            shared card renders its own success/error feedback. */}
        <ChangePasswordCard mode="admin" userId={user.id} />

        <div className="flex justify-end">
          <button className="btn-ghost text-xs" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  )
}
