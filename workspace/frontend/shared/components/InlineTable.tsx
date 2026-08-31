'use client'

import { Fragment, useCallback, useMemo, useState } from 'react'
import { parseLoosePayload } from '../utils/loosePayload'
import { useWidgetIntent } from './widgetIntent'
import { applyAliases, TABLE_ALIASES } from '../utils/normalizeBlock'

type ActionState =
  | { kind: 'idle' }
  | { kind: 'pending'; label: string }
  | { kind: 'applied'; label: string }
  | { kind: 'failed'; label: string; error: string }

/**
 * Module-level persistence for expand state + per-action click state.
 * Keyed by a hash of the table's JSON. The chat's MessageBubble
 * re-renders frequently (typing indicator, hover-to-show-actions,
 * scroll, new bot message arriving) and any of those can cause React
 * to re-mount InlineTable, wiping local useState. Persisting here
 * lets the row stay open across re-mounts. The Map only grows during
 * a session; small enough that GC isn't a concern.
 */
function djb2(s: string): number {
  let h = 5381
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h) ^ s.charCodeAt(i)
  return h >>> 0
}
const _expandedByJson = new Map<number, Set<number>>()
const _actionStateByJson = new Map<number, Record<string, ActionState>>()

interface RowAction {
  label: string
  /** Synthetic user message to dispatch via `useWidgetIntent` when
   *  clicked. Re-enters the chat as if the user typed it — keeps the
   *  agent's gates (auth, deliberation) in the loop. */
  prompt: string
  /** Optional override for the column the action mutates. The cell's
   *  text becomes `applied_value` once the action confirms. Without
   *  this, action success only hides the button — no cell update. */
  applies_to_column?: string
  applied_value?: string
}

interface RowDetails {
  /** Optional URL to the source document (e.g. invoice scan PDF).
   *  Renders as "View scan" link in the expanded panel. */
  scan_url?: string
  /** Free-form key/value pairs shown in the expanded panel.
   *  Each key/value pair gets a copy-to-clipboard button. Keys
   *  display as labels in the order given. */
  fields?: Record<string, string | number | null | undefined>
}

interface TableConfig {
  title?: string
  columns: string[]
  rows: string[][]
  /** Per-row click target. If set, rows[N] becomes clickable.
   *  - Without `row_details`: clicking navigates to row_links[N]
   *    (legacy behaviour for tables that don't ship inline detail).
   *  - With `row_details`: clicking expands the row inline; the
   *    `row_links[N]` becomes a "View in dashboard" affordance
   *    inside the expanded panel. */
  row_links?: (string | null)[]
  /** Per-row inline detail panel data. When present, rows expand
   *  inline on click instead of navigating. Each panel shows
   *  copyable key/value pairs + scan link + action buttons. */
  row_details?: (RowDetails | null)[]
  /** Per-row action buttons. Each click dispatches `{text: prompt}`
   *  via `useWidgetIntent` — synthetic user turn, keeps agent gates
   *  in the loop. */
  actions?: (RowAction[] | null)[]
}

function isNumeric(v: string): boolean {
  const s = v.trim()
  if (!s) return false
  return /^[+-]?[€$£]?[\d,]+\.?\d*%?$/.test(s)
}

function parseTableConfig(json: string): { config: TableConfig | null; error: string } {
  try {
    // JSON when the agent obeys; markdown table when it doesn't. It
    // reaches for a pipe table often enough that refusing one means
    // showing a red error in place of a correct answer.
    const parsed: any = applyAliases<any>(
      parseLoosePayload(json, { preferTable: true }), TABLE_ALIASES,
    )
    if (!parsed || typeof parsed !== 'object') {
      return { config: null, error: 'could not parse as JSON or markdown table' }
    }

    if (Array.isArray(parsed.columns) && Array.isArray(parsed.rows)) {
      const rows = parsed.rows.map((row: any) =>
        Array.isArray(row)
          ? row.map((v: any) => String(v ?? ''))
          : parsed.columns.map((c: string) => String(row?.[c] ?? '')),
      )
      const row_links = Array.isArray(parsed.row_links) ? parsed.row_links : undefined
      const row_details = Array.isArray(parsed.row_details)
        ? parsed.row_details.map((d: any) => {
            if (!d || typeof d !== 'object') return null
            return {
              scan_url: typeof d.scan_url === 'string' ? d.scan_url : undefined,
              fields: d.fields && typeof d.fields === 'object' ? d.fields : undefined,
            }
          })
        : undefined
      const actions = Array.isArray(parsed.actions)
        ? parsed.actions.map((a: any) =>
            Array.isArray(a)
              ? a
                  .filter((b: any) => b && typeof b.label === 'string' && typeof b.prompt === 'string')
                  .map((b: any) => ({
                    label: b.label,
                    prompt: b.prompt,
                    applies_to_column: typeof b.applies_to_column === 'string' ? b.applies_to_column : undefined,
                    applied_value: typeof b.applied_value === 'string' ? b.applied_value : undefined,
                  }))
              : null,
          )
        : undefined
      return {
        config: { title: parsed.title, columns: parsed.columns, rows, row_links, row_details, actions },
        error: '',
      }
    }

    const headers = parsed.headers || parsed.header
    const data = parsed.data || parsed.rows || parsed.items
    if (Array.isArray(headers) && Array.isArray(data)) {
      const rows = data.map((row: any) => {
        if (Array.isArray(row)) return row.map((v: any) => String(v ?? ''))
        if (typeof row === 'object') return headers.map((h: string) => String(row[h] ?? row[h.toLowerCase()] ?? ''))
        return [String(row)]
      })
      return { config: { title: parsed.title, columns: headers, rows }, error: '' }
    }

    if (Array.isArray(parsed.data) && parsed.data.length > 0 && typeof parsed.data[0] === 'object') {
      const columns = Object.keys(parsed.data[0])
      const rows = parsed.data.map((row: any) => columns.map(c => String(row[c] ?? '')))
      return { config: { title: parsed.title, columns, rows }, error: '' }
    }

    return { config: null, error: `unrecognized format (keys: ${Object.keys(parsed).join(', ')})` }
  } catch (e) {
    return { config: null, error: String(e).slice(0, 100) }
  }
}

function CopyableValue({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false)
  if (!value) return null
  return (
    <div className="flex items-center gap-2 py-0.5">
      <span className="text-[11px] font-medium text-gray-500 w-28 shrink-0">{label}</span>
      <span className="text-[11px] text-gray-800 font-mono flex-1 break-all">{value}</span>
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation()
          navigator.clipboard?.writeText(value).then(() => {
            setCopied(true)
            setTimeout(() => setCopied(false), 1200)
          })
        }}
        className="text-[10px] text-gray-400 hover:text-blue-700 px-1.5 py-0.5 rounded border border-gray-200 hover:border-blue-300 hover:bg-blue-50"
        title={`Copy ${label}`}
      >
        {copied ? '✓' : 'Copy'}
      </button>
    </div>
  )
}

export default function InlineTable({ json }: { json: string }) {
  const { config, error } = useMemo(() => parseTableConfig(json), [json])
  const dispatchIntent = useWidgetIntent()
  // State key: hash of the table JSON. Persists across remounts —
  // when MessageBubble re-renders for any reason (typing indicator,
  // hover, scroll, new bot message), the row stays open.
  const stateKey = useMemo(() => djb2(json), [json])
  const [expanded, setExpanded] = useState<Set<number>>(
    () => new Set(_expandedByJson.get(stateKey) ?? []),
  )
  const [actionStates, setActionStates] = useState<Record<string, ActionState>>(
    () => ({ ..._actionStateByJson.get(stateKey) ?? {} }),
  )

  if (!config) {
    return <pre className="text-xs text-red-500 bg-red-50 p-2 rounded">Invalid table data: {error}</pre>
  }

  const { title, columns, rows, row_links, row_details, actions } = config
  const hasActions = !!actions?.some(a => a && a.length > 0)
  const hasDetails = !!row_details?.some(d => d && (d.scan_url || (d.fields && Object.keys(d.fields).length > 0)))

  const toggleExpand = (ri: number) => {
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(ri)) next.delete(ri); else next.add(ri)
      _expandedByJson.set(stateKey, next)
      return next
    })
  }

  const runAction = async (ri: number, action: RowAction) => {
    const key = `${ri}:${action.label}`
    setActionStates(s => {
      const next = { ...s, [key]: { kind: 'pending' as const, label: action.label } }
      _actionStateByJson.set(stateKey, next)
      return next
    })
    try {
      const result = await dispatchIntent({
        text: action.prompt,
        source: 'action',
        meta: { row: ri, label: action.label },
      })
      setActionStates(s => {
        const newState: ActionState = result.ok
          ? { kind: 'applied', label: action.label }
          : { kind: 'failed', label: action.label, error: result.error || 'failed' }
        const next = { ...s, [key]: newState }
        _actionStateByJson.set(stateKey, next)
        return next
      })
    } catch (e: unknown) {
      setActionStates(s => {
        const next: Record<string, ActionState> = {
          ...s,
          [key]: { kind: 'failed', label: action.label, error: e instanceof Error ? e.message : 'failed' },
        }
        _actionStateByJson.set(stateKey, next)
        return next
      })
    }
  }

  const downloadCSV = useCallback(() => {
    const csvRows = [columns.join(','), ...rows.map(r => r.map(c => `"${c.replace(/"/g, '""')}"`).join(','))]
    const blob = new Blob([csvRows.join('\n')], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${(title || 'table').toLowerCase().replace(/\s+/g, '-')}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }, [title, columns, rows])

  return (
    <div className="my-3 overflow-x-auto not-prose group/table">
      <div className="flex items-center justify-between mb-1">
        {title && <div className="text-xs font-semibold text-gray-700">{title}</div>}
        <button
          onClick={downloadCSV}
          className="opacity-0 group-hover/table:opacity-100 transition-opacity text-[10px] text-gray-400 hover:text-gray-600 px-1.5 py-0.5 rounded hover:bg-gray-100"
          title="Download as CSV"
        >CSV</button>
      </div>
      <table className="w-full text-xs border-collapse">
        <thead>
          <tr className="border-b border-gray-200 bg-gray-50">
            <th className="px-2 py-1.5 w-8 text-right text-gray-400 font-normal">#</th>
            {hasDetails && <th className="px-2 py-1.5 w-6" aria-label="Expand" />}
            {columns.map((col, i) => (
              <th
                key={i}
                className="px-3 py-1.5 font-semibold text-gray-600"
                style={{ textAlign: i > 0 && rows.length > 0 && isNumeric(rows[0]?.[i] || '') ? 'right' : 'left' }}
              >
                {col}
              </th>
            ))}
            {hasActions && <th className="px-3 py-1.5 w-px" aria-label="Actions" />}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => {
            const link = row_links?.[ri] || null
            const details = row_details?.[ri] || null
            const rowActions = actions?.[ri] || null
            const isExpanded = expanded.has(ri)
            const baseClasses = `border-b border-gray-100 ${ri % 2 === 1 ? 'bg-gray-50/50' : ''}`

            // Per-row applied overrides — when an action confirms with
            // applies_to_column + applied_value, swap that cell visually.
            const cellOverrides: Record<number, string> = {}
            if (rowActions) {
              for (const action of rowActions) {
                const state = actionStates[`${ri}:${action.label}`]
                if (state?.kind === 'applied' && action.applies_to_column && action.applied_value) {
                  const col = columns.indexOf(action.applies_to_column)
                  if (col >= 0) cellOverrides[col] = action.applied_value
                }
              }
            }

            // Whole-row click toggles expand when row has details.
            // Falls back to row_links navigation when no details.
            // Otherwise inert.
            const onRowClick = details
              ? () => toggleExpand(ri)
              : link ? () => { window.location.href = link } : undefined
            const clickableClasses = onRowClick ? ' cursor-pointer hover:bg-blue-50 transition-colors' : ''

            return (
              <Fragment key={ri}>
                <tr
                  className={baseClasses + clickableClasses}
                  onClick={onRowClick}
                  role={onRowClick ? 'button' : undefined}
                  aria-expanded={details ? isExpanded : undefined}
                >
                  <td className="px-2 py-1 w-8 text-right text-[10px] text-gray-400 tabular-nums">{ri + 1}</td>
                  {hasDetails && (
                    <td className="px-2 py-1 w-6 text-center text-gray-400">
                      {details && (isExpanded ? '▾' : '▸')}
                    </td>
                  )}
                  {row.map((cell, ci) => (
                    <td
                      key={ci}
                      className="px-3 py-1.5 text-gray-800"
                      style={{ textAlign: ci > 0 && isNumeric(cell) ? 'right' : 'left' }}
                    >
                      {cellOverrides[ci] ?? cell}
                    </td>
                  ))}
                  {hasActions && (
                    <td className="px-2 py-1 whitespace-nowrap text-right" onClick={(e) => e.stopPropagation()}>
                      {rowActions && rowActions.length > 0 && (
                        <span className="inline-flex gap-1">
                          {rowActions.map((a) => {
                            const state = actionStates[`${ri}:${a.label}`]
                            if (state?.kind === 'applied') return null
                            if (state?.kind === 'pending') {
                              return (
                                <span key={a.label} className="inline-flex items-center gap-1 text-[10px] text-gray-500 px-2 py-0.5">
                                  <span className="inline-block w-2 h-2 rounded-full bg-blue-400 animate-pulse" />
                                  …
                                </span>
                              )
                            }
                            return (
                              <button
                                key={a.label}
                                type="button"
                                onClick={(e) => { e.stopPropagation(); runAction(ri, a) }}
                                className="text-[10px] font-medium text-gray-600 hover:text-blue-700 bg-white hover:bg-blue-50 border border-gray-200 hover:border-blue-300 rounded px-2 py-0.5 transition-colors"
                                title={state?.kind === 'failed' ? state.error : a.prompt}
                              >
                                {state?.kind === 'failed' ? `${a.label} (retry)` : a.label}
                              </button>
                            )
                          })}
                        </span>
                      )}
                    </td>
                  )}
                </tr>
                {details && isExpanded && (
                  <tr className="bg-blue-50/30 border-b border-gray-100">
                    <td colSpan={columns.length + 1 + (hasActions ? 1 : 0) + (hasDetails ? 1 : 0)} className="px-4 py-2">
                      <div className="space-y-0.5">
                        {details.fields && Object.entries(details.fields).map(([k, v]) =>
                          v != null && String(v).length > 0 ? (
                            <CopyableValue key={k} label={k} value={String(v)} />
                          ) : null,
                        )}
                        <div className="flex items-center gap-2 mt-2 text-[11px]">
                          {details.scan_url && (
                            <a
                              href={details.scan_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              onClick={(e) => e.stopPropagation()}
                              className="text-blue-700 hover:underline"
                            >📒 View scan</a>
                          )}
                          {link && (
                            <a
                              href={link}
                              onClick={(e) => e.stopPropagation()}
                              className="text-gray-500 hover:text-blue-700"
                            >Open in dashboard →</a>
                          )}
                        </div>
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
