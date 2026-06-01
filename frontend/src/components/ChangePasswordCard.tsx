/**
 * Shared change-password form (prompts-046).
 *
 * Unifies the two password-change flows that previously lived in AccountTab and
 * the User Management reset modal:
 *
 *   mode="self"  — the signed-in user changing their OWN password. Requires the
 *                  current password, and enforces new != current client-side
 *                  (the backend enforces it authoritatively). Calls
 *                  api.auth.changePassword.
 *   mode="admin" — an admin resetting ANOTHER user's password (or their own row
 *                  in User Management). No current-password field, no reuse
 *                  constraint. Calls api.auth.resetUserPassword(userId, …).
 *
 * Both modes require a confirmation field and enforce the active password policy
 * (length + character classes) via validatePassword. The policy comes from the
 * auth context (fetched from GET /api/auth/status).
 */
import { useState, type FormEvent } from 'react'
import { useMutation } from '@tanstack/react-query'
import { api } from '../api/client'
import { useAuth } from '../auth/useAuth'
import { describePasswordPolicy, validatePassword } from '../utils/passwordPolicy'

interface ChangePasswordCardProps {
  mode: 'self' | 'admin'
  /** Target user id — required when mode === 'admin'. */
  userId?: number
  /** Optional label shown above the form (e.g. the target username). */
  heading?: string
  /** Called after a successful change. */
  onSuccess?: () => void
}

export default function ChangePasswordCard({
  mode,
  userId,
  heading,
  onSuccess,
}: ChangePasswordCardProps) {
  const { passwordPolicy } = useAuth()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [done, setDone] = useState(false)

  const mutation = useMutation({
    mutationFn: () => {
      if (mode === 'admin') {
        if (userId === undefined) {
          return Promise.reject(new Error('Missing target user'))
        }
        return api.auth.resetUserPassword(userId, next)
      }
      return api.auth.changePassword(current, next)
    },
    onSuccess: () => {
      setDone(true)
      setCurrent('')
      setNext('')
      setConfirm('')
      onSuccess?.()
    },
  })

  const policyError = next !== '' ? validatePassword(next, passwordPolicy) : null

  let clientError: string | null = policyError
  if (clientError === null && confirm !== '' && next !== confirm) {
    clientError = 'New password and confirmation do not match.'
  } else if (
    clientError === null &&
    mode === 'self' &&
    next !== '' &&
    next === current
  ) {
    clientError = 'New password must differ from the current password.'
  }

  const canSubmit =
    next !== '' &&
    confirm !== '' &&
    (mode === 'admin' || current !== '') &&
    clientError === null &&
    !mutation.isPending

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (!canSubmit) return
    setDone(false)
    mutation.mutate()
  }

  function resetFeedback() {
    setDone(false)
    mutation.reset()
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      {heading !== undefined && (
        <p className="text-sm font-medium text-gray-300">{heading}</p>
      )}

      {mode === 'self' && (
        <div>
          <label htmlFor="current-password" className="label">
            Current password
          </label>
          <input
            id="current-password"
            type="password"
            autoComplete="current-password"
            className="input"
            value={current}
            onChange={(e) => {
              setCurrent(e.target.value)
              resetFeedback()
            }}
          />
        </div>
      )}

      <div>
        <label htmlFor="new-password" className="label">
          New password
        </label>
        <input
          id="new-password"
          type="password"
          autoComplete="new-password"
          className="input"
          value={next}
          onChange={(e) => {
            setNext(e.target.value)
            resetFeedback()
          }}
        />
        <p className="text-xs text-gray-500 mt-1">
          {describePasswordPolicy(passwordPolicy)}
        </p>
      </div>

      <div>
        <label htmlFor="confirm-password" className="label">
          Confirm new password
        </label>
        <input
          id="confirm-password"
          type="password"
          autoComplete="new-password"
          className="input"
          value={confirm}
          onChange={(e) => {
            setConfirm(e.target.value)
            resetFeedback()
          }}
        />
      </div>

      {clientError !== null && <p className="text-xs text-red-400">{clientError}</p>}
      {mutation.isError && (
        <p role="alert" className="text-xs text-red-400">
          {mutation.error instanceof Error
            ? mutation.error.message
            : 'Password change failed'}
        </p>
      )}
      {done && <p className="text-xs text-green-400">Password changed.</p>}

      <div className="flex justify-end">
        <button type="submit" className="btn-primary text-xs" disabled={!canSubmit}>
          {mutation.isPending
            ? 'Saving…'
            : mode === 'admin'
              ? 'Reset password'
              : 'Change password'}
        </button>
      </div>
    </form>
  )
}
