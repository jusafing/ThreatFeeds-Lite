/**
 * Tests for uploadMultipartWithProgress (prompts-021A item 2).
 *
 * The helper is the foundation of the local-feed upload progress UI:
 * it wraps XMLHttpRequest because the Fetch API does not expose
 * request-body progress. These tests use a hand-rolled XHR stub
 * (faster and more deterministic than jsdom's XHR) to verify both
 * the progress-event plumbing and the success/failure paths.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { uploadMultipartWithProgress, type UploadProgress } from '../api/client'

type ProgressListener = (ev: {
  lengthComputable?: boolean
  loaded?: number
  total?: number
}) => void

class FakeUpload {
  listeners = new Map<string, ProgressListener>()
  addEventListener(type: string, fn: ProgressListener) {
    this.listeners.set(type, fn)
  }
}

class FakeXHR {
  static instances: FakeXHR[] = []
  upload = new FakeUpload()
  listeners = new Map<string, () => void>()
  status = 0
  statusText = ''
  responseText = ''
  method = ''
  url = ''

  open(method: string, url: string) {
    this.method = method
    this.url = url
  }
  addEventListener(type: string, fn: () => void) {
    this.listeners.set(type, fn)
  }
  send(_body: unknown) {
    FakeXHR.instances.push(this)
  }
  fireProgress(loaded: number, total: number, lengthComputable = true) {
    this.upload.listeners.get('progress')?.({ lengthComputable, loaded, total })
  }
  fireLoad(status: number, body: string, statusText = '') {
    this.status = status
    this.statusText = statusText
    this.responseText = body
    this.listeners.get('load')?.()
  }
}

describe('uploadMultipartWithProgress', () => {
  beforeEach(() => {
    FakeXHR.instances = []
    vi.stubGlobal('XMLHttpRequest', FakeXHR as unknown as typeof XMLHttpRequest)
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('reports incremental progress and resolves with parsed JSON on 2xx', async () => {
    const events: UploadProgress[] = []
    const form = new FormData()
    form.append('file', new Blob(['hello world']), 'feed.json')

    const promise = uploadMultipartWithProgress<{ preview_id: string }>(
      '/ingest/preview/local/test',
      form,
      p => events.push(p),
    )

    await Promise.resolve()
    const xhr = FakeXHR.instances[0]
    expect(xhr).toBeTruthy()

    xhr.fireProgress(50, 100)
    xhr.fireProgress(100, 100)
    xhr.fireLoad(200, JSON.stringify({ preview_id: 'abc' }), 'OK')

    const result = await promise
    expect(result).toEqual({ preview_id: 'abc' })
    expect(events).toEqual([
      { loaded: 50, total: 100, pct: 50 },
      { loaded: 100, total: 100, pct: 100 },
    ])
  })

  it('rejects with status and body on non-2xx response', async () => {
    const form = new FormData()
    form.append('file', new Blob(['payload']), 'big.json')

    const promise = uploadMultipartWithProgress(
      '/ingest/preview/local/test',
      form,
    )

    await Promise.resolve()
    const xhr = FakeXHR.instances[0]
    xhr.fireLoad(413, 'too large', 'Payload Too Large')

    await expect(promise).rejects.toThrow(/413/)
  })
})
