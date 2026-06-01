/**
 * Account page tests (prompts-046).
 *
 * The Account page replaces the former Configuration > Account tab. It shows
 * the signed-in identity and embeds the shared ChangePasswordCard in self mode.
 * The API client and useAuth hook are mocked so the page is exercised in
 * isolation.
 */
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import type { AuthUser } from '../api/client'

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...actual,
    api: { auth: { changePassword: vi.fn() } },
  }
})

vi.mock('../auth/useAuth', () => ({ useAuth: vi.fn() }))

import { api } from '../api/client'
import { useAuth } from '../auth/useAuth'
import Account from '../pages/Account'

const selfUser: AuthUser = { id: 7, username: 'reader', role: 'normal', enabled: true }

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(useAuth).mockReturnValue({
    loading: false,
    authEnabled: true,
    user: selfUser,
    isAuthenticated: true,
    isAdmin: false,
    passwordPolicy: { min_length: 8, required_classes: 3, max_bytes: 72 },
    login: vi.fn(),
    logout: vi.fn(),
    refresh: vi.fn(),
  })
})

function renderAccount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <Account />
    </QueryClientProvider>,
  )
}

describe('Account page', () => {
  it('shows the signed-in username and role', () => {
    renderAccount()
    expect(screen.getByText('reader')).toBeInTheDocument()
    expect(screen.getByText('normal')).toBeInTheDocument()
  })

  it('blocks submission when the new password reuses the current one', () => {
    renderAccount()
    fireEvent.change(screen.getByLabelText('Current password'), { target: { value: 'Adminpass1' } })
    fireEvent.change(screen.getByLabelText('New password'), { target: { value: 'Adminpass1' } })
    fireEvent.change(screen.getByLabelText('Confirm new password'), { target: { value: 'Adminpass1' } })
    expect(screen.getByText(/must differ from the current password/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /change password/i })).toBeDisabled()
  })

  it('blocks submission when the new password fails the policy', () => {
    renderAccount()
    fireEvent.change(screen.getByLabelText('Current password'), { target: { value: 'Adminpass1' } })
    fireEvent.change(screen.getByLabelText('New password'), { target: { value: 'lowercase12' } })
    fireEvent.change(screen.getByLabelText('Confirm new password'), { target: { value: 'lowercase12' } })
    expect(screen.getByText(/must include at least 3 of/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /change password/i })).toBeDisabled()
  })

  it('submits a valid self change-password and confirms success', async () => {
    vi.mocked(api.auth.changePassword).mockResolvedValue({ status: 'password_changed' })
    renderAccount()
    fireEvent.change(screen.getByLabelText('Current password'), { target: { value: 'Adminpass1' } })
    fireEvent.change(screen.getByLabelText('New password'), { target: { value: 'Newpass123' } })
    fireEvent.change(screen.getByLabelText('Confirm new password'), { target: { value: 'Newpass123' } })
    fireEvent.click(screen.getByRole('button', { name: /change password/i }))

    await waitFor(() => {
      expect(api.auth.changePassword).toHaveBeenCalledWith('Adminpass1', 'Newpass123')
    })
    expect(await screen.findByText('Password changed.')).toBeInTheDocument()
  })
})
