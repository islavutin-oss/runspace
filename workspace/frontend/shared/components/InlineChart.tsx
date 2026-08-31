'use client'

import { useCallback, useMemo, useRef } from 'react'
import {
  BarChart, Bar, LineChart, Line, AreaChart, Area, PieChart, Pie, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend,
  ComposedChart, Radar, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis,
  Treemap, Sankey, ScatterChart, Scatter, ZAxis,
} from 'recharts'
import { parseChartConfig, type ChartConfig } from './parseChartConfig'
import { useWidgetIntent } from './widgetIntent'

export type { ChartConfig }

const DEFAULT_COLORS = [
  '#7C3AED', '#059669', '#D97706', '#DC2626', '#2563EB',
  '#EC4899', '#8B5CF6', '#14B8A6', '#F59E0B', '#6366F1',
]

// The euro used to be hardcoded here, a leftover from the first workspace this
// shipped in. Any chart of dollar figures was silently relabelled as euro.
function formatValue(v: unknown, fmt?: string, symbol = '$'): string {
  const n = Number(v)
  if (isNaN(n)) return String(v)
  if (fmt === 'currency') return `${symbol}${n.toLocaleString()}`
  if (fmt === 'percent') return `${n.toFixed(1)}%`
  return n.toLocaleString()
}

/** Navigate to a per-row href when the chart element is clicked. Used for
 *  drill-through into the dashboard tab via hash links like `#focus=1.8`. */
function navigateTo(href: unknown) {
  if (typeof href !== 'string' || !href) return
  if (href.startsWith('#')) {
    // Manually update hash — page-level click handlers (page.tsx) listen for
    // anchor clicks, but we can't synthesise an anchor here, so dispatch a
    // synthetic anchor to bubble up to the same listener.
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

function downloadCSV(data: Record<string, unknown>[], title?: string) {
  if (!data.length) return
  const keys = Object.keys(data[0])
  const rows = [keys.join(','), ...data.map(d => keys.map(k => `"${String(d[k] ?? '')}"`).join(','))]
  const blob = new Blob([rows.join('\n')], { type: 'text/csv' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${(title || 'chart-data').toLowerCase().replace(/\s+/g, '-')}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

function downloadPNG(container: HTMLElement | null, title?: string) {
  if (!container) return
  const svg = container.querySelector('.recharts-wrapper svg') as SVGSVGElement | null
  if (!svg) return
  const clone = svg.cloneNode(true) as SVGSVGElement
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
  const blob = new Blob([new XMLSerializer().serializeToString(clone)], { type: 'image/svg+xml' })
  const url = URL.createObjectURL(blob)
  const img = new Image()
  img.onload = () => {
    const canvas = document.createElement('canvas')
    canvas.width = img.width * 2
    canvas.height = img.height * 2
    const ctx = canvas.getContext('2d')!
    ctx.scale(2, 2)
    ctx.fillStyle = '#fff'
    ctx.fillRect(0, 0, img.width, img.height)
    ctx.drawImage(img, 0, 0)
    canvas.toBlob(b => {
      if (!b) return
      const a = document.createElement('a')
      a.href = URL.createObjectURL(b)
      a.download = `${(title || 'chart').toLowerCase().replace(/\s+/g, '-')}.png`
      a.click()
    })
    URL.revokeObjectURL(url)
  }
  img.src = url
}

export default function InlineChart({ json }: { json: string }) {
  const { config, error } = useMemo(() => parseChartConfig(json), [json])
  const chartRef = useRef<HTMLDivElement>(null)
  const dispatchIntent = useWidgetIntent()

  const onDataClick = useCallback(
    (payload: any) => {
      // Drill-through priority: synthetic-prompt > href.
      // Re-entering the chat keeps the agent's deliberation in the
      // loop ("show Tuesday's orders" gets normal Nova treatment),
      // whereas href is for external/dashboard navigation.
      if (config?.clickPromptKey) {
        const text = payload?.payload?.[config.clickPromptKey]
          ?? payload?.[config.clickPromptKey]
        if (typeof text === 'string' && text) {
          dispatchIntent({ text, source: 'point', meta: { chart: config.title } })
          return
        }
      }
      if (config?.clickHrefKey) {
        const href = payload?.payload?.[config.clickHrefKey] ?? payload?.[config.clickHrefKey]
        navigateTo(href)
      }
    },
    [config, dispatchIntent],
  )

  if (!config) {
    if (typeof console !== 'undefined') console.warn('[InlineChart] Parse failed:', error, json.slice(0, 200))
    return <pre className="text-xs text-red-500 bg-red-50 p-2 rounded">Invalid chart data: {error}</pre>
  }

  const { type, title, data, xKey, yKey, color, y2Key, y2Color, yFormat } = config
  const currencySymbol = config.currency || '$'
  // Deepest rank in a sankey, so terminal labels stay inside the box.
  const sankeyMaxDepth = (() => {
    const links = (config.links || []) as { source: number | string; target: number | string }[]
    if (!links.length) return 0
    const depth = new Map<string, number>()
    for (let pass = 0; pass < links.length; pass++) {
      for (const l of links) {
        const s = String(l.source)
        const tgt = String(l.target)
        const d = depth.get(s) ?? 0
        depth.set(s, d)
        depth.set(tgt, Math.max(depth.get(tgt) ?? 0, d + 1))
      }
    }
    return Math.max(...Array.from(depth.values()), 0)
  })()
  const mainColor = color || DEFAULT_COLORS[0]
  const secondColor = y2Color || DEFAULT_COLORS[1]
  const tickFormatter = (v: unknown) => formatValue(v, yFormat, currencySymbol)
  const cursor = (config.clickHrefKey || config.clickPromptKey) ? 'pointer' : 'default'

  // Heatmap — custom CSS grid (not recharts)
  if (type === 'heatmap') {
    const valueKey = config.valueKey || yKey
    const colLabels = config.columns || []
    const rowLabels = config.rows || []
    const grid: Record<string, Record<string, number>> = {}
    const cellRow: Record<string, Record<string, Record<string, unknown>>> = {}
    let maxVal = 0
    for (const d of data) {
      const col = String(d[xKey] ?? '')
      const row = String(d[yKey] ?? '')
      const v = Number(d[valueKey] ?? 0)
      if (!grid[row]) { grid[row] = {}; cellRow[row] = {} }
      grid[row][col] = v
      cellRow[row][col] = d
      if (v > maxVal) maxVal = v
    }
    const rLabels = rowLabels.length ? rowLabels : Object.keys(grid).sort()
    const cLabels = colLabels.length ? colLabels : (() => {
      const s = new Set<string>()
      for (const rv of Object.values(grid)) for (const k of Object.keys(rv)) s.add(k)
      return Array.from(s)
    })()

    const getColor = (v: number) => {
      if (v === 0) return '#f3f4f6'
      const intensity = Math.min(v / maxVal, 1)
      const base = mainColor || '#7C3AED'
      const r = parseInt(base.slice(1, 3), 16)
      const g = parseInt(base.slice(3, 5), 16)
      const b = parseInt(base.slice(5, 7), 16)
      const lr = Math.round(255 - (255 - r) * intensity)
      const lg = Math.round(255 - (255 - g) * intensity)
      const lb = Math.round(255 - (255 - b) * intensity)
      return `rgb(${lr},${lg},${lb})`
    }

    return (
      <div className="my-3 p-3 bg-white rounded-lg border border-gray-200 shadow-sm not-prose group/chart">
        <div className="flex items-center justify-between mb-2">
          {title && <div className="text-xs font-semibold text-gray-700">{title}</div>}
          <button
            onClick={() => downloadCSV(data, title)}
            className="opacity-0 group-hover/chart:opacity-100 transition-opacity text-[10px] text-gray-400 hover:text-gray-600 px-1.5 py-0.5 rounded hover:bg-gray-100"
            title="Download as CSV"
          >CSV</button>
        </div>
        <div className="overflow-x-auto">
          <div style={{ display: 'grid', gridTemplateColumns: `60px repeat(${cLabels.length}, 1fr)`, gap: '1px', fontSize: '10px' }}>
            <div />
            {cLabels.map(c => (
              <div key={c} style={{ textAlign: 'center', padding: '2px 1px', fontWeight: 600, color: '#6b7280' }}>{c}</div>
            ))}
            {rLabels.map(r => (
              <>
                <div key={`label-${r}`} style={{ padding: '4px 4px', fontWeight: 600, color: '#6b7280', display: 'flex', alignItems: 'center' }}>{r}</div>
                {cLabels.map(c => {
                  const v = grid[r]?.[c] ?? 0
                  const srcRow = cellRow[r]?.[c]
                  const clickable = v > 0 && (config.clickPromptKey || config.clickHrefKey) && srcRow
                  return (
                    <div
                      key={`${r}-${c}`}
                      title={`${r} ${c}: ${formatValue(v, yFormat, currencySymbol)}`}
                      onClick={clickable ? () => onDataClick({ payload: srcRow }) : undefined}
                      style={{
                        backgroundColor: getColor(v),
                        padding: '4px 2px',
                        textAlign: 'center',
                        borderRadius: '2px',
                        color: v > maxVal * 0.6 ? '#fff' : '#374151',
                        fontWeight: v > 0 ? 500 : 400,
                        cursor: clickable ? 'pointer' : 'default',
                      }}
                    >
                      {v > 0 ? formatValue(v, yFormat, currencySymbol) : '–'}
                    </div>
                  )
                })}
              </>
            ))}
          </div>
        </div>
      </div>
    )
  }

  // Recharts-based types
  return (
    <div ref={chartRef} className="my-3 p-3 bg-white rounded-lg border border-gray-200 shadow-sm not-prose group/chart">
      <div className="flex items-center justify-between mb-2">
        {title && <div className="text-xs font-semibold text-gray-700">{title}</div>}
        <div className="opacity-0 group-hover/chart:opacity-100 transition-opacity flex gap-1">
          <button
            onClick={() => downloadCSV(data, title)}
            className="text-[10px] text-gray-400 hover:text-gray-600 px-1.5 py-0.5 rounded hover:bg-gray-100"
            title="Download data as CSV"
          >CSV</button>
          <button
            onClick={() => downloadPNG(chartRef.current, title)}
            className="text-[10px] text-gray-400 hover:text-gray-600 px-1.5 py-0.5 rounded hover:bg-gray-100"
            title="Download chart as PNG"
          >PNG</button>
        </div>
      </div>
      <ResponsiveContainer width="100%" height={type === 'treemap' || type === 'sankey' ? 320 : 240}>
        {type === 'pie' ? (
          <PieChart>
            <Pie
              data={data}
              dataKey={yKey}
              nameKey={xKey}
              cx="50%" cy="50%"
              outerRadius={80}
              label={({ name, percent }: { name?: string | number; percent?: number }) => `${name ?? ''} ${((percent ?? 0) * 100).toFixed(0)}%`}
              onClick={onDataClick}
              style={{ cursor }}
            >
              {data.map((_, i) => (
                <Cell key={i} fill={DEFAULT_COLORS[i % DEFAULT_COLORS.length]} />
              ))}
            </Pie>
            <Tooltip formatter={(v: unknown) => formatValue(v, yFormat, currencySymbol)} />
            <Legend />
          </PieChart>
        ) : type === 'line' ? (
          <LineChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
            <XAxis dataKey={xKey} tick={{ fontSize: 11 }} />
            <YAxis tickFormatter={tickFormatter} tick={{ fontSize: 11 }} width={60} />
            <Tooltip formatter={(v: unknown) => formatValue(v, yFormat, currencySymbol)} />
            <Line type="monotone" dataKey={yKey} stroke={mainColor} strokeWidth={2} dot={{ r: 3 }} />
            {y2Key && <Line type="monotone" dataKey={y2Key} stroke={secondColor} strokeWidth={2} dot={{ r: 3 }} />}
            {(y2Key) && <Legend />}
          </LineChart>
        ) : type === 'area' ? (
          <AreaChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
            <XAxis dataKey={xKey} tick={{ fontSize: 11 }} />
            <YAxis tickFormatter={tickFormatter} tick={{ fontSize: 11 }} width={60} />
            <Tooltip formatter={(v: unknown) => formatValue(v, yFormat, currencySymbol)} />
            <Area type="monotone" dataKey={yKey} stroke={mainColor} fill={mainColor} fillOpacity={0.2} />
            {y2Key && <Area type="monotone" dataKey={y2Key} stroke={secondColor} fill={secondColor} fillOpacity={0.2} />}
          </AreaChart>
        ) : type === 'composed' ? (
          <ComposedChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
            <XAxis dataKey={xKey} tick={{ fontSize: 11 }} />
            <YAxis tickFormatter={tickFormatter} tick={{ fontSize: 11 }} width={60} />
            <Tooltip formatter={(v: unknown) => formatValue(v, yFormat, currencySymbol)} />
            <Legend />
            <Bar dataKey={yKey} fill={mainColor} radius={[4, 4, 0, 0]} onClick={onDataClick} style={{ cursor }} />
            {y2Key && <Line type="monotone" dataKey={y2Key} stroke={secondColor} strokeWidth={2} dot={{ r: 3 }} />}
          </ComposedChart>
        ) : type === 'radar' ? (
          <RadarChart data={data} cx="50%" cy="50%" outerRadius="75%">
            <PolarGrid stroke="#e5e7eb" />
            <PolarAngleAxis dataKey={xKey} tick={{ fontSize: 11 }} />
            <PolarRadiusAxis tick={{ fontSize: 10 }} />
            <Tooltip formatter={(v: unknown) => formatValue(v, yFormat, currencySymbol)} />
            <Legend
              onClick={config.legendPromptTemplate ? (e: any) => {
                const name = e?.value ?? e?.dataKey
                // The dispatcher takes a WidgetIntent, not a string; passing the
                // bare template left `intent.text` undefined at runtime.
                if (name) dispatchIntent({
                  text: config.legendPromptTemplate!.replace('{name}', String(name)),
                  source: 'action',
                  meta: { chart: config.title },
                })
              } : undefined}
              wrapperStyle={config.legendPromptTemplate ? { cursor: 'pointer' } : undefined}
            />
            {(config.radarKeys && config.radarKeys.length > 0
              ? config.radarKeys
              : [yKey, ...(y2Key ? [y2Key] : [])]
            ).map((key, i) => (
              <Radar
                key={key}
                name={key}
                dataKey={key}
                stroke={DEFAULT_COLORS[i % DEFAULT_COLORS.length]}
                fill={DEFAULT_COLORS[i % DEFAULT_COLORS.length]}
                fillOpacity={0.25}
              />
            ))}
          </RadarChart>
        ) : type === 'treemap' ? (
          <Treemap
            data={data}
            dataKey={yKey}
            nameKey={xKey}
            stroke="#fff"
            fill={mainColor}
            // recharts' Treemap `content` typing is too strict — the runtime
            // accepts a render-prop function but the TS declaration types it
            // as ReactElement only. Cast to any as the documented workaround.
            content={((props: any) => {
              const { x, y, width, height, name, value, index } = props
              const fill = DEFAULT_COLORS[(index ?? 0) % DEFAULT_COLORS.length]
              // recharts ignores `nameKey` inside a content render-prop and hands
              // back a `name` derived from the value, so every cell was labelled
              // with its own number and the categories were unreadable. Read the
              // category off the datum instead.
              const datum: any = (data as any[])?.[index] ?? {}
              const label = datum[xKey] ?? name
              const showLabel = width > 60 && height > 22
              return (
                <g style={{ cursor }} onClick={() => onDataClick(props)}>
                  <rect x={x} y={y} width={width} height={height} fill={fill} stroke="#fff" strokeWidth={2} />
                  {showLabel && (
                    <text x={x + 6} y={y + 16} fill="#fff" fontSize={11} fontWeight={600}>
                      {String(label ?? '')}
                    </text>
                  )}
                  {showLabel && height > 38 && (
                    <text x={x + 6} y={y + 30} fill="#fff" fontSize={10} opacity={0.85}>
                      {formatValue(value, yFormat, currencySymbol)}
                    </text>
                  )}
                </g>
              )
            }) as any}
          />
        ) : type === 'scatter' ? (
          <ScatterChart>
            <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
            <XAxis type="number" dataKey={xKey} name={xKey} tick={{ fontSize: 11 }}
                   tickFormatter={tickFormatter} />
            <YAxis type="number" dataKey={yKey} name={yKey} tick={{ fontSize: 11 }} width={60} />
            {config.sizeKey && <ZAxis type="number" dataKey={config.sizeKey} range={[30, 360]} />}
            <Tooltip cursor={{ strokeDasharray: '3 3' }}
                     formatter={(v: unknown) => formatValue(v, yFormat, currencySymbol)} />
            <Scatter data={data} fill={mainColor} onClick={onDataClick} style={{ cursor }}>
              {config.colorKey && data.map((d, i) => (
                <Cell key={i} fill={String(d[config.colorKey as string] ?? mainColor)} fillOpacity={0.65} />
              ))}
            </Scatter>
          </ScatterChart>
        ) : type === 'sankey' ? (
          <Sankey
            margin={{ top: 8, right: 16, bottom: 8, left: 12 }}
            data={{
              nodes: config.nodes || [],
              links: (config.links || data) as any,
            }}
            nodePadding={20}
            nodeWidth={10}
            linkCurvature={0.5}
            iterations={32}
            link={{ stroke: mainColor, strokeOpacity: 0.4 } as any}
            node={({ x, y, width, height, index, payload }: any) => {
              // Terminal nodes sit at the right edge, so a label drawn after them
              // runs off the container and the last stages of a flow lose their
              // names. recharts does not hand the node its container width, but it
              // does give a depth, so the deepest rank labels leftwards instead.
              const flip = payload?.depth != null && payload.depth >= sankeyMaxDepth
              return (
                <g>
                  <rect x={x} y={y} width={width} height={height} fill={DEFAULT_COLORS[index % DEFAULT_COLORS.length]} />
                  <text
                    x={flip ? x - 4 : x + width + 4}
                    y={y + height / 2}
                    dy={4}
                    fontSize={11}
                    fill="#374151"
                    textAnchor={flip ? 'end' : 'start'}
                  >
                    {payload?.name || ''}
                  </text>
                </g>
              )
            }}
          >
            <Tooltip />
          </Sankey>
        ) : (
          // bar (default)
          <BarChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
            <XAxis dataKey={xKey} tick={{ fontSize: 11 }} />
            <YAxis tickFormatter={tickFormatter} tick={{ fontSize: 11 }} width={60} />
            <Tooltip formatter={(v: unknown) => formatValue(v, yFormat, currencySymbol)} />
            <Bar dataKey={yKey} fill={mainColor} radius={[4, 4, 0, 0]} onClick={onDataClick} style={{ cursor }} />
            {y2Key && <Bar dataKey={y2Key} fill={secondColor} radius={[4, 4, 0, 0]} />}
            {(y2Key) && <Legend />}
          </BarChart>
        )}
      </ResponsiveContainer>
    </div>
  )
}
