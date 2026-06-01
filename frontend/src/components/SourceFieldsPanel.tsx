/**
 * SourceFieldsPanel — fetches and renders per-source field config.
 *
 * Write-back fix: we maintain two separate pieces of data:
 *   - mergedConfig  — the full resolved config for display (GET /fields endpoint)
 *   - rawFields     — the minimal override block stored in sources.yaml (from the
 *                     source list endpoint). This is what we mutate and write back.
 *
 * Writing back the raw override block (not the merged config) prevents the
 * "bloated fields" bug where the PUT endpoint would store 31 core fields into
 * every source's fields key.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, FieldDef, FieldsConfig, RemoteJsonSourceDef } from '../api/client'
import FieldList from './FieldList'

interface Props {
  /** e.g. "api-pull" | "rss-pull" | "remote-json-pull" | "listener" */
  sourceType: string
  /** Source name. Use "listener" for the push listener. */
  sourceName: string
}

type RawFields = { core_fields?: FieldDef[]; custom_fields?: FieldDef[] } | null

/** Fetch the raw (un-merged) fields block for a source directly from the source list. */
function useRawFields(sourceType: string, sourceName: string): RawFields {
  const { data: apiPull = [] }    = useQuery({ queryKey: ['api-pull'],         queryFn: api.getApiPull,        enabled: sourceType === 'api-pull',          staleTime: 10_000 })
  const { data: rssPull = [] }    = useQuery({ queryKey: ['rss-pull'],         queryFn: api.getRssPull,        enabled: sourceType === 'rss-pull',          staleTime: 10_000 })
  const { data: remotePull = [] } = useQuery({ queryKey: ['remote-json-pull'], queryFn: api.getRemoteJsonPull, enabled: sourceType === 'remote-json-pull',   staleTime: 10_000 })
  const { data: listener }        = useQuery({ queryKey: ['listener'],         queryFn: api.getListener,       enabled: sourceType === 'listener',          staleTime: 10_000 })

  if (sourceType === 'listener') {
    return (listener?.fields as RawFields) ?? null
  }
  const list =
    sourceType === 'api-pull'         ? apiPull    :
    sourceType === 'rss-pull'         ? rssPull    :
    sourceType === 'remote-json-pull' ? (remotePull as RemoteJsonSourceDef[]) :
    []
  const src = (list as Array<{ name: string; fields?: unknown }>).find(s => s.name === sourceName)
  return (src?.fields as RawFields) ?? null
}

export default function SourceFieldsPanel({ sourceType, sourceName }: Props) {
  const qc = useQueryClient()
  const mergedQueryKey = ['source-fields', sourceType, sourceName]

  const { data: mergedConfig, isLoading } = useQuery<FieldsConfig>({
    queryKey: mergedQueryKey,
    queryFn: () =>
      sourceType === 'listener'
        ? api.getListenerFields()
        : api.getSourceFields(sourceType, sourceName),
  })

  const rawFields = useRawFields(sourceType, sourceName)

  if (isLoading) return <p className="text-xs text-gray-500 px-1">Loading fields…</p>
  if (!mergedConfig) return null

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: mergedQueryKey })
    // Also invalidate the source list so rawFields stays fresh
    if (sourceType === 'api-pull')         qc.invalidateQueries({ queryKey: ['api-pull'] })
    else if (sourceType === 'rss-pull')    qc.invalidateQueries({ queryKey: ['rss-pull'] })
    else if (sourceType === 'remote-json-pull') qc.invalidateQueries({ queryKey: ['remote-json-pull'] })
    else if (sourceType === 'listener')    qc.invalidateQueries({ queryKey: ['listener'] })
  }

  /**
   * Build the minimal override block to persist.
   * We start from the existing raw block (to preserve any existing overrides)
   * and apply only the specific change passed in.
   */
  const buildDelta = (patch: Partial<{ core_fields: FieldDef[]; custom_fields: FieldDef[] }>): FieldsConfig => ({
    core_fields:   patch.core_fields   ?? rawFields?.core_fields   ?? [],
    custom_fields: patch.custom_fields ?? rawFields?.custom_fields ?? [],
  })

  const writeback = async (delta: FieldsConfig) => {
    if (sourceType === 'listener') {
      await api.putListenerFields(delta)
    } else {
      await api.putSourceFields(sourceType, sourceName, delta)
    }
    invalidate()
  }

  // Core field toggle: update only the named field in the raw core_fields override list
  const onToggleCore = async (fieldName: string, enabled: boolean) => {
    const existingCore = rawFields?.core_fields ?? []
    // Upsert the override entry for this field
    const updated = existingCore.some(f => f.name === fieldName)
      ? existingCore.map(f => f.name === fieldName ? { ...f, enabled } : f)
      : [...existingCore, { name: fieldName, enabled }]
    await writeback(buildDelta({ core_fields: updated }))
  }

  // Add custom field: append to raw custom_fields list
  const onAddCustom = async (field: FieldDef) => {
    const existing = rawFields?.custom_fields ?? []
    if (existing.some(f => f.name === field.name)) return
    await writeback(buildDelta({ custom_fields: [...existing, field] }))
  }

  // Toggle custom field: add to or remove from the source's custom_fields override list
  const onToggleCustom = async (fieldName: string, enabled: boolean) => {
    const existing = rawFields?.custom_fields ?? []
    let updated: FieldDef[]
    if (enabled) {
      // Re-enable: add back to the source's list (use description from mergedConfig if available)
      const fieldDef = mergedConfig.custom_fields.find(f => f.name === fieldName)
      if (!existing.some(f => f.name === fieldName)) {
        updated = [...existing, fieldDef ?? { name: fieldName }]
      } else {
        updated = existing
      }
    } else {
      // Disable: remove from source's list
      updated = existing.filter(f => f.name !== fieldName)
    }
    await writeback(buildDelta({ custom_fields: updated }))
  }

  // The set of custom field names present in the raw override list (= enabled at source level)
  const enabledCustomNames = new Set<string>(
    (rawFields?.custom_fields ?? []).map(f => f.name)
  )

  return (
    <FieldList
      config={mergedConfig}
      queryKeys={[mergedQueryKey]}
      onToggleCore={onToggleCore}
      onAddCustom={onAddCustom}
      onToggleCustom={onToggleCustom}
      isSourceScoped={true}
      enabledCustomNames={enabledCustomNames}
    />
  )
}
