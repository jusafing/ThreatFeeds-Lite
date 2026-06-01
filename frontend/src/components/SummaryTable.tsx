import { useQuery } from '@tanstack/react-query'
import { api, SummaryItem, RemoteJsonSourceDef, ActiveJobInfo } from '../api/client'
import { Database, TrendingUp, RefreshCw, Upload, Loader2 } from 'lucide-react'
import { relativeTime, absoluteTime } from '../utils/time'

type SourceKind = 'recurrent' | 'static'

function useSourceKindMap(): Map<string, SourceKind> {
  const { data: apiPull = [] }        = useQuery({ queryKey: ['api-pull'],          queryFn: api.getApiPull,        staleTime: 30_000 })
  const { data: rssPull = [] }        = useQuery({ queryKey: ['rss-pull'],          queryFn: api.getRssPull,        staleTime: 30_000 })
  const { data: remoteJson = [] }     = useQuery({ queryKey: ['remote-json-pull'],  queryFn: api.getRemoteJsonPull, staleTime: 30_000 })

  const map = new Map<string, SourceKind>()
  for (const s of apiPull)    map.set(s.name, 'recurrent')
  for (const s of rssPull)    map.set(s.name, 'recurrent')
  for (const s of (remoteJson as RemoteJsonSourceDef[])) {
    map.set(s.name, s.continuous ? 'recurrent' : 'static')
  }
  return map
}

function KindIcon({ kind }: { kind: SourceKind | undefined }) {
  if (kind === 'recurrent') return <span title="Scheduled pull"><RefreshCw className="w-3 h-3 text-brand-400 shrink-0" /></span>
  if (kind === 'static')    return <span title="Manual / one-shot"><Upload className="w-3 h-3 text-gray-400 shrink-0" /></span>
  return null
}

function ActiveBadge({ jobs }: { jobs: ActiveJobInfo[] }) {
  if (!jobs?.length) return null
  const j = jobs[0]
  const pct = j.total > 0 ? Math.min(100, Math.floor((j.processed / j.total) * 100)) : null
  const label = pct !== null ? `${j.step} ${pct}%` : j.step
  const extra = jobs.length > 1 ? ` (+${jobs.length - 1})` : ''
  return (
    <span
      className="inline-flex items-center gap-1 ml-2 px-1.5 py-0.5 rounded text-[10px] font-medium bg-brand-500/20 text-brand-300 border border-brand-500/40"
      title={`Active job: ${j.kind} — ${j.step}${pct !== null ? ` (${j.processed}/${j.total})` : ''}`}
    >
      <Loader2 className="w-2.5 h-2.5 animate-spin" />
      {label}{extra}
    </span>
  )
}

export default function SummaryTable() {
  const { data, isLoading, error } = useQuery<SummaryItem[]>({
    queryKey: ['summary', { active: true }],
    queryFn: () => api.getSummary({ includeActive: true }),
    refetchInterval: (q) => {
      const rows = q.state.data ?? []
      const anyActive = rows.some(r => (r.active_jobs?.length ?? 0) > 0)
      return anyActive ? 2_000 : 30_000
    },
  })

  const kindMap = useSourceKindMap()

  if (isLoading) return <div className="text-gray-500 text-sm py-4">Loading summary...</div>
  if (error)     return <div className="text-red-400 text-sm py-4">Failed to load summary.</div>

  const rows  = (data ?? []).filter(r => r.source !== '__total__')
  const total = (data ?? []).find(r => r.source === '__total__')

  return (
    <div className="card">
      <div className="flex items-center gap-2 mb-4">
        <Database className="w-4 h-4 text-brand-400" />
        <h2 className="text-sm font-semibold text-gray-200">Source Summary</h2>
      </div>

      {rows.length === 0 ? (
        <p className="text-sm text-gray-500">No data ingested yet.</p>
      ) : (
        <table className="w-full text-base">
          <thead>
            <tr>
              <th className="table-header rounded-tl-lg py-3">Source</th>
              <th className="table-header text-right py-3">Entries</th>
              <th className="table-header text-right py-3">Δ Last</th>
              <th className="table-header text-right rounded-tr-lg py-3">Last Ingest</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(row => {
              const delta = row.last_inserted ?? null
              const errored = row.last_job_state === 'error'
              return (
                <tr key={row.source} className="hover:bg-gray-800/40 transition-colors">
                  <td className="table-cell py-3">
                    <span className="flex items-center gap-1.5 font-mono text-sm text-gray-300">
                      <KindIcon kind={kindMap.get(row.source)} />
                      {row.source}
                      <ActiveBadge jobs={row.active_jobs ?? []} />
                    </span>
                  </td>
                  <td className="table-cell text-right tabular-nums py-3">{row.count.toLocaleString()}</td>
                  <td className="table-cell text-right tabular-nums py-3">
                    {delta === null || delta === undefined ? (
                      <span className="text-gray-600">—</span>
                    ) : (
                      <span className={delta > 0 ? 'text-emerald-400' : 'text-gray-500'}>
                        {delta > 0 ? `+${delta.toLocaleString()}` : '0'}
                      </span>
                    )}
                  </td>
                  <td
                    className="table-cell text-right text-sm text-gray-400 py-3"
                    title={absoluteTime(row.last_ingested_at)}
                  >
                    {errored && <span className="text-red-400 mr-1" title="last ingest errored">!</span>}
                    {relativeTime(row.last_ingested_at)}
                  </td>
                </tr>
              )
            })}
          </tbody>
          {total && (
            <tfoot>
              <tr className="border-t border-gray-700">
                <td className="px-3 py-2 text-sm font-semibold text-gray-200 flex items-center gap-1.5">
                  <TrendingUp className="w-3.5 h-3.5 text-brand-400" /> Total
                </td>
                <td className="px-3 py-2 text-sm font-bold text-brand-400 text-right tabular-nums">
                  {total.count.toLocaleString()}
                </td>
                <td />
                <td />
              </tr>
            </tfoot>
          )}
        </table>
      )}
    </div>
  )
}
