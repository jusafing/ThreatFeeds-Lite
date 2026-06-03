/**
 * Resolves the application's effective base URL prefix.
 *
 * Three-tier precedence (prompts-020):
 *   1. EXPLICIT — the backend-injected <meta name="app-base-prefix" content="/x">.
 *      Used as a *forcing* override for environments where the operator wants
 *      to pin a specific prefix regardless of how the document was loaded.
 *   2. AUTO-DETECT — derived from window.location.pathname. Handles the
 *      zero-config reverse-proxy alias case: nginx mounts the app under
 *      /feeds/ → the operator sets nothing, the frontend infers /feeds at
 *      runtime and feeds it to React Router as basename.
 *   3. EMPTY — when neither yields a prefix (true root mount, or SSR).
 *
 * Auto-detection algorithm (strategy delta):
 *   - If window.location.pathname ends in /<route> or /<route>/ for any
 *     entry of KNOWN_ROUTES, the prefix is everything before that segment
 *     (the "route-suffix strip"). This handles the deep-route-reload case
 *     such as https://host/feeds/configuration → prefix "/feeds".
 *   - Otherwise the prefix is window.location.pathname with the trailing
 *     slash stripped (the "trailing-slash strip"). This handles the
 *     index-load case such as https://host/feeds/ → prefix "/feeds".
 *
 * Behaviour matrix:
 *   URL                              meta   detected   final
 *   https://host/                    —      ""         ""
 *   https://host/viewer              —      ""         ""    (route stripped)
 *   https://host/feeds/              —      "/feeds"   "/feeds"
 *   https://host/feeds/configuration —      "/feeds"   "/feeds" (route stripped)
 *   https://host/feeds/              "/x"   —          "/x"  (explicit wins)
 *
 * Documented limitation: a URL with an unknown final segment such as
 *   https://host/feeds/garbage
 * cannot be distinguished from a legitimate sub-mount; the auto-detect
 * yields basename "/feeds/garbage", which is stable (Sidebar links remain
 * consistent within that mount) but suboptimal. Operators with such URL
 * shapes should set app_base_prefix explicitly.
 *
 * The KNOWN_ROUTES constant is the single source of truth for the SPA's
 * top-level routes and is consumed both here (for detection) and by
 * src/App.tsx (for the Route table). Adding a new top-level route
 * therefore updates auto-detection in lockstep.
 *
 * The prefix is consumed by:
 *   - React Router's <BrowserRouter basename=...>
 *   - the API client BASE (relative "api" when empty, "<prefix>/api" otherwise)
 *   - the Configuration UI display of the push URL
 *
 * The result is memoised on first read; _resetAppBasePrefixCache() is
 * provided for tests that need to mutate window.location between cases.
 */

// NOTE: 'login' (prompts-045) is a top-level route rendered OUTSIDE the sidebar
// shell, but it is still listed here so reverse-proxy alias auto-detection
// recognises e.g. https://host/feeds/login → prefix "/feeds". App.tsx derives
// the in-shell SHELL_ROUTES by excluding 'login'.
export const KNOWN_ROUTES = ['viewer', 'configuration', 'normalizer', 'watchers', 'account', 'about', 'login'] as const

let cached: string | null = null

function _normalise(v: string): string {
  let out = v.trim()
  while (out.endsWith('/')) out = out.slice(0, -1)
  if (out && !out.startsWith('/')) out = '/' + out
  return out
}

function _readMetaTag(): string {
  if (typeof document === 'undefined') return ''
  const tag = document.querySelector('meta[name="app-base-prefix"]')
  const raw = tag?.getAttribute('content') ?? ''
  return _normalise(raw)
}

function _detectFromLocation(): string {
  if (typeof window === 'undefined' || !window.location) return ''
  const path = window.location.pathname || '/'
  // Route-suffix strip: try each known route.
  for (const r of KNOWN_ROUTES) {
    // Match "<anything>/<route>" or "<anything>/<route>/" at end of path.
    const re = new RegExp(`^(.*?)/${r}/?$`)
    const m = path.match(re)
    if (m) return _normalise(m[1])
  }
  // Trailing-slash strip fallback.
  return _normalise(path)
}

export function getAppBasePrefix(): string {
  if (cached !== null) return cached
  // 1. Explicit meta tag wins.
  const meta = _readMetaTag()
  if (meta) {
    cached = meta
    return cached
  }
  // 2. Auto-detect from window.location.
  cached = _detectFromLocation()
  return cached
}

/** Test-only escape hatch: clears the memoised value. */
export function _resetAppBasePrefixCache(): void {
  cached = null
}
