/**
 * Run History modal (prompts-039).
 *
 * Read-only popup listing recent normalizer runs — manual ("Run Now"),
 * scheduled, and on-demand re-apply — newest first. Surfaced from both the
 * Normalizer Settings panel and the Smart Mappings active card.
 *
 * Columns: time · proposal name · feeds · result. Proposal name + feeds are
 * filled only for smart-mode applies; auto/manual rows show the mode and a dash.
 */
import { useQuery } from '@tanstack/react-query'
import { X, RefreshCw } from 'lucide-react'
import { api } from '../api/client'
import type { RunHistoryRow } from '../api/client'

interface Props {
  onClose: () => void
}

function fmtTime(iso: string): string {
  if (!iso) return ''
  return iso.replace('T', ' ').slice(0, 19)
}

const TRIGGER_LABEL: Record<RunHistoryRow['trigger'], string> = {
  manual: 'Run Now',
  schedule: 'Scheduled',
  reapply: 'Re-apply',
}

function resultText(r: RunHistoryRow): string {
  if (r.status !== 'ok') return r.status
  return `${r.processed} processed · ${r.inserted} inserted${
    r.errors ? ` · ${r.errors} errors` : ''
  }`
}

export default function RunHistoryModal({ onClose }: Props) {
  const { data: runs = [], isFetching, refetch } = useQuery({
    queryKey: ['normalizer-runs'],
    queryFn: () => api.getRunHistory(200),
    refetchInterval: 15_000,
  })

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70"
      role="dialog"
      aria-modal="true"
      aria-labelledby="run-history-title"
    >
      <div className="card w-full max-w-3xl mx-4 max-h-[90vh] overflow-y-auto">
        <div className="flex items-start justify-between mb-4">
          <div>
            <h2 id="run-history-title" className="text-base font-semibold text-gray-100">
              Run History
            </h2>
            <p className="text-xs text-gray-500 mt-1">
              Recent normalizer runs (manual, scheduled, and re-apply), newest first.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => refetch()}
              className="text-gray-500 hover:text-gray-300"
              aria-label="Refresh"
              title="Refresh"
            >
              <RefreshCw className={`w-4 h-4 ${isFetching ? 'animate-spin' : ''}`} />
            </button>
            <button
              type="button"
              onClick={onClose}
              className="text-gray-500 hover:text-gray-300"
              aria-label="Close"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        <div className="overflow-x-auto rounded-lg border border-gray-800">
          <table className="w-full text-sm text-left text-gray-300">
            <thead className="text-xs uppercase text-gray-500 bg-gray-900 border-b border-gray-800">
              <tr>
                <th className="px-4 py-3 font-medium whitespace-nowrap">Time</th>
                <th className="px-4 py-3 font-medium whitespace-nowrap">Trigger</th>
                <th className="px-4 py-3 font-medium whitespace-nowrap">Proposal</th>
                <th className="px-4 py-3 font-medium whitespace-nowrap">Feeds</th>
                <th className="px-4 py-3 font-medium">Result</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800">
              {runs.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-4 py-8 text-center text-gray-600 italic">
                    No runs recorded yet.
                  </td>
                </tr>
              ) : (
                runs.map((r) => (
                  <tr key={r.id} className="hover:bg-gray-800/40 transition-colors">
                    <td className="px-4 py-2.5 text-xs text-gray-400 whitespace-nowrap">
                      {fmtTime(r.started_at)}
                    </td>
                    <td className="px-4 py-2.5 text-xs text-gray-400 whitespace-nowrap">
                      {TRIGGER_LABEL[r.trigger] ?? r.trigger}
                      <span className="text-gray-600"> · {r.mode ?? '—'}</span>
                    </td>
                    <td className="px-4 py-2.5 text-xs text-gray-400 max-w-[180px] truncate">
                      {r.proposal_name ?? '—'}
                    </td>
                    <td className="px-4 py-2.5 text-xs text-gray-400 max-w-[200px] truncate">
                      {r.sources.length > 0 ? r.sources.join(', ') : '—'}
                    </td>
                    <td className="px-4 py-2.5 text-xs text-gray-400">
                      <span className={r.status === 'ok' ? '' : 'text-amber-400'}>
                        {resultText(r)}
                      </span>
                      {r.warning && (
                        <span className="block text-amber-400/80 mt-0.5">{r.warning}</span>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
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
