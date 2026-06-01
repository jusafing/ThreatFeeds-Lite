/**
 * Auth flow tests (prompts-045) — AuthProvider bootstrap, ProtectedLayout
 * guarding, and the Login screen.
 *
 * The API client is mocked so we can drive /api/auth/status and /api/auth/me
 * deterministically. setUnauthorizedHandler is passed through to the real
 * implementation so the 401 wiring is exercised end-to-end.
 */
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, it, expect, beforeEach, vi } from 'vitest'

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...actual,
    api: {
      auth: {
        status: vi.fn(),
        me: vi.fn(),
        login: vi.fn(),
        logout: vi.fn(),
        changePassword: vi.fn(),
      },
      // Sidebar (rendered inside ProtectedLayout) queries logo presence.
      getLogoInfo: vi.fn().mockResolvedValue({ has_logo: false }),
    },
  }
})

import { api, type AuthUser } from '../api/client'
import { AuthProvider } from '../auth/AuthContext'
import ProtectedLayout from '../components/ProtectedLayout'
import Login from '../pages/Login'

const adminUser: AuthUser = { id: 1, username: 'admin', role: 'admin', enabled: true }

function renderApp(initialPath = '/') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[initialPath]}>
        <AuthProvider>
          <Routes>
            <Route path="login" element={<Login />} />
            <Route element={<ProtectedLayout />}>
              <Route index element={<Navigate to="viewer" replace />} />
              <Route path="viewer" element={<div>VIEWER PAGE</div>} />
            </Route>
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.auth.status).mockReset()
  vi.mocked(api.auth.me).mockReset()
  vi.mocked(api.auth.login).mockReset()
  vi.mocked(api.auth.logout).mockReset()
  vi.mocked(api.auth.changePassword).mockReset()
})

describe('AuthProvider bootstrap + ProtectedLayout (prompts-045)', () => {
  it('renders the app without a login screen when auth is disabled (open app)', async () => {
    vi.mocked(api.auth.status).mockResolvedValue({ auth_enabled: false })

    renderApp('/')

    expect(await screen.findByText('VIEWER PAGE')).toBeInTheDocument()
    // /api/auth/me must NOT be called when auth is disabled.
    expect(api.auth.me).not.toHaveBeenCalled()
  })

  it('redirects to login when auth is enabled and no session exists', async () => {
    vi.mocked(api.auth.status).mockResolvedValue({ auth_enabled: true })
    vi.mocked(api.auth.me).mockRejectedValue(new Error('401 Unauthorized'))

    renderApp('/viewer')

    expect(await screen.findByLabelText('Username')).toBeInTheDocument()
    expect(screen.queryByText('VIEWER PAGE')).not.toBeInTheDocument()
  })

  it('renders the protected page when auth is enabled and a session exists', async () => {
    vi.mocked(api.auth.status).mockResolvedValue({ auth_enabled: true })
    vi.mocked(api.auth.me).mockResolvedValue({ user: adminUser })

    renderApp('/viewer')

    expect(await screen.findByText('VIEWER PAGE')).toBeInTheDocument()
  })
})

describe('Login screen (prompts-045)', () => {
  it('signs in and navigates to the originally requested page', async () => {
    vi.mocked(api.auth.status).mockResolvedValue({ auth_enabled: true })
    vi.mocked(api.auth.me).mockRejectedValue(new Error('401 Unauthorized'))
    vi.mocked(api.auth.login).mockResolvedValue({ user: adminUser })

    renderApp('/login')

    const username = await screen.findByLabelText('Username')
    fireEvent.change(username, { target: { value: 'admin' } })
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'secret' } })
    fireEvent.click(screen.getByRole('button', { name: /sign in/i }))

    await waitFor(() => {
      expect(api.auth.login).toHaveBeenCalledWith('admin', 'secret')
    })
    expect(await screen.findByText('VIEWER PAGE')).toBeInTheDocument()
  })

  it('surfaces the generic error message on a failed login', async () => {
    vi.mocked(api.auth.status).mockResolvedValue({ auth_enabled: true })
    vi.mocked(api.auth.me).mockRejectedValue(new Error('401 Unauthorized'))
    vi.mocked(api.auth.login).mockRejectedValue(new Error('401 Unauthorized: Invalid credentials'))

    renderApp('/login')

    fireEvent.change(await screen.findByLabelText('Username'), { target: { value: 'admin' } })
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'wrong' } })
    fireEvent.click(screen.getByRole('button', { name: /sign in/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/invalid credentials/i)
    expect(screen.queryByText('VIEWER PAGE')).not.toBeInTheDocument()
  })
})

describe('Forced password change (prompts-047)', () => {
  const flaggedUser: AuthUser = { ...adminUser, must_change_password: true }

  it('renders the forced change screen instead of the shell when the flag is set', async () => {
    vi.mocked(api.auth.status).mockResolvedValue({ auth_enabled: true })
    vi.mocked(api.auth.me).mockResolvedValue({ user: flaggedUser })

    renderApp('/viewer')

    expect(await screen.findByRole('heading', { name: /set a new password/i })).toBeInTheDocument()
    expect(screen.getByLabelText('Current password')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /log out/i })).toBeInTheDocument()
    expect(screen.queryByText('VIEWER PAGE')).not.toBeInTheDocument()
  })

  it('renders the shell once the flag clears after a successful change', async () => {
    vi.mocked(api.auth.status).mockResolvedValue({ auth_enabled: true })
    // bootstrap → flagged; refresh after change → flag cleared.
    vi.mocked(api.auth.me)
      .mockResolvedValueOnce({ user: flaggedUser })
      .mockResolvedValueOnce({ user: adminUser })
    vi.mocked(api.auth.changePassword).mockResolvedValue({ status: 'password_changed' })

    renderApp('/viewer')

    await screen.findByRole('heading', { name: /set a new password/i })

    fireEvent.change(screen.getByLabelText('Current password'), {
      target: { value: 'OldDefault1!' },
    })
    fireEvent.change(screen.getByLabelText('New password'), {
      target: { value: 'BrandNew2!' },
    })
    fireEvent.change(screen.getByLabelText('Confirm new password'), {
      target: { value: 'BrandNew2!' },
    })
    fireEvent.click(screen.getByRole('button', { name: /change password/i }))

    await waitFor(() => {
      expect(api.auth.changePassword).toHaveBeenCalledWith('OldDefault1!', 'BrandNew2!')
    })
    expect(await screen.findByText('VIEWER PAGE')).toBeInTheDocument()
  })

  it('does not force a change when the flag is absent', async () => {
    vi.mocked(api.auth.status).mockResolvedValue({ auth_enabled: true })
    vi.mocked(api.auth.me).mockResolvedValue({ user: adminUser })

    renderApp('/viewer')

    expect(await screen.findByText('VIEWER PAGE')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /set a new password/i })).not.toBeInTheDocument()
  })
})
