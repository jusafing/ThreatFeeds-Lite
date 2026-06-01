/**
 * Render an arbitrary cell value as a short, safe string for table display.
 * - null/undefined  -> ''
 * - object/array    -> JSON.stringify, truncated to 80 chars with '…'
 * - everything else -> String(value)
 */
export function cellToString(v: unknown, maxLen: number = 80): string {
  if (v === null || v === undefined) return ''
  if (typeof v === 'object') {
    try {
      const s = JSON.stringify(v)
      return s.length > maxLen ? s.slice(0, maxLen - 1) + '…' : s
    } catch {
      return String(v)
    }
  }
  return String(v)
}
