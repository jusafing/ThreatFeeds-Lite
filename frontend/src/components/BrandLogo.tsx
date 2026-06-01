/**
 * Branding logo (prompts-045).
 *
 * Renders the operator-configured logo image when one exists, otherwise the
 * default brand mark (a shield + feed-waves glyph, see BrandMark). Two usage
 * modes:
 *   - hasLogo known (Sidebar, via the /api/app/logo-info query): pass it
 *     explicitly so we render the right element on first paint.
 *   - hasLogo unknown (Login, which is unauthenticated and cannot query
 *     logo-info): omit it; we optimistically try the image and fall back to
 *     the icon via onError. GET /api/app/logo is public, so this works for
 *     signed-out users.
 *
 * The backend serves the logo with Cache-Control: no-cache, so a replaced
 * image refreshes without any client-side cache-busting.
 */
import { useState, useEffect } from 'react'
import { clsx } from 'clsx'
import { logoSrc } from '../api/client'
import BrandMark from './BrandMark'

interface Props {
  hasLogo?: boolean
  /** Square pixel size of the badge. */
  size?: number
  className?: string
}

export default function BrandLogo({ hasLogo, size = 28, className }: Props) {
  const [failed, setFailed] = useState(false)

  // Reset the failure flag if the logo availability changes (e.g. a logo is
  // uploaded after an earlier 404).
  useEffect(() => {
    setFailed(false)
  }, [hasLogo])

  const showImage = hasLogo !== false && !failed

  if (showImage) {
    return (
      <img
        src={logoSrc()}
        alt="Logo"
        width={size}
        height={size}
        className={clsx('object-contain rounded-md', className)}
        onError={() => setFailed(true)}
      />
    )
  }

  return (
    <div
      className={clsx('flex items-center justify-center bg-brand-600 rounded-md', className)}
      style={{ width: size, height: size }}
    >
      <BrandMark className="text-white" size={size * 0.62} />
    </div>
  )
}
