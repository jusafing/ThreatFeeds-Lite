/**
 * Unit tests for the password policy helpers (prompts-046).
 * Mirrors the backend rule in backend/api/routes_auth.py (_validate_password).
 */
import { describe, it, expect } from 'vitest'
import {
  DEFAULT_PASSWORD_POLICY,
  passwordByteLength,
  passwordClassCount,
  validatePassword,
  describePasswordPolicy,
} from '../utils/passwordPolicy'

describe('passwordClassCount', () => {
  it('counts each distinct character class once', () => {
    expect(passwordClassCount('lowercase')).toBe(1)
    expect(passwordClassCount('lower1')).toBe(2)
    expect(passwordClassCount('Lower1')).toBe(3)
    expect(passwordClassCount('Lower1!')).toBe(4)
    expect(passwordClassCount('')).toBe(0)
  })
})

describe('passwordByteLength', () => {
  it('returns UTF-8 byte length (multibyte aware)', () => {
    expect(passwordByteLength('abc')).toBe(3)
    // '€' is three UTF-8 bytes.
    expect(passwordByteLength('€')).toBe(3)
  })
})

describe('validatePassword (default policy: len>=8, 3-of-4 classes)', () => {
  it('accepts a password meeting length and class rules', () => {
    expect(validatePassword('Adminpass1')).toBeNull()
  })

  it('rejects a too-short password', () => {
    expect(validatePassword('Ab1')).toMatch(/at least 8 characters/)
  })

  it('rejects a password with fewer than 3 classes', () => {
    expect(validatePassword('lowercase12')).toMatch(/at least 3 of/)
  })

  it('rejects a password exceeding the byte ceiling', () => {
    // 73 ASCII bytes, satisfies classes but breaks the 72-byte cap.
    const long = 'Aa1' + 'x'.repeat(70)
    expect(validatePassword(long)).toMatch(/at most 72 bytes/)
  })

  it('honours a custom policy', () => {
    const policy = { min_length: 12, required_classes: 4, max_bytes: 72 }
    expect(validatePassword('Adminpass1', policy)).toMatch(/at least 12 characters/)
    expect(validatePassword('Adminpassword1', policy)).toMatch(/at least 4 of/)
    expect(validatePassword('Adminpass1!!', policy)).toBeNull()
  })
})

describe('describePasswordPolicy', () => {
  it('summarises the default policy', () => {
    expect(describePasswordPolicy()).toContain('At least 8 characters')
    expect(describePasswordPolicy()).toContain('3 of')
  })

  it('exposes sane defaults', () => {
    expect(DEFAULT_PASSWORD_POLICY).toEqual({
      min_length: 8,
      required_classes: 3,
      max_bytes: 72,
    })
  })
})
