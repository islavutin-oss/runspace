'use client'

import { useState } from 'react'
import { useIsWidgetIntentWired, useWidgetIntent } from './widgetIntent'
import { parseLoosePayload } from '../utils/loosePayload'
import { applyAliases, FORM_ALIASES } from '../utils/normalizeBlock'

/**
 * A `form` block: the agent asks for a few fields and gets the answers back as
 * an ordinary message.
 *
 * There is deliberately no submit endpoint. Submitting dispatches a widget
 * intent, exactly as a chart click does, so the values arrive as a turn the
 * agent can act on with a tool. That keeps the whole feature inside the block
 * vocabulary — no new backend route, no form state on the server, and it works
 * in any host that already renders these blocks.
 */
interface Field {
  key: string
  label: string
  placeholder?: string
  /** text | email | tel | textarea. Anything else is treated as text. */
  type?: string
  required?: boolean
}

interface FormSpec {
  title?: string
  body?: string
  fields: Field[]
  submitLabel?: string
  /** Sent on submit with {key} placeholders filled in. */
  prompt?: string
  /** Shown in place of the form once it has been sent. */
  done?: string
}

export default function InlineForm({ json }: { json: string }) {
  const dispatch = useWidgetIntent()
  const wired = useIsWidgetIntentWired()
  const [values, setValues] = useState<Record<string, string>>({})
  const [sent, setSent] = useState(false)
  const [busy, setBusy] = useState(false)

  let spec: FormSpec
  try {
    // Loose payload + key aliases: a block written as flat YAML, or with
    // `href` for `url` / `inputs` for `fields`, is still this block.
    spec = applyAliases(parseLoosePayload(json), FORM_ALIASES) as any
    if (!spec || typeof spec !== 'object') throw new Error('could not parse')
  } catch (e) {
    return (
      <div className="my-3 rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-600 not-prose">
        Invalid form block: {String(e).slice(0, 90)}
      </div>
    )
  }
  const fields = Array.isArray(spec.fields) ? spec.fields : []
  if (!fields.length) {
    return (
      <div className="my-3 rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-600 not-prose">
        form — expected a non-empty `fields` list.
      </div>
    )
  }

  const missing = fields.filter((f) => f.required && !(values[f.key] || '').trim())

  async function submit() {
    if (missing.length || busy) return
    setBusy(true)
    const filled = (spec.prompt || 'Here are my details:')
      .replace(/\{(\w+)\}/g, (_, k) => values[k] || '')
      .trim()
    const summary =
      filled +
      (spec.prompt
        ? ''
        : '\n' + fields.map((f) => `${f.label}: ${values[f.key] || '—'}`).join('\n'))
    try {
      await dispatch({ text: summary, source: 'action', meta: { form: spec.title } })
      setSent(true)
    } finally {
      setBusy(false)
    }
  }

  if (sent) {
    return (
      <div
        className="my-3 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800 not-prose"
        role="status"
      >
        {spec.done || 'Sent — thank you.'}
      </div>
    )
  }

  return (
    <div className="my-3 rounded-lg border border-gray-200 bg-white p-4 shadow-sm not-prose">
      {spec.title && <div className="text-sm font-semibold text-gray-900">{spec.title}</div>}
      {spec.body && <p className="mt-1 text-[13px] leading-[1.5] text-gray-600">{spec.body}</p>}

      <div className="mt-3 grid gap-2.5 sm:grid-cols-2">
        {fields.map((f) => {
          const isArea = f.type === 'textarea'
          return (
            <label key={f.key} className={`block text-[12px] ${isArea ? 'sm:col-span-2' : ''}`}>
              <span className="text-gray-600">
                {f.label}
                {f.required && <span className="text-gray-400"> *</span>}
              </span>
              {isArea ? (
                <textarea
                  rows={3}
                  value={values[f.key] || ''}
                  placeholder={f.placeholder}
                  onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))}
                  className="mt-1 w-full rounded-md border border-gray-200 px-2.5 py-1.5 text-[13px] outline-none focus:border-gray-400"
                />
              ) : (
                <input
                  type={['email', 'tel', 'text'].includes(f.type || '') ? f.type : 'text'}
                  value={values[f.key] || ''}
                  placeholder={f.placeholder}
                  onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))}
                  className="mt-1 w-full rounded-md border border-gray-200 px-2.5 py-1.5 text-[13px] outline-none focus:border-gray-400"
                />
              )}
            </label>
          )
        })}
      </div>

      <div className="mt-3 flex items-center gap-3">
        <button
          onClick={submit}
          disabled={!!missing.length || busy || !wired}
          className="rounded-lg px-3.5 py-1.5 text-[13px] font-medium text-white transition-opacity disabled:opacity-40"
          style={{ background: '#111827' }}
        >
          {busy ? 'Sending…' : spec.submitLabel || 'Send'}
        </button>
        {!wired && (
          <span className="text-[12px] text-gray-400">
            No chat host is wired up, so this form cannot send.
          </span>
        )}
      </div>
    </div>
  )
}
