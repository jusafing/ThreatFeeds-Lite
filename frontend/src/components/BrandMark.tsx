/**
 * BrandMark (prompts-048).
 *
 * The default app logo glyph: a security shield (threat intel) enclosing
 * RSS-style feed/signal waves (feed tracking). Rendered as inline SVG so it
 * scales crisply with `size`, needs no network request, and is reverse-proxy
 * prefix-safe. Strokes/fills use `currentColor`, so the parent controls the
 * colour (BrandLogo paints it white on the brand-blue badge).
 *
 * The same artwork is duplicated as a self-contained, hard-coloured favicon at
 * `frontend/public/favicon.svg`; keep the two path definitions in sync.
 */
interface Props {
  /** Square pixel size of the glyph. */
  size?: number
  className?: string
}

export default function BrandMark({ size = 24, className }: Props) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      role="img"
      aria-label="ThreatFeeds Lite"
    >
      <title>ThreatFeeds Lite</title>
      {/* Shield — threat intel */}
      <path d="M12 2.5 20 5.2V11c0 5.5-3.6 9-8 10.5C7.6 20 4 16.5 4 11V5.2Z" />
      {/* Feed waves — feed tracking */}
      <path d="M8 9.5A6.5 6.5 0 0 1 14.5 16" />
      <path d="M8 12.5A3.5 3.5 0 0 1 11.5 16" />
      <circle cx="8" cy="16" r="1.05" fill="currentColor" stroke="none" />
    </svg>
  )
}
