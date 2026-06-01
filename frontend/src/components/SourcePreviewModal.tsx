/**
 * Modal that shows a sample of normalised entries fetched from a pull source URL.
 * The user can Confirm (persist the source + ingest the entries) or Cancel.
 */
import { useEffect, useMemo, useState } from 'react'
import { X, Check } from 'lucide-react'
import { PreviewResponse } from '../api/client'
import { clsx } from 'clsx'
import { cellToString } from '../utils/cell'

const HIDDEN_KEYS = new Set(['extra', 'dedup_key'])

interface Props {
  preview: PreviewResponse
  onConfirm: () => void
  onCancel: () => void
  confirming?: boolean
  error?: string | null
}

export default function SourcePreviewModal({ preview, onConfirm, onCancel, confirming, error }: Props) {
  const [secondsLeft, setSecondsLeft] = useState(preview.expires_in_seconds)

  useEffect(() => {
    const start = Date.now()
    const t = setInterval(() => {
      const remaining = Math.max(0, preview.expires_in_seconds - Math.floor((Date.now() - start) / 1000))
      setSecondsLeft(remaining)
      if (remaining <= 0) clearInterval(t)
    }, 1000)
    return () => clearInterval(t)
  }, [preview.expires_in_seconds])

  // Build column list from the union of keys observed in the sample
  const columns = useMemo(() => {
    const seen = new Set<string>()
    for (const row of preview.sample) {
      for (const k of Object.keys(row ?? {})) {
        if (!HIDDEN_KEYS.has(k)) seen.add(k)
      }
    }
    // Pin a few important columns first if present
    const preferred = ['source', 'indicator', 'title', 'severity', 'published_at']
    const present = preferred.filter(c => seen.has(c))
    const rest = Array.from(seen).filter(k => !present.includes(k)).sort()
    return [...present, ...rest]
  }, [preview.sample])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
      <div className="w-full max-w-5xl rounded-lg border border-gray-700 bg-gray-900 shadow-2xl flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-gray-700">
          <div>
            <h3 className="text-sm font-semibold text-gray-100">
              Preview source: <span className="font-mono text-brand-300">{preview.source_name}</span>
            </h3>
            <p className="text-xs text-gray-500 mt-0.5">
              format <span className="font-mono">{preview.format}</span> · {preview.total} entries fetched · showing first {preview.sample.length}
            </p>
          </div>
          <div className="flex items-center gap-3">
            <span className={clsx(
              'text-xs font-mono',
              secondsLeft < 30 ? 'text-amber-400' : 'text-gray-500',
            )}>
              expires in {Math.floor(secondsLeft / 60)}:{String(secondsLeft % 60).padStart(2, '0')}
            </span>
            <button className="btn-ghost p-1" onClick={onCancel} title="Cancel">
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-auto p-4">
          {preview.sample.length === 0 ? (
            <p className="text-sm text-gray-500 text-center py-8">
              No entries were extracted from this source.
            </p>
          ) : (
            <div className="overflow-auto rounded-lg border border-gray-800">
              <table className="w-full min-w-max">
                <thead>
                  <tr>
                    {columns.map(c => (
                      <th key={c} className="table-header capitalize">{c.replace(/_/g, ' ')}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {preview.sample.map((row, i) => (
                    <tr key={i} className="hover:bg-gray-800/50 transition-colors">
                      {columns.map(c => {
                        const raw = (row as Record<string, unknown>)[c]
                        const text = cellToString(raw)
                        return (
                          <td key={c} className="table-cell">
                            <span title={text}>
                              {text === '' ? <span className="text-gray-600">—</span> : text}
                            </span>
                          </td>
                        )
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {error && <p className="text-xs text-red-400 mt-3">{error}</p>}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 p-4 border-t border-gray-700">
          <button className="btn-ghost" onClick={onCancel} disabled={confirming}>
            <X className="w-3.5 h-3.5" /> Cancel
          </button>
          <button
            className="btn-primary"
            onClick={onConfirm}
            disabled={confirming || secondsLeft <= 0 || preview.sample.length === 0}
          >
            <Check className="w-3.5 h-3.5" />
            {confirming ? 'Confirming...' : `Confirm & Ingest (${preview.total})`}
          </button>
        </div>
      </div>
    </div>
  )
}
