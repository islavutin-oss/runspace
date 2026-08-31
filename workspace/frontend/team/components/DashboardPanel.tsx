'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { ExternalLink } from 'lucide-react'

export interface DashboardSpec {
  id: string
  label: string
  /** URL of the dashboard — typically a static HTML viewer or an embedded BI link. */
  src: string
  /** Optional emoji/icon shown next to the tab label. */
  icon?: string
}

/** Generic event surfaced by an embedded dashboard — host can react to it. */
export interface DashboardMessage {
  type: string
  [key: string]: any
}

interface DashboardPanelProps {
  dashboards: DashboardSpec[]
  /** Initial active tab id; defaults to the first dashboard. */
  defaultId?: string
  /** Optional URL params appended to the iframe src (e.g. {focus:"1.5.2"}).
   *  Each dashboard receives the same params; iframe re-loads when they change. */
  urlParams?: Record<string, string | undefined>
  /** Override which dashboard is active (controlled mode). */
  activeId?: string
  /** Tab change callback. */
  onActiveChange?(id: string): void
  /** Receives any postMessage events the iframe sends to its parent.
   *  Filter on `e.type` in the host. */
  onMessage?(event: DashboardMessage): void
}

/**
 * Iframe-tab container for embedded dashboards.
 * - sub-tab bar when more than one dashboard is provided
 * - URL-param plumbing for deep-linking (e.g. ?focus=1.5.2)
 * - postMessage listener for two-way sync with the host page
 */
export default function DashboardPanel({
  dashboards, defaultId, urlParams, activeId: activeIdProp, onActiveChange, onMessage,
}: DashboardPanelProps) {
  const [internalId, setInternalId] = useState<string>(defaultId || dashboards[0]?.id || '')
  const activeId = activeIdProp ?? internalId
  const setActive = (id: string) => {
    if (onActiveChange) onActiveChange(id)
    if (activeIdProp === undefined) setInternalId(id)
  }

  useEffect(() => {
    if (!dashboards.find(d => d.id === activeId) && dashboards[0]) setActive(dashboards[0].id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dashboards, activeId])

  // Listen for postMessage from any iframe in our subtree. The iframe targets
  // window.parent so it lands here once mounted.
  const onMessageRef = useRef(onMessage)
  useEffect(() => { onMessageRef.current = onMessage }, [onMessage])
  useEffect(() => {
    function handler(e: MessageEvent) {
      const data = e.data
      if (!data || typeof data !== 'object' || typeof data.type !== 'string') return
      onMessageRef.current?.(data as DashboardMessage)
    }
    window.addEventListener('message', handler)
    return () => window.removeEventListener('message', handler)
  }, [])

  const active = dashboards.find(d => d.id === activeId) || dashboards[0]

  // Compute the iframe src with appended params; the key forces a reload when src changes.
  const srcWithParams = useMemo(() => {
    if (!active) return ''
    if (!urlParams) return active.src
    const url = new URL(active.src, window.location.origin)
    for (const [k, v] of Object.entries(urlParams)) {
      if (v == null || v === '') url.searchParams.delete(k)
      else url.searchParams.set(k, String(v))
    }
    // Return path+search so we don't accidentally cross-origin.
    return url.pathname + (url.search ? url.search : '')
  }, [active, urlParams])

  if (dashboards.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-sm text-gray-400 px-6 text-center">
        No dashboards configured.
      </div>
    )
  }
  if (!active) return null

  return (
    <div className="h-full flex flex-col">
      {dashboards.length > 1 && (
        <div className="flex items-center gap-1 px-3 py-2 border-b border-gray-200 bg-gray-50 shrink-0 overflow-x-auto">
          {dashboards.map(d => {
            const isActive = d.id === activeId
            return (
              <button key={d.id} onClick={() => setActive(d.id)}
                className={`flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md transition-colors whitespace-nowrap ${
                  isActive ? 'bg-white shadow-sm text-gray-900' : 'text-gray-600 hover:bg-gray-100'
                }`}>
                {d.icon && <span className="text-sm">{d.icon}</span>}
                <span className="font-medium">{d.label}</span>
              </button>
            )
          })}
          <div className="flex-1" />
          <a href={srcWithParams} target="_blank" rel="noopener noreferrer"
            className="flex items-center gap-1 px-2 py-1 text-[11px] text-gray-500 hover:text-gray-700">
            <ExternalLink className="h-3 w-3" />
            Open in new tab
          </a>
        </div>
      )}
      <div className="flex-1 min-h-0 bg-white">
        <iframe
          key={srcWithParams}
          src={srcWithParams}
          title={active.label}
          className="w-full h-full border-0"
          sandbox="allow-scripts allow-same-origin allow-popups allow-forms"
        />
      </div>
    </div>
  )
}
