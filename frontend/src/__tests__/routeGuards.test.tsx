/**
 * Route access-guard tests (prompts-046).
 *
 * Hiding the sidebar links is not enough — a normal user can still type an
 * admin URL directly. RequireAdmin / RequireAuthEnabled (App.tsx) bounce
 * unauthorised navigation back to /viewer. These tests exercise the real guard
 * components against a minimal router with stub destination pages.
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { describe, it, expect, vi } from 'vitest'

vi.mock('../auth/useAuth', () => ({ useAuth: vi.fn() }))

import { useAuth } from '../auth/useAuth'
import { RequireAdmin, RequireAuthEnabled } from '../App'

type AuthShape = {
  authEnabled: boolean
  isAdmin: boolean
}

function mockAuth(shape: AuthShape) {
  vi.mocked(useAuth).mockReturnValue(shape as unknown as ReturnType<typeof useAuth>)
}

function renderGuarded(guarded: React.ReactElement, initialPath: string) {
  // Mirror App.tsx's shell shape (a pathless parent with sibling routes) so the
  // guard's absolute "/viewer" redirect resolves exactly as it does in the app.
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route>
          <Route path="configuration" element={guarded} />
          <Route path="account" element={guarded} />
          <Route path="viewer" element={<div data-testid="viewer">viewer</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

describe('RequireAdmin', () => {
  it('redirects a normal user to viewer', () => {
    mockAuth({ authEnabled: true, isAdmin: false })
    renderGuarded(
      <RequireAdmin>
        <div data-testid="config">config</div>
      </RequireAdmin>,
      '/configuration',
    )
    expect(screen.getByTestId('viewer')).toBeInTheDocument()
    expect(screen.queryByTestId('config')).toBeNull()
  })

  it('renders the page for an admin user', () => {
    mockAuth({ authEnabled: true, isAdmin: true })
    renderGuarded(
      <RequireAdmin>
        <div data-testid="config">config</div>
      </RequireAdmin>,
      '/configuration',
    )
    expect(screen.getByTestId('config')).toBeInTheDocument()
  })

  it('renders the page in open mode (auth disabled → admin-equivalent)', () => {
    mockAuth({ authEnabled: false, isAdmin: true })
    renderGuarded(
      <RequireAdmin>
        <div data-testid="config">config</div>
      </RequireAdmin>,
      '/configuration',
    )
    expect(screen.getByTestId('config')).toBeInTheDocument()
  })
})

describe('RequireAuthEnabled', () => {
  it('renders the account page when auth is enabled', () => {
    mockAuth({ authEnabled: true, isAdmin: false })
    renderGuarded(
      <RequireAuthEnabled>
        <div data-testid="account">account</div>
      </RequireAuthEnabled>,
      '/account',
    )
    expect(screen.getByTestId('account')).toBeInTheDocument()
  })

  it('redirects the account page to viewer in open mode (auth disabled)', () => {
    mockAuth({ authEnabled: false, isAdmin: true })
    renderGuarded(
      <RequireAuthEnabled>
        <div data-testid="account">account</div>
      </RequireAuthEnabled>,
      '/account',
    )
    expect(screen.getByTestId('viewer')).toBeInTheDocument()
    expect(screen.queryByTestId('account')).toBeNull()
  })
})
