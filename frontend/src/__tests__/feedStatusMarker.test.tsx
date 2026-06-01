/**
 * Tests for the per-feed status marker (issue #1).
 *
 * FeedStatusMarker is a pure presentational mapping from a FeedStatus to a small
 * icon with an accessible label. These tests pin that mapping:
 *
 *   - idle    → renders nothing
 *   - pulling → status role + "Pulling" label (spinner)
 *   - ready   → "Ready" label (green check)
 *   - error   → "Error" label / "Last pull failed" title (red alert)
 */
import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'

import FeedStatusMarker from '../components/FeedStatusMarker'

describe('FeedStatusMarker', () => {
  it('renders nothing when idle', () => {
    const { container } = render(<FeedStatusMarker status="idle" />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders a status spinner when pulling', () => {
    render(<FeedStatusMarker status="pulling" />)
    const marker = screen.getByLabelText('Pulling')
    expect(marker).toBeInTheDocument()
    expect(marker).toHaveAttribute('role', 'status')
    expect(marker).toHaveAttribute('title', 'Pulling…')
  })

  it('renders a ready marker when ready', () => {
    render(<FeedStatusMarker status="ready" />)
    const marker = screen.getByLabelText('Ready')
    expect(marker).toBeInTheDocument()
    expect(marker).toHaveAttribute('title', 'Ready')
  })

  it('renders an error marker when error', () => {
    render(<FeedStatusMarker status="error" />)
    const marker = screen.getByLabelText('Error')
    expect(marker).toBeInTheDocument()
    expect(marker).toHaveAttribute('title', 'Last pull failed')
  })

  it('forwards an extra className onto the marker', () => {
    render(<FeedStatusMarker status="ready" className="shrink-0" />)
    expect(screen.getByLabelText('Ready')).toHaveClass('shrink-0')
  })
})
