/**
 * Pure chart config parser — extracted from InlineChart for testability.
 * No React dependencies.
 *
 * Handles every chart JSON format LLMs produce:
 * - Standard: {type, data, xKey, yKey}
 * - Aliases: nameKey/valueKey, labelKey/dataKey, categoryKey/amountKey
 * - Chart.js: {labels, series/datasets}
 * - Missing keys: auto-detect from data types
 * - Wrapped in text: extracts JSON from surrounding prose
 *
 * Supported chart types:
 *   bar | line | area | pie | heatmap                    (existing)
 *   composed | radar | treemap | sankey                   (added)
 *
 * Click-through: any chart with `clickHrefKey` set will navigate to
 * `data[i][clickHrefKey]` when a slice/bar/cell/node is clicked. Used to
 * deep-link from inline charts into the dashboard tab via `#focus=…`.
 */

import { applyAliases, CHART_ALIASES } from '../utils/normalizeBlock'

export type ChartType =
  | 'bar' | 'line' | 'area' | 'pie' | 'heatmap'
  | 'composed' | 'radar' | 'treemap' | 'sankey' | 'scatter'

export interface ChartConfig {
  type: ChartType
  title?: string
  data: Record<string, unknown>[]
  xKey: string
  yKey: string
  color?: string
  y2Key?: string
  y2Color?: string
  yFormat?: 'currency' | 'number' | 'percent'
  /** Symbol for `yFormat: 'currency'`. Defaults to '$'; set '€' for euro data. */
  currency?: string
  /** Heatmap: separate cell value column (defaults to yKey). */
  valueKey?: string
  columns?: string[]
  rows?: string[]
  /** Click-through: per-row href column. Common: 'href' pointing at '#focus=ID'. */
  clickHrefKey?: string
  /** Click-as-prompt: per-row column whose value is dispatched as a
   *  synthetic user message via `useWidgetIntent`. Drill-through that
   *  re-enters the chat ("show Tuesday's orders") instead of navigating.
   *  Falls back to `clickHrefKey` (if set) when no chat host is wired. */
  clickPromptKey?: string
  /** Composed-only: which series should render as a Line (rest = Bar). */
  lineKeys?: string[]
  /** Radar-only: array of metric keys to plot as overlapping shapes. */
  radarKeys?: string[]
  /** Sankey-only: nodes + links shape. If omitted, derived from data[]. */
  nodes?: { name: string }[]
  links?: { source: number | string; target: number | string; value: number }[]
  /** Scatter-only: optional bubble-size column + per-point color column. */
  sizeKey?: string
  colorKey?: string
  /** Radar-only: clicking a legend series dispatches this template with
   *  `{name}` replaced by the series key (e.g. "профиль класса {name}"). */
  legendPromptTemplate?: string
}

export interface ParseResult {
  config: ChartConfig | null
  error: string
}

const _NUMERIC_TYPES: ChartType[] = ['bar', 'line', 'area', 'pie', 'heatmap', 'composed', 'radar', 'treemap', 'sankey', 'scatter']

export function parseChartConfig(json: string): ParseResult {
  try {
    let text = json.trim()
    if (!text.startsWith('{')) {
      const match = text.match(/\{[\s\S]*\}/)
      if (match) text = match[0]
    }
    // Key aliases first — `rows` for `data`, `x`/`y`, `chart_type`. Applied
    // before the labels/series conversions below, which rely on the
    // canonical names being in place. `series` is deliberately not an
    // alias for `data`: the {labels, series} branch converts it itself.
    const parsed = applyAliases<any>(JSON.parse(text), CHART_ALIASES)

    // Normalize Chart.js-style format (labels + series)
    if (!parsed.data && parsed.labels && parsed.series) {
      const labels = parsed.labels as string[]
      const series = parsed.series as { name: string; values: number[] }[]
      const yKey = series[0]?.name?.toLowerCase().replace(/\s+/g, '_') || 'value'
      parsed.data = labels.map((label: string, i: number) => {
        const row: Record<string, unknown> = { label }
        for (const s of series) {
          const key = (s.name || `series_${i}`).toLowerCase().replace(/\s+/g, '_')
          row[key] = s.values[i] ?? 0
        }
        return row
      })
      if (!parsed.xKey) parsed.xKey = 'label'
      if (!parsed.yKey) parsed.yKey = yKey
      if (series.length > 1) parsed.y2Key = parsed.y2Key || (series[1].name || 'series_1').toLowerCase().replace(/\s+/g, '_')
    }

    // Also handle flat {labels, datasets}
    if (!parsed.data && parsed.labels && parsed.datasets) {
      const labels = parsed.labels as string[]
      const datasets = parsed.datasets as { label: string; data: number[] }[]
      const yKey = datasets[0]?.label?.toLowerCase().replace(/\s+/g, '_') || 'value'
      parsed.data = labels.map((label: string, i: number) => {
        const row: Record<string, unknown> = { label }
        for (const ds of datasets) {
          const key = (ds.label || `dataset_${i}`).toLowerCase().replace(/\s+/g, '_')
          row[key] = ds.data[i] ?? 0
        }
        return row
      })
      if (!parsed.xKey) parsed.xKey = 'label'
      if (!parsed.yKey) parsed.yKey = yKey
      if (datasets.length > 1) parsed.y2Key = parsed.y2Key || (datasets[1].label || 'dataset_1').toLowerCase().replace(/\s+/g, '_')
    }

    // Sankey: data may be expressed as {nodes, links}; data[] becomes a copy of
    // links. An empty `data: []` alongside them counts as absent — a model that
    // supplies the flow correctly and adds an empty array for shape should not
    // be told its chart has no data.
    if (
      parsed.type === 'sankey' &&
      parsed.nodes &&
      parsed.links &&
      (!parsed.data || (Array.isArray(parsed.data) && parsed.data.length === 0))
    ) {
      parsed.data = parsed.links
    }

    // Treemap: data may be hierarchical {name, children: [...]} — wrap so data[] shape is satisfied.
    if (parsed.type === 'treemap' && parsed.data && !Array.isArray(parsed.data)) {
      parsed.data = [parsed.data]
    }

    if (!parsed.data || !Array.isArray(parsed.data)) {
      return { config: null, error: `missing data array (keys: ${Object.keys(parsed).join(', ')})` }
    }
    if (parsed.data.length === 0) return { config: null, error: 'empty data array' }

    if (!parsed.xKey) parsed.xKey = parsed.nameKey || parsed.labelKey || parsed.categoryKey || parsed.category
    if (!parsed.yKey) parsed.yKey = parsed.valueKey || parsed.dataKey || parsed.amountKey || parsed.metricKey

    // Auto-detect from first row if still missing.
    if (!parsed.xKey || !parsed.yKey) {
      const first = parsed.data[0]
      if (!first || typeof first !== 'object') return { config: null, error: 'invalid data[0]' }
      const keys = Object.keys(first)
      if (keys.length === 0) return { config: null, error: 'empty data[0]' }
      const stringKey = keys.find(k => typeof first[k] === 'string') || keys[0]
      const numberKey = keys.find(k => k !== stringKey && typeof first[k] === 'number') || keys.find(k => k !== stringKey) || keys[1]
      if (!parsed.xKey) parsed.xKey = stringKey
      if (!parsed.yKey) parsed.yKey = numberKey
    }

    if (!parsed.xKey || !parsed.yKey) return { config: null, error: 'could not determine xKey/yKey' }

    if (!_NUMERIC_TYPES.includes(parsed.type)) {
      // Default to bar if type is unknown — keeps LLM mistakes recoverable.
      parsed.type = 'bar'
    }

    return { config: parsed as ChartConfig, error: '' }
  } catch (e) {
    return { config: null, error: String(e).slice(0, 100) }
  }
}
