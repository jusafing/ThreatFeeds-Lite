/**
 * BrandLogo / BrandMark tests (prompts-048).
 *
 * Verifies the default brand mark fallback (shield + feed waves) and that an
 * operator-configured logo still overrides it. BrandLogo only builds a URL via
 * logoSrc(), so no API mocking is needed to render.
 */
import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'

import BrandLogo from '../components/BrandLogo'

describe('BrandLogo default brand mark (prompts-048)', () => {
  it('renders the default brand mark when no logo is configured', () => {
    render(<BrandLogo hasLogo={false} size={48} />)

    expect(screen.getByRole('img', { name: 'ThreatFeeds Lite' })).toBeInTheDocument()
    // No operator image element in the fallback state.
    expect(screen.queryByRole('img', { name: 'Logo' })).not.toBeInTheDocument()
  })

  it('renders the operator logo image when one is configured', () => {
    render(<BrandLogo hasLogo={true} size={28} />)

    const img = screen.getByRole('img', { name: 'Logo' })
    expect(img).toBeInTheDocument()
    expect(img).toHaveAttribute('src', expect.stringContaining('/app/logo'))
    expect(screen.queryByRole('img', { name: 'ThreatFeeds Lite' })).not.toBeInTheDocument()
  })
})
