/**
 * Key normalisation for MCP-UI blocks.
 *
 * Every fenced block (```insight, ```kpi, ```datatable, ```chart, ```file,
 * ```form) is written by a model, and models reach for synonyms. Live examples
 * that reached users as a red error instead of an answer:
 *
 *   2026-08-30  ```insight  {"kind","title","text"}  → "missing headline"
 *   2026-08-30  ```datatable with no |---| separator → "could not parse"
 *
 * The fence already declares intent. Refusing `title` where we wanted
 * `headline` does not protect the reader from anything — it just hides a
 * correct answer behind a schema complaint. So accept the obvious synonyms and
 * render.
 *
 * This is deliberately NOT a general "guess what they meant": each block
 * declares its own small alias table, aliases only fill a key that is absent,
 * and the canonical name always wins.
 */

export type AliasTable = Record<string, string[]>

/** Fill canonical keys from their aliases. Non-destructive: a canonical key
 *  that is already present is never overwritten. */
export function applyAliases<T extends Record<string, any>>(
  input: unknown,
  aliases: AliasTable,
): T {
  if (!input || typeof input !== 'object' || Array.isArray(input)) return input as T
  const obj: Record<string, any> = { ...(input as Record<string, any>) }

  // Case- and separator-insensitive lookup: submit_label, submitLabel and
  // "Submit Label" are the same key as far as a model is concerned.
  const canon = (k: string) => k.toLowerCase().replace(/[\s_-]/g, '')
  const byCanon = new Map<string, string>()
  for (const k of Object.keys(obj)) if (!byCanon.has(canon(k))) byCanon.set(canon(k), k)

  for (const [target, alts] of Object.entries(aliases)) {
    const present = obj[target] !== undefined && obj[target] !== null && obj[target] !== ''
    if (present) continue
    for (const alt of [target, ...alts]) {
      const actual = byCanon.get(canon(alt))
      if (actual === undefined) continue
      const v = obj[actual]
      if (v === undefined || v === null || v === '') continue
      obj[target] = v
      break
    }
  }
  return obj as T
}

/** Same, mapped over an array of blocks. */
export function applyAliasesAll<T extends Record<string, any>>(
  input: unknown,
  aliases: AliasTable,
): T[] {
  const list = Array.isArray(input) ? input : [input]
  return list.map(item => applyAliases<T>(item, aliases))
}

export const INSIGHT_ALIASES: AliasTable = {
  headline: ['title', 'heading', 'header', 'summary', 'label', 'name'],
  body:     ['text', 'detail', 'details', 'description', 'content', 'subtitle', 'note'],
  kind:     ['type', 'severity', 'variant'],
}

// KPICard reads `title` / `value` / `subtitle`. Getting this backwards — with
// `label` as the canonical name — meant a block written `label:` filled a key
// nothing reads and the card rendered blank. Canonical names must match the
// component, not read well in a table.
export const KPI_ALIASES: AliasTable = {
  title:    ['label', 'name', 'metric', 'heading', 'headline'],
  value:    ['val', 'amount', 'number', 'figure', 'count'],
  subtitle: ['caption', 'hint', 'sub', 'note', 'detail', 'description'],
}

export const TABLE_ALIASES: AliasTable = {
  columns: ['headers', 'header', 'cols', 'column', 'fields'],
  rows:    ['data', 'values', 'items', 'records'],
  title:   ['heading', 'headline', 'caption', 'name'],
}

export const CHART_ALIASES: AliasTable = {
  type:  ['chart_type', 'chartType', 'kind', 'variant'],
  // NOTE: `series` is deliberately absent. parseChartConfig converts a
  // {labels, series} payload into rows itself; aliasing series→data would
  // hand it an array of series objects and silently break that path.
  data:  ['rows', 'values', 'points', 'items'],
  xKey:  ['x', 'x_key', 'xField', 'xAxis', 'category', 'label'],
  yKey:  ['y', 'y_key', 'yField', 'yAxis', 'value'],
  title: ['heading', 'headline', 'caption', 'name'],
}

export const FILE_ALIASES: AliasTable = {
  name: ['filename', 'file_name', 'title', 'label'],
  url:  ['href', 'link', 'path', 'src', 'download_url'],
  kind: ['type', 'format', 'mime', 'extension'],
  size: ['bytes', 'size_bytes', 'filesize', 'length'],
}

export const FORM_ALIASES: AliasTable = {
  fields:      ['inputs', 'questions', 'items', 'controls'],
  title:       ['heading', 'headline', 'name'],
  body:        ['text', 'description', 'detail', 'intro'],
  submitLabel: ['submit_label', 'submit', 'button', 'buttonLabel', 'cta'],
  prompt:      ['message', 'template'],
  done:        ['success', 'confirmation', 'thanks'],
}
