/**
 * Read-only detail popup for a failed watcher delivery (issue_local_007
 * review_01).
 *
 * Surfaced from the Watchers Summary error card and from the Activity tab's
 * Delivery column next to "failed" rows so an operator can see exactly why a
 * receiver (e.g. Discord) rejected the payload — the HTTP status, reason,
 * response headers, response body, and the target URL — without leaving the
 * page. Nothing here is editable.
 */
import { X } from 'lucide-react'
import type { DeliveryDetail } from '../api/client'

interface Props {
  detail: DeliveryDetail
  title?: string
  onClose: () => void
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs font-medium text-gray-400 mb-1">{label}</div>
      <pre className="text-[11px] font-mono text-gray-300 bg-gray-950/70 border border-gray-800 rounded px-2.5 py-2 whitespace-pre-wrap break-words max-h-60 overflow-y-auto">
        {value || <span className="text-gray-600 italic">(empty)</span>}
      </pre>
    </div>
  )
}

export default function WatcherDeliveryErrorModal({ detail, title, onClose }: Props) {
  const statusLine = [
    detail.status != null ? String(detail.status) : null,
    detail.reason || null,
  ]
    .filter(Boolean)
    .join(' ')
  const headers = detail.headers
    ? Object.entries(detail.headers)
        .map(([k, v]) => `${k}: ${v}`)
        .join('\n')
    : ''

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70"
      role="dialog"
      aria-modal="true"
      aria-labelledby="watcher-delivery-error-title"
    >
      <div className="card w-full max-w-2xl mx-4 max-h-[90vh] overflow-y-auto">
        <div className="flex items-start justify-between mb-4">
          <div>
            <h2
              id="watcher-delivery-error-title"
              className="text-base font-semibold text-gray-100"
            >
              {title ?? 'Delivery error'}
            </h2>
            <p className="text-xs text-gray-500 mt-1">
              The last delivery attempt failed. Read-only.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-gray-500 hover:text-gray-300"
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="space-y-4">
          <Field label="Status" value={statusLine || detail.message || '—'} />
          <Field label="Target URL" value={detail.url ?? ''} />
          {detail.error_type && <Field label="Error type" value={detail.error_type} />}
          {detail.message && statusLine && (
            <Field label="Message" value={detail.message} />
          )}
          <Field label="Response headers" value={headers} />
          <Field label="Response body" value={detail.body ?? ''} />
        </div>

        <div className="flex items-center justify-end mt-5">
          <button type="button" onClick={onClose} className="btn-secondary text-sm">
            Close
          </button>
        </div>
      </div>
    </div>
  )
}
