import { CheckCircle2, AlertCircle, Loader2 } from 'lucide-react'
import { clsx } from 'clsx'
import { Job, JobStep } from '../api/client'

const STEPS: { id: JobStep; label: string }[] = [
  { id: 'fetching',    label: 'Fetch' },
  { id: 'parsing',     label: 'Parse' },
  { id: 'normalising', label: 'Normalise' },
  { id: 'inserting',   label: 'Insert' },
  { id: 'done',        label: 'Done' },
]

interface Props {
  job: Job
  compact?: boolean
}

/**
 * Visual progress indicator for a background ingest job.
 *
 * Shows the 5 ingestion phases and a progress bar with processed/total
 * for the insertion phase. Useful when the user uploads a large file
 * or kicks off a remote refresh — they can navigate away and come back.
 */
export default function JobProgressBar({ job, compact = false }: Props) {
  const isError    = job.state === 'error'
  const isDone     = job.state === 'done'
  const stepIdx    = STEPS.findIndex(s => s.id === job.step)
  const pct        = job.total > 0
    ? Math.min(100, Math.round((job.processed / job.total) * 100))
    : isDone ? 100 : 0

  return (
    <div className="rounded-lg border border-brand-800/40 bg-brand-900/10 p-3 space-y-2">
      <div className="flex items-center justify-between text-xs">
        <div className="flex items-center gap-2">
          {isError ? (
            <AlertCircle className="w-3.5 h-3.5 text-red-400" />
          ) : isDone ? (
            <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
          ) : (
            <Loader2 className="w-3.5 h-3.5 text-brand-300 animate-spin" />
          )}
          <span className={clsx(
            'font-mono uppercase tracking-wide',
            isError ? 'text-red-300' : isDone ? 'text-emerald-300' : 'text-brand-200',
          )}>
            {job.first_ingest ? 'first ingestion' : 'updates'}
            {!compact && ' · '}
            {!compact && (isError ? 'error' : isDone ? 'done' : job.step)}
          </span>
        </div>
        {job.total > 0 && !isError && (
          <span className="text-gray-400 tabular-nums">
            {job.processed.toLocaleString()} / {job.total.toLocaleString()}
          </span>
        )}
      </div>

      {/* Step pills */}
      {!compact && (
        <div className="flex items-center gap-1">
          {STEPS.map((s, i) => {
            const reached = isDone || i < stepIdx || (i === stepIdx && job.state === 'running')
            const active = !isDone && i === stepIdx && !isError
            return (
              <div
                key={s.id}
                title={s.label}
                className={clsx(
                  'h-1.5 flex-1 rounded-full transition-colors',
                  isError && reached  ? 'bg-red-500/60' :
                  active              ? 'bg-brand-400 animate-pulse' :
                  reached             ? 'bg-brand-500' :
                                        'bg-gray-700',
                )}
              />
            )
          })}
        </div>
      )}

      {/* Numeric progress bar */}
      {job.total > 0 && !isError && (
        <div className="h-1 rounded-full bg-gray-800 overflow-hidden">
          <div
            className={clsx(
              'h-full transition-all',
              isDone ? 'bg-emerald-500' : 'bg-brand-400',
            )}
            style={{ width: `${pct}%` }}
          />
        </div>
      )}

      {/* Counters when done */}
      {isDone && job.counters && (
        <div className="flex gap-3 text-[11px] text-gray-400 tabular-nums">
          <span>read <span className="text-gray-200">{job.counters.total_read ?? 0}</span></span>
          <span>inserted <span className="text-emerald-300">+{job.counters.inserted ?? 0}</span></span>
          <span>dup <span className="text-amber-300">{job.counters.duplicates ?? 0}</span></span>
          <span>skipped <span className="text-gray-300">{job.counters.discarded ?? 0}</span></span>
        </div>
      )}

      {isError && job.error_msg && (
        <p className="text-[11px] text-red-300 break-words">{job.error_msg}</p>
      )}
    </div>
  )
}
