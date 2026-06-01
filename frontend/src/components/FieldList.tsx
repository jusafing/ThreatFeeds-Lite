/**
 * Reusable field list editor — shows core fields (toggle only) and
 * custom fields (add / toggle-or-delete depending on context).
 *
 * When isSourceScoped=true (used inside SourceFieldsPanel):
 *   - Custom fields show an enable/disable toggle instead of a delete button.
 *   - onToggleCustom is called with (name, enabled) to add/remove the field
 *     from the source's override list.
 *
 * When isSourceScoped=false (default, global GlobalFieldsTab):
 *   - Custom fields show the delete (Trash2) button.
 *   - onDeleteCustom is called with (name).
 */
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api, FieldDef, FieldsConfig } from '../api/client'
import { Plus, Trash2, ChevronDown, ChevronUp } from 'lucide-react'
import Toggle from './Toggle'

interface Props {
  config: FieldsConfig
  /** Query key(s) to invalidate after mutations. Defaults to [['fields']]. */
  queryKeys?: string[][]
  /** Override core-field toggle — called with (name, enabled). Defaults to global API. */
  onToggleCore?: (name: string, enabled: boolean) => Promise<unknown>
  /** Override custom field add. Defaults to global API. */
  onAddCustom?: (field: FieldDef) => Promise<unknown>
  /**
   * When isSourceScoped=false (default): called to delete a custom field by name.
   * Defaults to global API deleteCustomField.
   */
  onDeleteCustom?: (name: string) => Promise<unknown>
  /**
   * When isSourceScoped=true: called with (name, enabled) to enable/disable
   * a custom field at source level. Required when isSourceScoped=true.
   */
  onToggleCustom?: (name: string, enabled: boolean) => Promise<unknown>
  /**
   * When true, renders an enable/disable toggle for custom fields instead of
   * the delete button. Intended for per-source field panels.
   */
  isSourceScoped?: boolean
  /**
   * When isSourceScoped=true, the set of custom field names that are currently
   * enabled at source level (present in the source's override list).
   */
  enabledCustomNames?: Set<string>
}

export default function FieldList({
  config,
  queryKeys,
  onToggleCore,
  onAddCustom,
  onDeleteCustom,
  onToggleCustom,
  isSourceScoped = false,
  enabledCustomNames,
}: Props) {
  const qc = useQueryClient()
  const [newName, setNewName] = useState('')
  const [newDesc, setNewDesc] = useState('')
  const [coreExpanded, setCoreExpanded] = useState(false)

  const keys = queryKeys ?? [['fields']]
  const invalidate = () => keys.forEach(k => qc.invalidateQueries({ queryKey: k }))

  const toggleCoreMut = useMutation({
    mutationFn: ({ name, enabled }: { name: string; enabled: boolean }) =>
      onToggleCore
        ? onToggleCore(name, enabled)
        : api.toggleCoreField(name, enabled),
    onSuccess: invalidate,
  })

  const addCustomMut = useMutation({
    mutationFn: (field: FieldDef) =>
      onAddCustom ? onAddCustom(field) : api.addCustomField(field),
    onSuccess: () => { invalidate(); setNewName(''); setNewDesc('') },
  })

  const deleteCustomMut = useMutation({
    mutationFn: (name: string) =>
      onDeleteCustom ? onDeleteCustom(name) : api.deleteCustomField(name),
    onSuccess: invalidate,
  })

  const toggleCustomMut = useMutation({
    mutationFn: ({ name, enabled }: { name: string; enabled: boolean }) =>
      onToggleCustom!(name, enabled),
    onSuccess: invalidate,
  })

  return (
    <div className="space-y-4">
      {/* Core fields (collapsible) */}
      <div>
        <button
          className="flex items-center gap-2 text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2 hover:text-gray-200"
          onClick={() => setCoreExpanded(v => !v)}
        >
          {coreExpanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
          Core Fields ({config.core_fields.filter(f => f.enabled).length}/{config.core_fields.length} enabled)
        </button>

        {coreExpanded && (
          <div className="space-y-1 max-h-64 overflow-y-auto pr-1">
            {config.core_fields.map(field => (
              <div key={field.name} className="flex items-center justify-between py-1.5 px-3 rounded-md bg-gray-800/50 hover:bg-gray-800">
                <div className="min-w-0">
                  <p className="text-sm font-mono text-gray-200 truncate">{field.name}</p>
                  {field.description && (
                    <p className="text-xs text-gray-500 truncate">{field.description}</p>
                  )}
                </div>
                <Toggle
                  checked={field.enabled ?? true}
                  onChange={enabled => toggleCoreMut.mutate({ name: field.name, enabled })}
                />
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Custom fields */}
      <div>
        <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
          Custom Fields
        </p>

        <div className="space-y-1 mb-3">
          {config.custom_fields.length === 0 && (
            <p className="text-xs text-gray-600 px-3 py-2">No custom fields defined.</p>
          )}
          {config.custom_fields.map(field => {
            const isEnabled = isSourceScoped
              ? (enabledCustomNames?.has(field.name) ?? true)
              : true

            return (
              <div key={field.name} className="flex items-center gap-2 py-1.5 px-3 rounded-md bg-gray-800/50 hover:bg-gray-800">
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-mono text-gray-200 truncate">{field.name}</p>
                  {field.description && (
                    <p className="text-xs text-gray-500 truncate">{field.description}</p>
                  )}
                </div>

                {isSourceScoped ? (
                  /* Per-source: toggle enable/disable */
                  <Toggle
                    checked={isEnabled}
                    onChange={enabled => toggleCustomMut.mutate({ name: field.name, enabled })}
                  />
                ) : (
                  /* Global: delete button */
                  <button
                    className="btn-ghost p-1 text-red-400 hover:text-red-300"
                    onClick={() => deleteCustomMut.mutate(field.name)}
                    title="Delete custom field"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                )}
              </div>
            )
          })}
        </div>

        {/* Add custom field */}
        <div className="flex gap-2 items-end">
          <div className="flex-1">
            <label className="label">Field Name</label>
            <input
              className="input font-mono"
              placeholder="my_custom_field"
              value={newName}
              onChange={e => setNewName(e.target.value.replace(/\s+/g, '_').toLowerCase())}
            />
          </div>
          <div className="flex-[2]">
            <label className="label">Description</label>
            <input
              className="input"
              placeholder="Optional description"
              value={newDesc}
              onChange={e => setNewDesc(e.target.value)}
            />
          </div>
          <button
            className="btn-primary"
            disabled={!newName.trim() || addCustomMut.isPending}
            onClick={() => addCustomMut.mutate({ name: newName.trim(), description: newDesc.trim() || undefined })}
          >
            <Plus className="w-3.5 h-3.5" />
            Add
          </button>
        </div>
        {addCustomMut.isError && (
          <p className="text-xs text-red-400 mt-1">{String(addCustomMut.error)}</p>
        )}
      </div>
    </div>
  )
}
