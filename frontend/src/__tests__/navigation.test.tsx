/**
 * Navigation tests for prompts-019 State Y + prompts-020 alias auto-detect.
 *
 * Verifies that:
 *   - Sidebar NavLinks emit document-relative <a href> values under empty
 *     basename and root-anchored hrefs under non-empty basename.
 *   - getAppBasePrefix() implements the three-tier precedence:
 *       explicit meta > window.location auto-detect > empty.
 *   - The auto-detect strategy strips known-route suffixes when present
 *     and falls back to trailing-slash strip otherwise.
 */
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import Sidebar from '../components/Sidebar'
import {
  getAppBasePrefix,
  _resetAppBasePrefixCache,
  KNOWN_ROUTES,
} from '../utils/basePrefix'

// Sidebar (prompts-045) reads auth state via useAuth and queries logo-info.
// Mock both. `authState` is mutable so individual tests can flip to the
// 'normal' role or an authenticated user; it defaults to the "open app"
// state (auth disabled → admin-equivalent → every nav entry visible) which
// the legacy href-contract tests rely on.
type MockAuth = {
  authEnabled: boolean
  isAdmin: boolean
  isAuthenticated: boolean
  user: { username: string; role: string } | null
  logout: () => void
}

const logoutSpy = vi.fn()
const defaultAuth: MockAuth = {
  authEnabled: false,
  isAdmin: true,
  isAuthenticated: true,
  user: null,
  logout: logoutSpy,
}
let authState: MockAuth = { ...defaultAuth }

vi.mock('../auth/useAuth', () => ({
  useAuth: () => authState,
}))

vi.mock('../api/client', async (importActual) => {
  const actual = await importActual<typeof import('../api/client')>()
  return {
    ...actual,
    api: { ...actual.api, getLogoInfo: vi.fn().mockResolvedValue({ has_logo: false }) },
  }
})

function renderSidebar(basename: string | undefined, initialPath = '/') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter basename={basename} initialEntries={[initialPath]}>
        <Sidebar />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('Sidebar NavLink href contract (prompts-019)', () => {
  beforeEach(() => {
    authState = { ...defaultAuth }
    logoutSpy.mockClear()
    localStorage.clear()
  })

  it('renders root-anchored hrefs under empty basename when at "/"', () => {
    // React Router resolves relative `to="viewer"` against the current
    // route. With no basename and starting at "/", the rendered <a href>
    // is "/viewer".
    renderSidebar(undefined, '/')
    const link = screen.getByRole('link', { name: /viewer/i })
    expect(link.getAttribute('href')).toBe('/viewer')
  })

  it('prepends the basename to relative `to=` values', () => {
    renderSidebar('/feeds', '/feeds')
    const link = screen.getByRole('link', { name: /configuration/i })
    expect(link.getAttribute('href')).toBe('/feeds/configuration')
  })

  it('all four nav entries render with correct hrefs under root', () => {
    renderSidebar(undefined, '/')
    expect(screen.getByRole('link', { name: /viewer/i })
      .getAttribute('href')).toBe('/viewer')
    expect(screen.getByRole('link', { name: /configuration/i })
      .getAttribute('href')).toBe('/configuration')
    expect(screen.getByRole('link', { name: /normalizer/i })
      .getAttribute('href')).toBe('/normalizer')
    expect(screen.getByRole('link', { name: /about/i })
      .getAttribute('href')).toBe('/about')
  })

  it('all four nav entries render with prefix under non-empty basename', () => {
    renderSidebar('/feeds', '/feeds')
    expect(screen.getByRole('link', { name: /viewer/i })
      .getAttribute('href')).toBe('/feeds/viewer')
    expect(screen.getByRole('link', { name: /configuration/i })
      .getAttribute('href')).toBe('/feeds/configuration')
    expect(screen.getByRole('link', { name: /normalizer/i })
      .getAttribute('href')).toBe('/feeds/normalizer')
    expect(screen.getByRole('link', { name: /about/i })
      .getAttribute('href')).toBe('/feeds/about')
  })
})

describe('Sidebar behaviour (prompts-045: auth + collapse + logo)', () => {
  beforeEach(() => {
    authState = { ...defaultAuth }
    logoutSpy.mockClear()
    localStorage.clear()
  })

  it('restricts a normal user to Viewer, Account and About (prompts-046)', () => {
    authState = {
      authEnabled: true,
      isAdmin: false,
      isAuthenticated: true,
      user: { username: 'reader', role: 'normal' },
      logout: logoutSpy,
    }
    renderSidebar(undefined, '/')
    // Admin-only entries are hidden from a normal user.
    expect(screen.queryByRole('link', { name: /normalizer/i })).toBeNull()
    expect(screen.queryByRole('link', { name: /configuration/i })).toBeNull()
    // Viewer, Account and About remain.
    expect(screen.getByRole('link', { name: /viewer/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /account/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /about/i })).toBeInTheDocument()
  })

  it('shows admin-only entries and Account for an admin user (prompts-046)', () => {
    authState = {
      authEnabled: true,
      isAdmin: true,
      isAuthenticated: true,
      user: { username: 'admin', role: 'admin' },
      logout: logoutSpy,
    }
    renderSidebar(undefined, '/')
    expect(screen.getByRole('link', { name: /normalizer/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /configuration/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /account/i })).toBeInTheDocument()
  })

  it('hides the Account entry in open mode (auth disabled, prompts-046)', () => {
    // defaultAuth = auth disabled. Account is authOnly so it must not appear,
    // while the admin-equivalent open app still shows Configuration/Normalizer.
    renderSidebar(undefined, '/')
    expect(screen.queryByRole('link', { name: /account/i })).toBeNull()
    expect(screen.getByRole('link', { name: /configuration/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /normalizer/i })).toBeInTheDocument()
  })

  it('renders a sign-out control and invokes logout when auth is enabled', () => {
    authState = {
      authEnabled: true,
      isAdmin: true,
      isAuthenticated: true,
      user: { username: 'admin', role: 'admin' },
      logout: logoutSpy,
    }
    renderSidebar(undefined, '/')
    const signOut = screen.getByRole('button', { name: /sign out/i })
    fireEvent.click(signOut)
    expect(logoutSpy).toHaveBeenCalledTimes(1)
  })

  it('omits the sign-out control when auth is disabled (open app)', () => {
    renderSidebar(undefined, '/')
    expect(screen.queryByRole('button', { name: /sign out/i })).toBeNull()
  })

  it('toggles collapse and persists the state to localStorage', () => {
    renderSidebar(undefined, '/')
    expect(screen.getByText('Configuration')).toBeInTheDocument()
    const collapseBtn = screen.getByRole('button', { name: /collapse sidebar/i })
    fireEvent.click(collapseBtn)
    expect(localStorage.getItem('sfi.sidebar.collapsed')).toBe('1')
    expect(screen.getByRole('button', { name: /expand sidebar/i })).toBeInTheDocument()
  })

  it('restores the collapsed state from localStorage on mount', () => {
    localStorage.setItem('sfi.sidebar.collapsed', '1')
    renderSidebar(undefined, '/')
    expect(screen.getByRole('button', { name: /expand sidebar/i })).toBeInTheDocument()
  })
})

describe('Index route redirects to "viewer" (prompts-019)', () => {
  // Lightweight reproduction of App.tsx's router shape, without pulling in
  // the full page components and QueryClient.
  function MiniApp() {
    return (
      <Routes>
        <Route index element={<Navigate to="viewer" replace />} />
        <Route path="viewer" element={<div data-testid="viewer-page">viewer</div>} />
      </Routes>
    )
  }

  it('navigates to viewer under empty basename', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <MiniApp />
      </MemoryRouter>,
    )
    expect(screen.getByTestId('viewer-page')).toBeInTheDocument()
  })

  it('navigates to viewer under non-empty basename', () => {
    render(
      <MemoryRouter basename="/feeds" initialEntries={['/feeds']}>
        <MiniApp />
      </MemoryRouter>,
    )
    expect(screen.getByTestId('viewer-page')).toBeInTheDocument()
  })
})

/**
 * Helpers for the auto-detect suite.
 *
 * jsdom exposes window.location as a Location object; replacing it with
 * Object.defineProperty + a plain literal lets us drive pathname per test.
 * We restore the original after each case so other suites that rely on
 * jsdom's default "http://localhost/" are not affected.
 */
function setLocationPathname(pathname: string) {
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: {
      ...window.location,
      pathname,
      origin: 'http://localhost',
      href: `http://localhost${pathname}`,
    },
  })
}

function setMetaTag(content: string | null) {
  const existing = document.querySelector('meta[name="app-base-prefix"]')
  if (existing) existing.remove()
  if (content !== null) {
    const tag = document.createElement('meta')
    tag.setAttribute('name', 'app-base-prefix')
    tag.setAttribute('content', content)
    document.head.appendChild(tag)
  }
}

describe('getAppBasePrefix() three-tier precedence (prompts-020)', () => {
  let originalLocation: Location

  beforeEach(() => {
    _resetAppBasePrefixCache()
    originalLocation = window.location
    setMetaTag(null)
  })

  afterEach(() => {
    _resetAppBasePrefixCache()
    setMetaTag(null)
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: originalLocation,
    })
  })

  it('exposes the known top-level routes as a single source of truth', () => {
    expect(KNOWN_ROUTES).toEqual(['viewer', 'configuration', 'normalizer', 'watchers', 'account', 'about', 'login'])
  })

  it('returns "" at root with no meta tag', () => {
    setLocationPathname('/')
    expect(getAppBasePrefix()).toBe('')
  })

  it('returns "" when at a known route at root (route-suffix stripped)', () => {
    setLocationPathname('/viewer')
    expect(getAppBasePrefix()).toBe('')
  })

  it('auto-detects "/feeds" when loaded at the alias index "/feeds/"', () => {
    setLocationPathname('/feeds/')
    expect(getAppBasePrefix()).toBe('/feeds')
  })

  it('auto-detects "/feeds" on deep-route reload "/feeds/configuration"', () => {
    setLocationPathname('/feeds/configuration')
    expect(getAppBasePrefix()).toBe('/feeds')
  })

  it('auto-detects "/feeds" on deep-route reload "/feeds/viewer"', () => {
    setLocationPathname('/feeds/viewer')
    expect(getAppBasePrefix()).toBe('/feeds')
  })

  it('auto-detects "/feeds" on deep-route reload "/feeds/about"', () => {
    setLocationPathname('/feeds/about')
    expect(getAppBasePrefix()).toBe('/feeds')
  })

  it('auto-detects "/feeds" on deep-route reload "/feeds/normalizer"', () => {
    setLocationPathname('/feeds/normalizer')
    expect(getAppBasePrefix()).toBe('/feeds')
  })

  it('auto-detects "/feeds" on deep-route reload "/feeds/login"', () => {
    setLocationPathname('/feeds/login')
    expect(getAppBasePrefix()).toBe('/feeds')
  })

  it('handles multi-segment aliases such as "/proxy/feeds/"', () => {
    setLocationPathname('/proxy/feeds/')
    expect(getAppBasePrefix()).toBe('/proxy/feeds')
  })

  it('explicit meta tag overrides auto-detect (forcing override)', () => {
    setLocationPathname('/feeds/viewer')
    setMetaTag('/x')
    expect(getAppBasePrefix()).toBe('/x')
  })

  it('explicit meta tag is normalised (trailing slash stripped)', () => {
    setLocationPathname('/')
    setMetaTag('/x/')
    expect(getAppBasePrefix()).toBe('/x')
  })

  it('empty meta content falls through to auto-detect', () => {
    setLocationPathname('/feeds/')
    setMetaTag('')
    expect(getAppBasePrefix()).toBe('/feeds')
  })

  it('memoises the result across repeated calls', () => {
    setLocationPathname('/feeds/')
    const first = getAppBasePrefix()
    setLocationPathname('/changed/')
    // Cache should still hold the first value until reset.
    const second = getAppBasePrefix()
    expect(first).toBe('/feeds')
    expect(second).toBe('/feeds')
  })

  it('documented limitation: unknown final segment yields a sub-mount basename', () => {
    setLocationPathname('/feeds/garbage')
    // No route match → trailing-slash strip leaves the whole path. This
    // is stable (Sidebar links stay consistent) but suboptimal; operators
    // with such URL shapes should set app_base_prefix explicitly.
    expect(getAppBasePrefix()).toBe('/feeds/garbage')
  })
})
