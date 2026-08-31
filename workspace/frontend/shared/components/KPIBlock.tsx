'use client'

/**
 * Markdown bridge for KPICard — render one or more KPI cards from a fenced
 * ```kpi block. Single-card or array. Handles a small set of named lucide
 * icons via string lookup so the JSON stays simple for the LLM.
 */

import KPICard from './KPICard'
import {
  Activity, BarChart3, Boxes, Clock, DollarSign, Layers, LineChart,
  Package, PieChart, ShoppingBag, Target, TrendingDown, TrendingUp, Users,
  Wine, type LucideIcon,
} from 'lucide-react'
import { parseLoosePayload } from '../utils/loosePayload'
import { applyAliasesAll, KPI_ALIASES } from '../utils/normalizeBlock'

const ICONS: Record<string, LucideIcon> = {
  activity: Activity, barchart: BarChart3, boxes: Boxes, clock: Clock,
  dollar: DollarSign, layers: Layers, line: LineChart, package: Package,
  pie: PieChart, shop: ShoppingBag, target: Target,
  down: TrendingDown, up: TrendingUp, users: Users, wine: Wine,
}

export interface KPISpec {
  title: string
  value: string
  subtitle?: string
  /** lookup key (default 'activity'). Pass any of: activity, barchart, boxes,
   *  clock, dollar, layers, line, package, pie, shop, target, down, up, users, wine. */
  icon?: string
  /** Optional accent color → gradient background. */
  color?: string
  trend?: { value: number; label: string }
}

export default function KPIBlock({ json }: { json: string }) {
  // Loose payload, not strict JSON: a kpi block written as flat YAML is still
  // a kpi block, and `title`/`value` is as common from a model as
  // `label`/`value`.
  const raw = parseLoosePayload(json)
  if (!raw || typeof raw !== 'object') {
    return <pre className="text-xs text-red-500 bg-red-50 p-2 rounded">Invalid kpi block: could not parse</pre>
  }
  const list: KPISpec[] = applyAliasesAll<KPISpec>(raw, KPI_ALIASES)
    .filter(k => k && (k.title !== undefined || k.value !== undefined))
  if (!list.length) {
    const keys = [...new Set((Array.isArray(raw) ? raw : [raw]).flatMap(
      (k: any) => (k && typeof k === 'object' ? Object.keys(k) : [])))]
    return (
      <pre className="text-xs text-amber-700 bg-amber-50 p-2 rounded whitespace-pre-wrap">
        {`KPI block needs "title" and "value"${keys.length ? ` — got: ${keys.join(', ')}` : ''}`}
      </pre>
    )
  }
  // Auto-grid: 1→one column, 2→two, 3→three, 4+→4 across.
  const cols = Math.min(4, Math.max(1, list.length))
  return (
      <div
        className="my-3 not-prose grid gap-3"
        style={{
          // `auto-fit` drops to two columns on a phone rather than squeezing
          // four 85px tracks that wrap every label onto three lines.
          gridTemplateColumns: `repeat(auto-fit, minmax(min(100%, 150px), 1fr))`,
          maxWidth: cols < 4 ? `${cols * 260}px` : undefined,
        }}
      >
      {list.map((s, i) => {
        const Icon = ICONS[(s.icon || 'activity').toLowerCase()] || Activity
        return (
          <KPICard
            key={i}
            title={s.title}
            value={s.value}
            subtitle={s.subtitle}
            icon={Icon}
            color={s.color}
            trend={s.trend}
          />
        )
      })}
    </div>
  )
}
