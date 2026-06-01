/**
 * Normalizer section (prompts-032 Phase A; trimmed in prompts-033 Phase F).
 *
 * Promoted from a Configuration sub-tab to a top-level sidebar section.
 * Hosts three sub-tabs:
 *   - Settings        → normalizer enable/mode/interval + manual run
 *   - Smart Mappings  → LLM-assisted proposal review queue (reused page body)
 *   - LLM Providers   → provider catalogue / credentials management
 *
 * The Settings panel is moved verbatim from the former Configuration
 * "Normalizer" tab. The Activity and Mapping-versions sub-tabs were removed
 * in prompts-033 Phase F; per-source version history now lives in the
 * NormalizedTable version filter.
 */
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { clsx } from 'clsx'
import { AlertTriangle, Play, History } from 'lucide-react'
import { api } from '../api/client'
import { useRunNow, useNormalizerRunning } from '../hooks/useNormalizerRun'
import Toggle from '../components/Toggle'
import RunHistoryModal from '../components/RunHistoryModal'
import SmartMappings from './SmartMappings'
import LLMProvidersTab from './configuration/LLMProvidersTab'

type NormalizerSubTab =
  | 'settings'
  | 'smart-mappings'
  | 'llm-providers'

const SUB_TABS: { id: NormalizerSubTab; label: string }[] = [
  { id: 'settings',       label: 'Settings' },
  { id: 'smart-mappings', label: 'Smart Mappings' },
  { id: 'llm-providers',  label: 'LLM Providers' },
]

export default function Normalizer() {
  const [sub, setSub] = useState<NormalizerSubTab>('settings')

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-lg font-semibold text-gray-100">Normalizer</h1>
        <p className="text-sm text-gray-500">
          Map raw ingest fields to a canonical schema and manage LLM-assisted
          mapping proposals.
        </p>
      </div>

      {/* Sub-tabs */}
      <div className="border-b border-gray-800">
        <nav className="flex gap-6 flex-wrap" role="tablist" aria-label="Normalizer sub-tabs">
          {SUB_TABS.map(({ id, label }) => (
            <button
              key={id}
              role="tab"
              aria-selected={sub === id}
              onClick={() => setSub(id)}
              className={clsx(
                'pb-3 text-sm font-medium transition-colors',
                sub === id ? 'tab-active' : 'tab-inactive',
              )}
            >
              {label}
            </button>
          ))}
        </nav>
      </div>

      {sub === 'settings' && (
        <div className="max-w-3xl">
          <NormalizerSettings />
        </div>
      )}
      {sub === 'smart-mappings' && <SmartMappings />}
      {sub === 'llm-providers' && (
        <div className="max-w-3xl">
          <LLMProvidersTab />
        </div>
      )}
    </div>
  )
}

function NormalizerSettings() {
  const qc = useQueryClient()
  const { data: cfg = {} } = useQuery({
    queryKey: ['normalizer-config'],
    queryFn: api.getNormalizerConfig,
  })
  const [runResult, setRunResult] = useState<Record<string, unknown> | null>(null)
  // prompts-040: run state is shared globally via react-query so it survives
  // navigation and stays consistent across every run button. `running` is true
  // whenever ANY normalizer run is in flight, not just one started here.
  const runNow = useRunNow()
  const running = useNormalizerRunning()
  const [showHistory, setShowHistory] = useState(false)

  const updateCfg = useMutation({
    mutationFn: (body: Record<string, unknown>) => api.updateNormalizerConfig(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['normalizer-config'] }),
  })

  // Q5: when smart mode is selected but no consolidated mapping is active,
  // the engine silently falls back to auto. Surface that as a warning banner.
  const { data: activeRes } = useQuery({
    queryKey: ['consolidated-active'],
    queryFn: api.smartMappings.getActive,
  })

  const handleRun = async () => {
    setRunResult(null)
    try {
      const result = await runNow.mutateAsync()
      setRunResult(result)
    } catch (e) {
      setRunResult({ status: 'error', message: String(e) })
    }
  }

  const mode = (cfg['mode'] as string) ?? 'auto'
  const enabled = (cfg['enabled'] as boolean) ?? true
  const interval = (cfg['interval_minutes'] as number) ?? 10
  const noActiveConsolidated = !activeRes?.active
  const showSmartWarning = mode === 'smart' && noActiveConsolidated

  return (
    <div className="card space-y-5">
      <div>
        <h3 className="text-sm font-semibold text-gray-200">Normalizer</h3>
        <p className="text-xs text-gray-500 mt-1">
          Maps raw ingest fields to a canonical schema for cross-source analysis.
          Auto mode uses synonym groups; manual mode uses explicit field mappings
          per source; smart mode applies the active consolidated mapping
          (built from LLM-assisted proposals) to every feed.
        </p>
      </div>

      {/* Enabled toggle */}
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm text-gray-300">Enable Normalizer</p>
          <p className="text-xs text-gray-500">Runs on schedule and can be triggered manually</p>
        </div>
        <Toggle
          checked={enabled}
          onChange={v => updateCfg.mutate({ ...cfg, enabled: v })}
        />
      </div>

      {/* Mode */}
      <div>
        <label className="label">Mode</label>
        <select
          className="input w-72"
          value={mode}
          onChange={e => updateCfg.mutate({ ...cfg, mode: e.target.value })}
        >
          <option value="auto">Auto (synonym groups)</option>
          <option value="manual">Manual (per-source mapping)</option>
          <option value="smart">Smart (consolidated mapping)</option>
        </select>
        {showSmartWarning && (
          <div
            role="alert"
            className="mt-2 flex items-start gap-2 rounded border border-amber-700/60 bg-amber-900/20 p-2.5 text-xs text-amber-300"
          >
            <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
            <span>
              Smart mode is selected but no consolidated mapping is active. The
              normalizer will fall back to auto until you approve a consolidated
              proposal in the Smart Mappings tab.
            </span>
          </div>
        )}
      </div>

      {/* Interval */}
      <div>
        <label className="label">Run Interval (minutes)</label>
        <div className="flex items-center gap-2">
          <div className="max-w-[160px] flex-1">
            <input
              className="input"
              type="number"
              min={1}
              value={interval}
              onChange={e => updateCfg.mutate({ ...cfg, interval_minutes: Number(e.target.value) })}
            />
          </div>
          <button
            type="button"
            className="btn-secondary flex items-center gap-1.5 text-sm"
            onClick={() => setShowHistory(true)}
          >
            <History className="w-3.5 h-3.5" />
            Run History
          </button>
        </div>
      </div>

      {/* Manual run */}
      <div className="border-t border-gray-800 pt-4 flex items-center gap-3">
        <button
          className="btn-primary flex items-center gap-1.5"
          disabled={running}
          onClick={handleRun}
        >
          <Play className={clsx('w-3.5 h-3.5', running && 'animate-pulse')} />
          {running ? 'Running…' : 'Run Now'}
        </button>
        {runResult && (
          <span className={clsx(
            'text-xs font-mono',
            runResult['status'] === 'ok' ? 'text-green-400' : 'text-red-400',
          )}>
            {runResult['status'] === 'ok'
              ? `processed=${runResult['processed']} inserted=${runResult['inserted']} errors=${runResult['errors']}`
              : String(runResult['message'] ?? runResult['status'])
            }
          </span>
        )}
      </div>
      {showHistory && <RunHistoryModal onClose={() => setShowHistory(false)} />}
    </div>
  )
}
