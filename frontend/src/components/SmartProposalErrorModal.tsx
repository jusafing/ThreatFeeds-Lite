/**
 * Read-only detail popup for a smart-mapping proposal's LLM exchange
 * (prompts-033; generalized in prompts-034).
 *
 * Surfaced from the Smart Mappings list next to error / no-mapping rows so the
 * operator can inspect exactly what was sent to and returned from the model
 * without leaving the page. Shows the system prompt, user prompt, and the raw
 * (unparsed) model response. Nothing here is editable.
 *
 * prompts-034: also accepts a generic `detail` payload so failures that never
 * produced a proposal row (e.g. a job that errored before inserting anything)
 * can surface their error in the same modal shape.
 */
import type { ReactNode } from 'react'
import { X } from 'lucide-react'
import type { SmartProposal } from '../api/client'

interface LogSection {
  title: string
  body: string
}

interface LogDetail {
  title: string
  subtitle?: ReactNode
  sections: LogSection[]
}

type Props =
  | { proposal: SmartProposal; onClose: () => void }
  | { detail: LogDetail; onClose: () => void }

function Section({ title, body }: LogSection) {
  return (
    <div>
      <div className="text-xs font-medium text-gray-400 mb-1">{title}</div>
      <pre className="text-[11px] font-mono text-gray-300 bg-gray-950/70 border border-gray-800 rounded px-2.5 py-2 whitespace-pre-wrap break-words max-h-60 overflow-y-auto">
        {body || <span className="text-gray-600 italic">(empty)</span>}
      </pre>
    </div>
  )
}

export default function SmartProposalErrorModal(props: Props) {
  const { onClose } = props
  const view: LogDetail =
    'proposal' in props
      ? {
          title: `Proposal #${props.proposal.id} — LLM log`,
          subtitle: (
            <>
              Raw exchange with{' '}
              <span className="font-mono text-gray-400">
                {props.proposal.provider_name ?? '—'} / {props.proposal.model ?? '—'}
              </span>
              . Read-only.
            </>
          ),
          sections: [
            { title: 'System prompt', body: props.proposal.prompt_system },
            { title: 'User prompt', body: props.proposal.prompt_user },
            // prompts-037: the raw HTTP request + full response envelope.
            {
              title: 'Raw HTTP request (llm_request_raw)',
              body: props.proposal.llm_request_raw ?? '',
            },
            {
              title: 'Raw HTTP response — full JSON (llm_response_json)',
              body: props.proposal.llm_response_json ?? '',
            },
            {
              title: 'Extracted model content (llm_response_raw)',
              body: props.proposal.llm_response_raw,
            },
          ],
        }
      : props.detail

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70"
      role="dialog"
      aria-modal="true"
      aria-labelledby="smart-log-title"
    >
      <div className="card w-full max-w-2xl mx-4 max-h-[90vh] overflow-y-auto">
        <div className="flex items-start justify-between mb-4">
          <div>
            <h2 id="smart-log-title" className="text-base font-semibold text-gray-100">
              {view.title}
            </h2>
            {view.subtitle && <p className="text-xs text-gray-500 mt-1">{view.subtitle}</p>}
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
          {view.sections.map((s) => (
            <Section key={s.title} title={s.title} body={s.body} />
          ))}
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
