/**
 * Test Details modal — renders the transcript produced by
 * backend/llm/test_runner.run_provider_test (prompts-022 step 6).
 *
 * Used by:
 *   - AddProviderWizard: after the operator clicks "Test Connection"
 *     inside the wizard (ephemeral test against a draft provider).
 *   - LLMProvidersTab / ProviderCard: after the per-card Test button
 *     (test against an already-persisted provider).
 *
 * The transcript is a list of LLMTestStepDetail records — one per
 * wire call (list_models, complete, or anthropic synthetic). Headers
 * are pre-redacted by the backend and request/response bodies are
 * truncated at 8 KiB; this modal renders them verbatim inside <pre>
 * blocks. No secrets reach this component.
 */
import { useEffect } from 'react'
import { X, CheckCircle2, XCircle } from 'lucide-react'

import type { LLMTestRunResult, LLMTestStepDetail } from '../api/client'

interface Props {
  /** The transcript to render. */
  result: LLMTestRunResult
  /** Provider display label for the header (name from the draft / record). */
  providerLabel: string
  onClose: () => void
}

export default function TestDetailsModal({ result, providerLabel, onClose }: Props) {
  // Close on Escape — matches the rest of the project's modal pattern.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  const ok = result.status === 'ok'

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="test-details-title"
    >
      <div className="card w-full max-w-3xl max-h-[90vh] overflow-y-auto">
        <div className="flex items-start justify-between mb-4">
          <div className="flex items-center gap-2">
            {ok ? (
              <CheckCircle2 className="w-5 h-5 text-green-400 shrink-0" />
            ) : (
              <XCircle className="w-5 h-5 text-red-400 shrink-0" />
            )}
            <div>
              <h2 id="test-details-title" className="text-base font-semibold text-gray-100">
                Test details — {providerLabel}
              </h2>
              <p className="text-xs text-gray-500 mt-0.5">
                {ok ? 'All steps succeeded.' : 'One or more steps failed.'}
                {' '}
                {result.details.length} step{result.details.length === 1 ? '' : 's'}.
              </p>
            </div>
          </div>
          <button
            type="button"
            className="text-gray-400 hover:text-gray-200"
            aria-label="Close"
            onClick={onClose}
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Summary line: models discovered + sample completion */}
        <div className="mb-4 text-xs space-y-1">
          {result.models && result.models.length > 0 && (
            <p className="text-gray-300">
              <span className="text-gray-500">Models discovered:</span>{' '}
              {result.models.length} ({result.models.slice(0, 3).join(', ')}
              {result.models.length > 3 && `, +${result.models.length - 3} more`})
            </p>
          )}
          {result.sample && (
            <p className="text-gray-300">
              <span className="text-gray-500">Sample completion:</span>{' '}
              <span className="font-mono">{result.sample}</span>
            </p>
          )}
        </div>

        {result.details.length === 0 ? (
          <p className="text-sm text-gray-500 italic">No transcript captured.</p>
        ) : (
          <ol className="space-y-3">
            {result.details.map((step, idx) => (
              <StepBlock key={idx} step={step} index={idx} />
            ))}
          </ol>
        )}

        <div className="mt-5 flex justify-end border-t border-gray-800 pt-3">
          <button type="button" className="btn-secondary" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  )
}

function StepBlock({ step, index }: { step: LLMTestStepDetail; index: number }) {
  const ok = step.error === null
  return (
    <li className="border border-gray-800 rounded p-3 space-y-2">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2">
          {ok ? (
            <CheckCircle2 className="w-4 h-4 text-green-400" />
          ) : (
            <XCircle className="w-4 h-4 text-red-400" />
          )}
          <span className="text-sm font-medium text-gray-200">
            #{index + 1} {step.step}
          </span>
          {step.method && step.url && (
            <span className="text-xs text-gray-500 font-mono break-all">
              {step.method} {step.url}
            </span>
          )}
        </div>
        <div className="text-xs text-gray-500 flex items-center gap-3">
          {step.status_code !== null && (
            <span className={ok ? 'text-gray-400' : 'text-red-400'}>
              HTTP {step.status_code}
            </span>
          )}
          <span>{step.duration_ms} ms</span>
        </div>
      </div>

      {step.error && (
        <pre className="text-xs text-red-300 bg-red-900/10 border border-red-800/40 rounded p-2 whitespace-pre-wrap break-all">
          {step.error}
        </pre>
      )}

      {!step.error && step.warning && (
        <pre className="text-xs text-amber-300 bg-amber-900/10 border border-amber-800/40 rounded p-2 whitespace-pre-wrap break-all">
          {step.warning}
        </pre>
      )}

      {step.headers_redacted && Object.keys(step.headers_redacted).length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer text-gray-400 hover:text-gray-200">
            Request headers (redacted)
          </summary>
          <pre className="mt-1 text-gray-300 bg-gray-900/50 border border-gray-800 rounded p-2 whitespace-pre-wrap break-all">
            {Object.entries(step.headers_redacted)
              .map(([k, v]) => `${k}: ${v}`)
              .join('\n')}
          </pre>
        </details>
      )}

      {step.request_body && (
        <details className="text-xs">
          <summary className="cursor-pointer text-gray-400 hover:text-gray-200">
            Request body
          </summary>
          <pre className="mt-1 text-gray-300 bg-gray-900/50 border border-gray-800 rounded p-2 whitespace-pre-wrap break-all">
            {step.request_body}
          </pre>
        </details>
      )}

      {step.response_body && (
        <details className="text-xs">
          <summary className="cursor-pointer text-gray-400 hover:text-gray-200">
            Response body
          </summary>
          <pre className="mt-1 text-gray-300 bg-gray-900/50 border border-gray-800 rounded p-2 whitespace-pre-wrap break-all">
            {step.response_body}
          </pre>
        </details>
      )}
    </li>
  )
}
