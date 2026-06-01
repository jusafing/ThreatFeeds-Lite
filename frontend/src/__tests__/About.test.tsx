/**
 * About page tests (prompts-051).
 *
 * Verifies the rebrand display name, the Credits row (Code Dev Team), and the
 * License card that points to the LICENSE and THIRD-PARTY-NOTICES.md files.
 * The API client is mocked so the page renders in isolation; the health query
 * is irrelevant to the assertions below.
 */
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, it, expect, vi } from 'vitest'

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...actual,
    api: { health: vi.fn().mockResolvedValue({ status: 'ok', version: '0.1.0' }) },
  }
})

import About from '../pages/About'

function renderAbout() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <About />
    </QueryClientProvider>,
  )
}

describe('About page (prompts-051)', () => {
  it('shows the ThreatFeeds Lite brand name', () => {
    renderAbout()
    expect(screen.getByText('ThreatFeeds Lite')).toBeInTheDocument()
  })

  it('credits the code dev team and author', () => {
    renderAbout()
    expect(screen.getByText('Code Dev Team')).toBeInTheDocument()
    expect(screen.getByText(/Javier Santillan/)).toBeInTheDocument()
    const mail = screen.getByRole('link', { name: 'jusafing@jusanet.org' })
    expect(mail).toHaveAttribute('href', 'mailto:jusafing@jusanet.org')
  })

  it('shows the License card referencing the license files', () => {
    renderAbout()
    expect(screen.getByRole('heading', { name: 'License' })).toBeInTheDocument()
    expect(screen.getByText(/Apache License 2.0/)).toBeInTheDocument()
    expect(screen.getByText('LICENSE')).toBeInTheDocument()
    expect(screen.getByText('THIRD-PARTY-NOTICES.md')).toBeInTheDocument()
  })
})
