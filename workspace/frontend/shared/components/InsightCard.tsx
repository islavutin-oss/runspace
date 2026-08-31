'use client'

/**
 * Insight callout — Linear/Notion-style banner used in chat to highlight a
 * standout finding (top concentration, anomaly, recommendation, risk).
 *
 * Rendered from a fenced ```insight code block via MarkdownContent.
 * JSON schema:
 *   { kind?: 'insight'|'warning'|'opportunity'|'risk',
 *     headline: string,
 *     body?: string,         // 1-2 sentences
 *     cta?: { label, href }  // optional button
 *   }
 *
 * Multiple cards in one block are stacked.
 */

import { parseLoosePayload } from '../utils/loosePayload'
import { Lightbulb, AlertTriangle, TrendingUp, ShieldAlert } from 'lucide-react'
import { applyAliasesAll, INSIGHT_ALIASES } from '../utils/normalizeBlock'

export type InsightKind = 'insight' | 'warning' | 'opportunity' | 'risk'

export interface InsightSpec {
  kind?: InsightKind
  headline: string
  body?: string
  cta?: { label: string; href: string }
}

const STYLE: Record<InsightKind, { Icon: any; accent: string; bg: string; iconBg: string; iconColor: string }> = {
  insight:     { Icon: Lightbulb,    accent: '#6366F1', bg: '#EEF2FF', iconBg: '#6366F1', iconColor: '#fff' },
  warning:     { Icon: AlertTriangle, accent: '#D97706', bg: '#FEF3C7', iconBg: '#D97706', iconColor: '#fff' },
  opportunity: { Icon: TrendingUp,    accent: '#059669', bg: '#D1FAE5', iconBg: '#059669', iconColor: '#fff' },
  risk:        { Icon: ShieldAlert,   accent: '#DC2626', bg: '#FEE2E2', iconBg: '#DC2626', iconColor: '#fff' },
}

function navigateTo(href: string) {
  if (!href) return
  if (href.startsWith('#')) {
    const a = document.createElement('a')
    a.href = href
    a.style.display = 'none'
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
  } else {
    window.location.assign(href)
  }
}

function CardOne({ spec }: { spec: InsightSpec }) {
  const kind = (spec.kind || 'insight') as InsightKind
  const style = STYLE[kind] || STYLE.insight
  const Icon = style.Icon
  return (
    <div className="my-2 not-prose flex gap-3 rounded-lg overflow-hidden border border-gray-200"
         style={{ borderLeftColor: style.accent, borderLeftWidth: 3, background: style.bg }}>
      <div className="flex items-start justify-center pl-3 pt-3">
        <span className="inline-flex items-center justify-center rounded-md w-7 h-7"
              style={{ background: style.iconBg, color: style.iconColor }}>
          <Icon className="w-4 h-4" />
        </span>
      </div>
      <div className="flex-1 py-3 pr-3">
        <div className="text-[13px] font-semibold text-gray-900 leading-snug">{spec.headline}</div>
        {spec.body && <div className="text-[12px] text-gray-700 mt-1 leading-relaxed">{spec.body}</div>}
        {spec.cta && (
          <button
            onClick={() => navigateTo(spec.cta!.href)}
            className="mt-2 inline-flex items-center text-[11px] font-medium px-2.5 py-1 rounded-md text-white"
            style={{ background: style.accent }}
          >
            {spec.cta.label}
          </button>
        )}
      </div>
    </div>
  )
}

export default function InsightCard({ json }: { json: string }) {
  // JSON when the agent obeys; a flat `kind: insight` YAML block when it
  // doesn't — which is what it emitted live on 2026-08-19.
  const raw = parseLoosePayload(json)
  if (!raw || typeof raw !== 'object') {
    return <pre className="text-xs text-red-500 bg-red-50 p-2 rounded">Invalid insight: could not parse</pre>
  }
  // Models write `title`/`text` as often as `headline`/`body`. The fence has
  // already declared this is an insight, so accept the synonyms rather than
  // showing a schema complaint where the answer should be.
  const list: InsightSpec[] = applyAliasesAll<InsightSpec>(raw, INSIGHT_ALIASES)

  // Parsed, but not an insight. A model that emits the kpi shape here — or
  // any other object — used to render a silent empty card, which looks like
  // a layout bug rather than a malformed payload.
  const usable = list.filter((s) => s && typeof s.headline === 'string' && s.headline.trim())
  if (!usable.length) {
    const keys = [...new Set(list.flatMap((s) => (s && typeof s === 'object' ? Object.keys(s) : [])))]
    return (
      <pre className="text-xs text-amber-700 bg-amber-50 p-2 rounded whitespace-pre-wrap">
        {`Insight block is missing "headline"${keys.length ? ` — got: ${keys.join(', ')}` : ''}`}
      </pre>
    )
  }
  return <>{usable.map((s, i) => <CardOne key={i} spec={s} />)}</>
}
