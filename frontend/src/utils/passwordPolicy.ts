/**
 * Password policy helpers (prompts-046).
 *
 * Mirrors the backend rule in backend/api/routes_auth.py (_validate_password):
 *   - at least `min_length` characters,
 *   - at most `max_bytes` UTF-8 bytes (bcrypt's 72-byte ceiling),
 *   - at least `required_classes` of the four character classes
 *     {lowercase, uppercase, number, symbol}.
 *
 * The backend remains the source of truth; these checks are a UX convenience so
 * the user sees an inline error before submitting. The active policy is fetched
 * from GET /api/auth/status and threaded through the auth context.
 */
import type { PasswordPolicy } from '../api/client'

/** Fallback used before /api/auth/status resolves, or when auth is disabled. */
export const DEFAULT_PASSWORD_POLICY: PasswordPolicy = {
  min_length: 8,
  required_classes: 3,
  max_bytes: 72,
}

/** UTF-8 byte length of a string (matches Python len(s.encode('utf-8'))). */
export function passwordByteLength(value: string): number {
  return new TextEncoder().encode(value).length
}

/** Number of distinct character classes present in `value` (0-4). */
export function passwordClassCount(value: string): number {
  let n = 0
  if (/[a-z]/.test(value)) n += 1
  if (/[A-Z]/.test(value)) n += 1
  if (/[0-9]/.test(value)) n += 1
  if (/[^A-Za-z0-9]/.test(value)) n += 1
  return n
}

/**
 * Validate `value` against `policy`. Returns a human-readable error string when
 * invalid, or null when the password satisfies every rule.
 */
export function validatePassword(
  value: string,
  policy: PasswordPolicy = DEFAULT_PASSWORD_POLICY,
): string | null {
  if (value.length < policy.min_length) {
    return `Password must be at least ${policy.min_length} characters.`
  }
  if (passwordByteLength(value) > policy.max_bytes) {
    return `Password must be at most ${policy.max_bytes} bytes.`
  }
  if (passwordClassCount(value) < policy.required_classes) {
    return (
      `Password must include at least ${policy.required_classes} of: ` +
      'lowercase, uppercase, number, symbol.'
    )
  }
  return null
}

/** Short human description of the policy for helper text. */
export function describePasswordPolicy(
  policy: PasswordPolicy = DEFAULT_PASSWORD_POLICY,
): string {
  return (
    `At least ${policy.min_length} characters and ${policy.required_classes} ` +
    'of: lowercase, uppercase, number, symbol.'
  )
}
