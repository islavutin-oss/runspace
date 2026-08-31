'use client'

import { useEffect, useState } from 'react'

export interface GatewayInfo {
  id: string
  /** Display name — "Telegram", "WhatsApp", "Slack". */
  label: string
  /** connected | configured | unconfigured | error */
  status?: string
  /** Free-text detail: the bot handle, the number, the last error. */
  detail?: string
  /** Which env vars this gateway needs, so an unconfigured one says how. */
  requires?: string[]
  docsUrl?: string
}

interface Props {
  section: {
    id: string
    label?: string
    description?: string
    /** Static list, or omit and give an `endpoint` to fetch one. */
    gateways?: GatewayInfo[]
    endpoint?: string
  }
}

const STYLE: Record<string, { dot: string; text: string; bg: string; word: string }> = {
  connected:    { dot: '#22C55E', text: '#15803D', bg: '#DCFCE7', word: 'connected' },
  configured:   { dot: '#0EA5E9', text: '#0369A1', bg: '#E0F2FE', word: 'configured' },
  unconfigured: { dot: '#9CA3AF', text: '#4B5563', bg: '#F3F4F6', word: 'not set up' },
  error:        { dot: '#EF4444', text: '#B91C1C', bg: '#FEE2E2', word: 'error' },
}

/**
 * Which external channels this workspace can be reached on.
 *
 * `gateway_status` was named in the settings type union long before it
 * rendered anything: a section declaring it fell through to "Unknown widget
 * type", so no deployment could show whether Telegram or WhatsApp was
 * actually wired up.
 */
export default function GatewayStatusWidget({ section }: Props) {
  const [gateways, setGateways] = useState<GatewayInfo[]>(section.gateways || [])
  const [loading, setLoading] = useState(Boolean(section.endpoint && !section.gateways))

  useEffect(() => {
    if (!section.endpoint || section.gateways) return
    let cancelled = false
    fetch(section.endpoint)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (cancelled || !d) return
        setGateways(Array.isArray(d) ? d : d.gateways || [])
      })
      .catch(() => {})
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [section.endpoint, section.gateways])

  return (
    <div className="p-4 bg-gray-50 rounded-lg">
      {loading && <p className="text-xs text-gray-400">Checking…</p>}
      {!loading && !gateways.length && (
        <p className="text-xs text-gray-500">No gateways declared for this workspace.</p>
      )}

      <div className="space-y-2">
        {gateways.map((g) => {
          const s = STYLE[g.status || 'unconfigured'] || STYLE.unconfigured
          return (
            <div key={g.id} className="flex items-start gap-3 bg-white border border-gray-200 rounded-lg px-3 py-2.5">
              <span className="mt-1.5 w-2 h-2 rounded-full shrink-0" style={{ background: s.dot }} />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-gray-900">{g.label}</span>
                  <span
                    className="text-[11px] px-1.5 py-0.5 rounded-full font-medium"
                    style={{ background: s.bg, color: s.text }}
                  >
                    {s.word}
                  </span>
                </div>
                {g.detail && <div className="text-xs text-gray-600 mt-0.5 break-words">{g.detail}</div>}
                {!g.detail && g.requires?.length ? (
                  <div className="text-xs text-gray-500 mt-0.5">
                    needs {g.requires.map((r) => <code key={r} className="mx-0.5">{r}</code>)}
                  </div>
                ) : null}
              </div>
              {g.docsUrl && (
                <a href={g.docsUrl} className="text-xs text-sky-600 shrink-0 mt-0.5">
                  set up
                </a>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
