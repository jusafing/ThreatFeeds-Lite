import { useEffect, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Save } from 'lucide-react'
import clsx from 'clsx'
import { api, type ThreatIntelToggle } from '../api/client'
import Toggle from './Toggle'
import FeedStatusMarker from './FeedStatusMarker'
import { useFeedStatus } from '../hooks/useFeedStatus'

interface RowState {
  enabled: boolean
  continuous: boolean
  interval_minutes: number
}

/**
 * prompts-042: "Open Threat Intel / Vulnerability Sources" catalogue card.
 *
 * Lists curated default feeds (config/default-sources.yaml). Each row has an
 * enable toggle; an enabled row reveals a Continuous-pull toggle + interval.
 * Changes are batched into local draft state and applied with a single Save,
 * which writes real entries into sources.yaml and reloads the scheduler.
 */
export default function ThreatIntelCatalog() {
  const qc = useQueryClient()
  const { data: catalog, isLoading } = useQuery({
    queryKey: ['threat-intel-catalog'],
    queryFn: api.getThreatIntelCatalog,
  })

  const [draft, setDraft] = useState<Record<string, RowState>>({})

  const { statusFor } = useFeedStatus()

  // Re-seed draft whenever the server catalogue changes.
  useEffect(() => {
    if (!catalog) return
    const next: Record<string, RowState> = {}
    for (const item of catalog) {
      next[item.name] = {
        enabled: item.enabled,
        continuous: item.continuous,
        interval_minutes: item.interval_minutes,
      }
    }
    setDraft(next)
  }, [catalog])

  const saveMut = useMutation({
    mutationFn: (toggles: ThreatIntelToggle[]) => api.saveThreatIntelSources(toggles),
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: ['threat-intel-catalog'] }),
        qc.invalidateQueries({ queryKey: ['remote-json-pull'] }),
        qc.invalidateQueries({ queryKey: ['rss-pull'] }),
        qc.invalidateQueries({ queryKey: ['active-jobs'] }),
        qc.invalidateQueries({ queryKey: ['viewer-summary', 'feed-status'] }),
      ])
      await api.reloadScheduler().catch(() => { /* best-effort */ })
    },
  })

  if (isLoading) return <div className="card text-sm text-gray-500">Loading…</div>
  if (!catalog) return null

  const update = (name: string, patch: Partial<RowState>) =>
    setDraft(prev => ({ ...prev, [name]: { ...prev[name], ...patch } }))

  const dirty = catalog.some(item => {
    const d = draft[item.name]
    if (!d) return false
    return (
      d.enabled !== item.enabled ||
      d.continuous !== item.continuous ||
      d.interval_minutes !== item.interval_minutes
    )
  })

  const onSave = () => {
    const toggles: ThreatIntelToggle[] = catalog.map(item => {
      const d = draft[item.name] ?? {
        enabled: item.enabled,
        continuous: item.continuous,
        interval_minutes: item.interval_minutes,
      }
      return {
        name: item.name,
        enabled: d.enabled,
        continuous: d.continuous,
        interval_minutes: d.interval_minutes,
      }
    })
    saveMut.mutate(toggles)
  }

  return (
    <div className="card space-y-4">
      <div>
        <h3 className="text-sm font-semibold text-gray-200">
          Open Threat Intel / Vulnerability Sources
        </h3>
        <p className="text-xs text-gray-500 mt-1">
          Curated open feeds. Toggle one on to add it as a pull source, then turn on
          scheduled pulling to fetch it on an interval. Click Save to apply your changes.
        </p>
      </div>

      <div className="space-y-2">
        {catalog.map(item => {
          const d = draft[item.name] ?? {
            enabled: item.enabled,
            continuous: item.continuous,
            interval_minutes: item.interval_minutes,
          }
          return (
            <div
              key={item.name}
              className="rounded-lg border border-gray-700 bg-gray-800/50 p-3"
            >
              <div className="flex items-start gap-3">
                <div className="pt-0.5 shrink-0">
                  <Toggle
                    checked={d.enabled}
                    onChange={v => update(item.name, { enabled: v })}
                  />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <p
                      className={clsx(
                        'text-sm font-medium',
                        d.enabled ? 'text-gray-200' : 'text-gray-400',
                      )}
                    >
                      {item.title}
                    </p>
                    <span className="text-xs text-gray-500 border border-gray-700 rounded px-1.5 py-0.5 shrink-0">
                      {item.kind === 'rss_pull' ? 'RSS' : 'JSON'}
                    </span>
                    {d.enabled && (
                      <FeedStatusMarker status={statusFor(item.name)} className="shrink-0" />
                    )}
                  </div>
                  <p className="text-xs text-gray-500 truncate" title={item.url}>
                    {item.url}
                  </p>
                  {item.info && (
                    <p className="text-xs text-gray-600 mt-0.5">{item.info}</p>
                  )}

                  {d.enabled && (
                    <div className="flex items-center gap-3 mt-2 flex-wrap">
                      <div className="flex items-center gap-2">
                        <Toggle
                          checked={d.continuous}
                          onChange={v => update(item.name, { continuous: v })}
                        />
                        <span className="text-sm text-gray-300">Continuous pull</span>
                      </div>
                      {d.continuous && (
                        <div className="flex items-center gap-2">
                          <label className="label mb-0">Every</label>
                          <input
                            className="input w-20"
                            type="number"
                            min={1}
                            value={d.interval_minutes}
                            onChange={e =>
                              update(item.name, {
                                interval_minutes: Number(e.target.value),
                              })
                            }
                          />
                          <span className="text-sm text-gray-400">min</span>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            </div>
          )
        })}
      </div>

      <div className="flex items-center gap-3">
        <button
          className="btn-primary"
          disabled={!dirty || saveMut.isPending}
          onClick={onSave}
        >
          <Save className="w-3.5 h-3.5" /> {saveMut.isPending ? 'Saving…' : 'Save'}
        </button>
        {saveMut.isError && <span className="text-xs text-red-400">Save failed.</span>}
        {saveMut.isSuccess && !dirty && (
          <span className="text-xs text-green-400">Saved.</span>
        )}
      </div>
    </div>
  )
}
