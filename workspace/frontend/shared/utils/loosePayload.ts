/**
 * Tolerant parsing for widget fences (```table, ```insight, ...).
 *
 * The agent is asked for JSON inside these fences, but it is an LLM and
 * it drifts — reliably, under load, in the middle of an otherwise good
 * answer. Observed live on 2026-08-19 in a single reply:
 *
 *     Invalid table data: Unexpected token '|', "| Branch | "...
 *     Invalid insight: Unexpected token 'k', "kind: insi"...
 *
 * It had emitted a markdown table and a YAML block. Both were perfectly
 * good content; only the syntax was off, and the user got a red error
 * where the analysis should have been.
 *
 * Tightening the prompt does not fix this, it only makes it rarer. The
 * renderer is the place that can be made total: parse JSON first, and
 * fall back to the two shapes the model actually reaches for.
 */

/** Markdown pipe table → { columns, rows }. Null when not a table.
 *
 *  `declared` means the caller already knows this is meant to be a table —
 *  it came out of a ```table / ```datatable fence. That removes the ambiguity
 *  the separator row exists to resolve, so a header + rows with no separator
 *  is accepted. Models emit exactly that shape often, and refusing it showed
 *  the reader "Invalid table data" in place of a correct answer. Left false
 *  (the default) when sniffing tables out of free prose, where the separator
 *  is still the only thing telling a table from a sentence with a pipe. */
export function parseMarkdownTable(
  text: string,
  opts: { declared?: boolean } = {},
): { columns: string[]; rows: string[][] } | null {
  const lines = text
    .trim()
    .split('\n')
    .map(l => l.trim())
    .filter(Boolean)
  if (lines.length < 2) return null

  // Outer pipes are optional in GitHub-flavoured markdown: a table written
  // `Ветвь | Доля` is valid and must parse. The separator row below is the
  // real discriminator, so widening this cannot swallow prose.
  const isRow = (l: string) => l.includes('|')
  // The separator row is what distinguishes a table from prose that
  // happens to contain pipes: |---|:--:|---|
  const isSeparator = (l: string) =>
    isRow(l) && /^[\s:|-]+$/.test(l) && l.includes('-')

  const cells = (l: string) => {
    let s = l.trim()
    if (s.startsWith('|')) s = s.slice(1)
    if (s.endsWith('|')) s = s.slice(0, -1)
    // \| is an escaped pipe inside a cell, not a column break.
    return s
      .split(/(?<!\\)\|/)
      .map(c => c.replace(/\\\|/g, '|').trim())
  }

  let start = lines.findIndex(isRow)
  if (start === -1) return null

  let hasSeparator = !!lines[start + 1] && isSeparator(lines[start + 1])
  if (!hasSeparator && !opts.declared) return null

  // No separator to anchor on: the header is the first line of the LONGEST run
  // of consecutive rows that all split into the same number of cells. Taking
  // the first piped line instead would make a lead-in sentence that happens to
  // contain a pipe the header, and shift every column after it.
  if (!hasSeparator) {
    let bestStart = -1
    let bestLen = 0
    let runStart = -1
    let runWidth = -1
    for (let i = 0; i <= lines.length; i++) {
      const width = i < lines.length && isRow(lines[i]) ? cells(lines[i]).length : -1
      if (width !== runWidth || width < 2) {
        const runLen = runStart === -1 ? 0 : i - runStart
        if (runWidth >= 2 && runLen > bestLen) {
          bestLen = runLen
          bestStart = runStart
        }
        runStart = width >= 2 ? i : -1
        runWidth = width
      }
    }
    // A header alone is not a table; there must be at least one data row.
    if (bestStart === -1 || bestLen < 2) return null
    start = bestStart
    lines.length = bestStart + bestLen
    hasSeparator = false
  }

  const columns = cells(lines[start])
  if (!hasSeparator && columns.length < 2) return null

  const rows: string[][] = []
  for (const line of lines.slice(start + (hasSeparator ? 2 : 1))) {
    if (!isRow(line) || isSeparator(line)) break
    const r = cells(line)
    // Pad or trim so every row matches the header width — a ragged row
    // must not shift every column after it.
    while (r.length < columns.length) r.push('')
    rows.push(r.slice(0, columns.length))
  }
  if (!columns.length || !rows.length) return null
  return { columns, rows }
}

/** Minimal `key: value` block → object. Null when it isn't one. */
/** Repair the JSON a model actually writes, before giving up on it.
 *
 *  Each of these produced "Invalid insight: could not parse" in production —
 *  a correct answer replaced by an error box because of punctuation:
 *
 *    {"body": "line one
 *    line two"}            a real newline inside a string. The most common of
 *                          the three: any body written across two lines.
 *    {"a": 1,}             a trailing comma before the closing brace.
 *    {'a': 'b'}            single quotes, the Python habit.
 *
 *  Deliberately conservative: only applied after a straight JSON.parse has
 *  already failed, and each repair is shape-checked so it cannot corrupt a
 *  payload that was merely unusual rather than malformed.
 */
function repairJson(text: string): string {
  let s = text

  // Escape raw newlines (and tabs) that sit INSIDE a string literal. Walk the
  // text tracking whether we are inside quotes, so newlines between fields are
  // left alone.
  let out = ''
  let inStr = false
  let quote = ''
  for (let i = 0; i < s.length; i++) {
    const c = s[i]
    const prev = i > 0 ? s[i - 1] : ''
    if (!inStr && (c === '"' || c === "'")) { inStr = true; quote = c; out += c; continue }
    if (inStr && c === quote && prev !== '\\') { inStr = false; out += c; continue }
    if (inStr && c === '\n') { out += '\\n'; continue }
    if (inStr && c === '\r') { continue }
    if (inStr && c === '\t') { out += '\\t'; continue }
    out += c
  }
  s = out

  // Single-quoted object → double-quoted. Only when the payload contains no
  // double-quoted string at all, so a legitimate apostrophe inside a normal
  // JSON string is never touched.
  if (!/"/.test(s) && /'/.test(s)) {
    s = s.replace(/'((?:[^'\\]|\\.)*)'/g, (_m, inner) => '"' + inner.replace(/"/g, '\\"') + '"')
  }

  // Trailing comma before a closing brace or bracket.
  s = s.replace(/,\s*([}\]])/g, '$1')

  return s
}

export function parseLooseYaml(text: string): Record<string, unknown> | null {
  const rawLines = text.trim().split('\n')
  const lines = rawLines
    .map(l => l.trimEnd())
    .filter(l => l.trim() && !l.trim().startsWith('#'))
  if (!lines.length) return null

  const out: Record<string, unknown> = {}
  for (let i = 0; i < lines.length; i++) {
    // Models bold their keys as often as not: `**headline**: Альфа`.
    const line = lines[i].trim().replace(/^\*\*([A-Za-z_][\w.-]*)\*\*(\s*:)/, '$1$2')

    // YAML block scalar — `body: |` or `body: >` followed by an indented run.
    // Without this a multi-line body made the whole block unparseable, which
    // showed the reader an error instead of the answer.
    const blockOpen = line.match(/^([A-Za-z_][\w.-]*)\s*:\s*([|>])[+-]?\s*$/)
    if (blockOpen) {
      const [, key, style] = blockOpen
      const body: string[] = []
      let j = i + 1
      for (; j < lines.length; j++) {
        if (!/^\s/.test(lines[j]) && lines[j].trim()) break   // dedented → block ends
        body.push(lines[j].replace(/^\s{1,4}/, ''))
      }
      out[key] = style === '>' ? body.join(' ').trim() : body.join('\n').trim()
      i = j - 1
      continue
    }

    const m = line.match(/^([A-Za-z_][\w.-]*)\s*:\s*(.*)$/)
    if (!m) return null // any non key: value line → not a flat block
    const [, key, raw] = m
    let v = raw.trim()
    if (
      (v.startsWith('"') && v.endsWith('"') && v.length > 1) ||
      (v.startsWith("'") && v.endsWith("'") && v.length > 1)
    ) {
      v = v.slice(1, -1)
    }
    if (v === '') continue // `key:` opening a nested block — skip the key
    if (v === 'true' || v === 'false') out[key] = v === 'true'
    else if (/^-?\d+(\.\d+)?$/.test(v)) out[key] = Number(v)
    else out[key] = v
  }
  return Object.keys(out).length ? out : null
}

/**
 * JSON first, then the fallbacks. `preferTable` also tries a markdown
 * table, returning it in the { columns, rows } shape InlineTable expects.
 */
export function parseLoosePayload(
  text: string,
  opts: { preferTable?: boolean } = {},
): unknown | null {
  const trimmed = text.trim()
  if (!trimmed) return null

  let candidate = trimmed
  if (!candidate.startsWith('{') && !candidate.startsWith('[')) {
    const m = candidate.match(/[{[][\s\S]*[}\]]/)
    if (m) candidate = m[0]
  }
  try {
    return JSON.parse(candidate)
  } catch {
    // Not valid JSON as written. Models routinely emit a real newline inside a
    // string, a trailing comma, or single quotes; repair those and retry once
    // before falling through to the YAML reader.
    try {
      return JSON.parse(repairJson(candidate))
    } catch {
      /* fall through */
    }
  }

  if (opts.preferTable) {
    // preferTable is set by the ```table / ```datatable renderers, so the
    // block is a declared table and does not need a separator row.
    const table = parseMarkdownTable(trimmed, { declared: true })
    if (table) return table
  }
  return parseLooseYaml(trimmed)
}
