import { Check, X } from 'lucide-react'
import { PreviewResponse } from '../api/client'
import { cellToString } from '../utils/cell'

interface Props {
  preview: PreviewResponse
  confirming: boolean
  onConfirm: () => void
  onCancel: () => void
}

/**
 * Inline verification panel for local file uploads.
 *
 * Renders a sample of the parsed entries from a preview response
 * and offers Confirm / Cancel actions.
 *
 * Used by LocalFeedTab. For remote pull sources we use the modal
 * variant in SourcePreviewModal.
 */
export default function LocalFeedPreviewPanel({
  preview,
  confirming,
  onConfirm,
  onCancel,
}: Props) {
  const sample = preview.sample ?? []

  // Union of keys across the sample (not just the first row), capped to 8.
  const colSet = new Set<string>()
  for (const row of sample) {
    for (const k of Object.keys(row)) colSet.add(k)
  }
  const cols = Array.from(colSet).slice(0, 8)

  return (
    <div className="rounded-lg border border-amber-700/30 bg-amber-900/10 p-3 space-y-3">
      <p className="text-xs font-semibold text-amber-300">
        Preview — {preview.total} entries detected ({preview.format.toUpperCase()})
      </p>

      {sample.length > 0 ? (
        <div className="overflow-x-auto max-h-48 rounded border border-gray-700">
          <table className="w-full text-xs text-gray-300">
            <thead className="bg-gray-800 sticky top-0">
              <tr>
                {cols.map(k => (
                  <th
                    key={k}
                    className="px-2 py-1 text-left font-medium text-gray-400 whitespace-nowrap"
                  >
                    {k}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800">
              {sample.map((row, i) => (
                <tr key={i}>
                  {cols.map(k => (
                    <td key={k} className="px-2 py-1 truncate max-w-[140px]">
                      {cellToString(row[k])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-xs text-gray-500">No sample rows extracted.</p>
      )}

      <div className="flex gap-2 justify-end">
        <button
          className="btn-ghost text-xs"
          onClick={onCancel}
          disabled={confirming}
        >
          <X className="w-3.5 h-3.5" /> Cancel
        </button>
        <button
          className="btn-primary text-xs"
          disabled={confirming}
          onClick={onConfirm}
        >
          <Check className="w-3.5 h-3.5" />
          {confirming ? 'Ingesting…' : `Confirm & Ingest ${preview.total} entries`}
        </button>
      </div>
    </div>
  )
}
