/**
 * Per-feed live status marker (issue #1).
 *
 * Renders a small icon reflecting the current state of a feed's background
 * pull, driven by `useFeedStatus`:
 *
 * - pulling → spinning loader ("Pulling…")
 * - ready   → green check ("Ready")
 * - error   → red alert ("Last pull failed")
 * - idle    → nothing (no pull has run / source not yet enabled)
 */
import { Loader2, CheckCircle2, AlertCircle } from 'lucide-react'
import { clsx } from 'clsx'
import type { FeedStatus } from '../hooks/useFeedStatus'

interface Props {
  status: FeedStatus
  className?: string
}

export default function FeedStatusMarker({ status, className }: Props) {
  if (status === 'idle') return null

  if (status === 'pulling') {
    return (
      <span
        className={clsx('inline-flex items-center text-brand-400', className)}
        title="Pulling…"
        aria-label="Pulling"
        role="status"
      >
        <Loader2 className="w-3.5 h-3.5 animate-spin" />
      </span>
    )
  }

  if (status === 'ready') {
    return (
      <span
        className={clsx('inline-flex items-center text-green-400', className)}
        title="Ready"
        aria-label="Ready"
      >
        <CheckCircle2 className="w-3.5 h-3.5" />
      </span>
    )
  }

  return (
    <span
      className={clsx('inline-flex items-center text-red-400', className)}
      title="Last pull failed"
      aria-label="Error"
    >
      <AlertCircle className="w-3.5 h-3.5" />
    </span>
  )
}
