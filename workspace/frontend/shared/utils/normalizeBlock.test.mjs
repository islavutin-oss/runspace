/**
 * Every MCP-UI block, against the shapes models actually emit.
 *
 * Each of these was a live red error in a user's chat before it was a test:
 *   ```insight {"kind","title","text"}   → 'missing "headline"'   (2026-08-30)
 *   ```datatable with no |---| separator → 'could not parse'      (2026-08-30)
 * The pattern repeats per block, so this suite covers all of them at once
 * rather than waiting for each to surface in production in turn.
 */
import assert from 'node:assert/strict'
import {
  applyAliases, applyAliasesAll,
  INSIGHT_ALIASES, KPI_ALIASES, TABLE_ALIASES,
  CHART_ALIASES, FILE_ALIASES, FORM_ALIASES,
} from './normalizeBlock.ts'
import { parseLoosePayload } from './loosePayload.ts'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
const __dir = path.dirname(fileURLToPath(import.meta.url))
const src = f => fs.readFileSync(path.join(__dir, '..', 'components', f), 'utf-8')

let pass = 0
const t = (name, fn) => {
  try { fn(); pass++; console.log('  ok  ' + name) }
  catch (e) { console.log('  FAIL ' + name + ' -> ' + e.message); process.exitCode = 1 }
}

// ── the normaliser itself ──────────────────────────────────────────────
t('canonical key always wins over an alias', () => {
  const r = applyAliases({ headline: 'real', title: 'other' }, INSIGHT_ALIASES)
  assert.equal(r.headline, 'real')
})
t('alias fills only an absent key', () => {
  const r = applyAliases({ title: 'from alias' }, INSIGHT_ALIASES)
  assert.equal(r.headline, 'from alias')
})
t('empty string counts as absent', () => {
  const r = applyAliases({ headline: '', title: 'used' }, INSIGHT_ALIASES)
  assert.equal(r.headline, 'used')
})
t('snake_case, camelCase and spaced keys are the same key', () => {
  for (const k of ['submit_label', 'submitLabel', 'Submit Label']) {
    const r = applyAliases({ fields: [], [k]: 'Send' }, FORM_ALIASES)
    assert.equal(r.submitLabel, 'Send', k)
  }
})
t('non-objects pass through untouched', () => {
  assert.equal(applyAliases(null, INSIGHT_ALIASES), null)
  assert.equal(applyAliases('text', INSIGHT_ALIASES), 'text')
})
t('unknown keys are preserved, not dropped', () => {
  const r = applyAliases({ title: 'x', custom: 42 }, INSIGHT_ALIASES)
  assert.equal(r.custom, 42)
})

// ── insight — the exact payload that failed live ───────────────────────
t('insight: {kind,title,text} — the 2026-08-30 failure', () => {
  const [r] = applyAliasesAll(
    { kind: 'insight', title: 'Альфа — 8.48%', text: 'Крупнейшая ветвь.' },
    INSIGHT_ALIASES)
  assert.equal(r.headline, 'Альфа — 8.48%')
  assert.equal(r.body, 'Крупнейшая ветвь.')
  assert.equal(r.kind, 'insight')
})
t('insight: heading/description also accepted', () => {
  const [r] = applyAliasesAll({ heading: 'H', description: 'B' }, INSIGHT_ALIASES)
  assert.equal(r.headline, 'H'); assert.equal(r.body, 'B')
})
t('insight: an array of blocks all normalise', () => {
  const r = applyAliasesAll([{ title: 'a' }, { title: 'b' }], INSIGHT_ALIASES)
  assert.deepEqual(r.map(x => x.headline), ['a', 'b'])
})

// ── kpi ────────────────────────────────────────────────────────────────
t('kpi: {label,value} normalises to the title KPICard reads', () => {
  // KPICard reads `title`. This assertion used to run the other way — asserting
  // `label` — so it passed while a block written `label:` rendered a blank card.
  const [r] = applyAliasesAll({ label: 'Listed', value: '396' }, KPI_ALIASES)
  assert.equal(r.title, 'Listed'); assert.equal(r.value, '396')
})
t('kpi: caption/hint become subtitle', () => {
  const [r] = applyAliasesAll({ title: 'L', value: 1, hint: 'note' }, KPI_ALIASES)
  assert.equal(r.subtitle, 'note')
})
t('kpi: a flat YAML block parses before normalising', () => {
  const [r] = applyAliasesAll(parseLoosePayload('label: Listed\nvalue: 396'), KPI_ALIASES)
  assert.equal(r.title, 'Listed')
})
t('every alias table targets fields the component actually reads', () => {
  // The bug this catches: KPI_ALIASES named `label` canonical while KPISpec
  // declares `title`, so the alias filled a key nothing rendered. Tie the
  // canonical names to the interfaces.
  const iface = (file, name) => {
    const src = fs.readFileSync(path.join(__dir, '..', 'components', file), 'utf-8')
    const m = src.match(new RegExp(`interface ${name} \\{([^}]*)\\}`, 's'))
    assert.ok(m, `${name} not found in ${file}`)
    return new Set([...m[1].matchAll(/^\s*(\w+)\??:/gm)].map(x => x[1]))
  }
  for (const [table, file, name] of [
    [KPI_ALIASES, 'KPIBlock.tsx', 'KPISpec'],
    [INSIGHT_ALIASES, 'InsightCard.tsx', 'InsightSpec'],
  ]) {
    const fields = iface(file, name)
    for (const target of Object.keys(table)) {
      assert.ok(fields.has(target),
        `${name} has no field "${target}" — the alias fills a key nothing reads`)
    }
  }
})

// ── datatable ──────────────────────────────────────────────────────────
t('table: headers/data normalise to columns/rows', () => {
  const r = applyAliases({ headers: ['A', 'B'], data: [['1', '2']] }, TABLE_ALIASES)
  assert.deepEqual(r.columns, ['A', 'B']); assert.deepEqual(r.rows, [['1', '2']])
})
t('table: the separator-less markdown case still parses', () => {
  const r = parseLoosePayload('A | B\n1 | 2', { preferTable: true })
  assert.ok(r && r.columns.length === 2 && r.rows.length === 1)
})

// ── chart ──────────────────────────────────────────────────────────────
t('chart: x/y and chart_type normalise', () => {
  const r = applyAliases({ chart_type: 'bar', rows: [{ a: 1 }], x: 'a', y: 'b' }, CHART_ALIASES)
  assert.equal(r.type, 'bar'); assert.equal(r.xKey, 'a')
  assert.equal(r.yKey, 'b');   assert.deepEqual(r.data, [{ a: 1 }])
})
t('chart: `series` is NOT aliased to data', () => {
  // parseChartConfig converts {labels, series} itself; aliasing would hand it
  // an array of series objects and silently break that path.
  const r = applyAliases({ labels: ['x'], series: [{ name: 'n', values: [1] }] }, CHART_ALIASES)
  assert.equal(r.data, undefined)
  assert.ok(Array.isArray(r.series))
})

// ── file ───────────────────────────────────────────────────────────────
t('file: href/filename normalise to url/name', () => {
  const r = applyAliases({ filename: 'a.csv', href: '/files/a.csv' }, FILE_ALIASES)
  assert.equal(r.name, 'a.csv'); assert.equal(r.url, '/files/a.csv')
})
t('file: link and download_url also work', () => {
  assert.equal(applyAliases({ name: 'a', link: '/x' }, FILE_ALIASES).url, '/x')
  assert.equal(applyAliases({ name: 'a', download_url: '/y' }, FILE_ALIASES).url, '/y')
})

// ── form ───────────────────────────────────────────────────────────────
t('form: inputs/questions normalise to fields', () => {
  assert.ok(applyAliases({ inputs: [{ key: 'e' }] }, FORM_ALIASES).fields)
  assert.ok(applyAliases({ questions: [{ key: 'e' }] }, FORM_ALIASES).fields)
})
t('form: button/cta normalise to submitLabel', () => {
  assert.equal(applyAliases({ fields: [], button: 'Go' }, FORM_ALIASES).submitLabel, 'Go')
})

// ── the alias tables must not collide ──────────────────────────────────
t('no alias table maps one source key to two targets', () => {
  for (const [name, table] of Object.entries({
    INSIGHT_ALIASES, KPI_ALIASES, TABLE_ALIASES, CHART_ALIASES, FILE_ALIASES, FORM_ALIASES })) {
    const seen = new Map()
    for (const [target, alts] of Object.entries(table)) {
      for (const a of alts) {
        assert.ok(!seen.has(a),
          `${name}: "${a}" maps to both ${seen.get(a)} and ${target}`)
        seen.set(a, target)
      }
    }
  }
})


// ── Wiring: an alias table that no renderer applies is decoration ───────
// These caught a real gap: CHART_ALIASES existed and its unit tests passed
// while parseChartConfig never called applyAliases, so `{rows}` still failed
// in production. Assert the source actually wires each table up.

for (const [file, table] of [
  ['InsightCard.tsx', 'INSIGHT_ALIASES'],
  ['KPIBlock.tsx', 'KPI_ALIASES'],
  ['InlineTable.tsx', 'TABLE_ALIASES'],
  ['InlineFile.tsx', 'FILE_ALIASES'],
  ['InlineForm.tsx', 'FORM_ALIASES'],
  ['parseChartConfig.ts', 'CHART_ALIASES'],
]) {
  t(`${file} imports and applies ${table}`, () => {
    const s = src(file)
    assert.ok(s.includes(table), `${file} does not reference ${table}`)
    assert.ok(/applyAliases(All)?\s*[<(]/.test(s), `${file} never calls applyAliases`)
  })
}

// kpi, file and form used bare JSON.parse, so a flat-YAML block errored where
// the other renderers accepted it.
for (const file of ['KPIBlock.tsx', 'InlineFile.tsx', 'InlineForm.tsx']) {
  t(`${file} parses loose payloads, not strict JSON only`, () => {
    const s = src(file)
    assert.ok(s.includes('parseLoosePayload'), `${file} still JSON.parse-only`)
  })
}


// ── Every shipped .ts/.tsx must actually parse ──────────────────────────
// A split multi-line import (`import {` immediately followed by another
// `import`) is syntactically valid to grep but fails the build. That shipped
// once, inside a published wheel, because the source was never compiled
// between edit and release.
t('no source file has a split import block', () => {
  const root = path.join(__dir, '..')
  const walk = d => fs.readdirSync(d, { withFileTypes: true }).flatMap(e => {
    const f = path.join(d, e.name)
    if (e.isDirectory()) return e.name === 'node_modules' ? [] : walk(f)
    return /\.tsx?$/.test(e.name) ? [f] : []
  })
  const broken = []
  for (const f of walk(root)) {
    const lines = fs.readFileSync(f, 'utf-8').split('\n')
    for (let i = 1; i < lines.length; i++) {
      if (/^import \{$/.test(lines[i - 1]) && /^import /.test(lines[i])) {
        broken.push(`${path.relative(root, f)}:${i + 1}`)
      }
    }
  }
  assert.deepEqual(broken, [], 'split import blocks: ' + broken.join(', '))
})

console.log(`\n${pass} passed`)
if (process.exitCode) { console.log('SUITE FAILED'); }
