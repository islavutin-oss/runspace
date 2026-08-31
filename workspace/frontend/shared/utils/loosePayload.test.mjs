import assert from 'node:assert/strict'
import { parseMarkdownTable, parseLooseYaml, parseLoosePayload } from './loosePayload.ts'

let pass = 0
const t = (name, fn) => { try { fn(); pass++; console.log('  ok  ' + name) } catch (e) { console.log('  FAIL ' + name + ' -> ' + e.message); process.exitCode = 1 } }

// The exact payload from the live failure screenshot.
const MD = `| Branch | Share of revenue | Kind |
|---|---:|---|
| Alcohol-free wine | 4.2% | category |
| Gin | 3.1% | category |
| PREMIUM | 2.8% | price segment |`

t('markdown table parses', () => {
  const r = parseMarkdownTable(MD)
  assert.deepEqual(r.columns, ['Branch', 'Share of revenue', 'Kind'])
  assert.equal(r.rows.length, 3)
  assert.deepEqual(r.rows[2], ['PREMIUM', '2.8%', 'price segment'])
})

t('the failing table payload no longer errors', () => {
  const r = parseLoosePayload(MD, { preferTable: true })
  assert.ok(r && r.columns && r.rows, 'should yield columns+rows')
})

t('prose with pipes is NOT a table', () => {
  assert.equal(parseMarkdownTable('a | b\nc | d'), null)
  assert.equal(parseMarkdownTable('| not a table |\n| still not |'), null)
})

t('ragged rows are padded, not shifted', () => {
  const r = parseMarkdownTable('| a | b | c |\n|---|---|---|\n| 1 | 2 |')
  assert.deepEqual(r.rows[0], ['1', '2', ''])
})

// The exact insight payload from the screenshot.
const YAML = `kind: insight
headline: Pure branches drive 12% of revenue
body: Five of 25 branches are price segments, not categories.
severity: 2
actionable: true`

t('loose yaml parses', () => {
  const r = parseLooseYaml(YAML)
  assert.equal(r.kind, 'insight')
  assert.equal(r.severity, 2)          // number coercion
  assert.equal(r.actionable, true)     // bool coercion
  assert.ok(r.headline.startsWith('Pure'))
})

t('the failing insight payload no longer errors', () => {
  const r = parseLoosePayload(YAML)
  assert.ok(r && r.kind === 'insight')
})

t('JSON still wins and is unchanged', () => {
  assert.deepEqual(parseLoosePayload('{"a":1}'), { a: 1 })
  assert.deepEqual(parseLoosePayload('[{"a":1}]'), [{ a: 1 }])
  // JSON embedded in chatter
  assert.deepEqual(parseLoosePayload('here:\n{"a":1}\nthanks'), { a: 1 })
})

t('quoted yaml values are unquoted', () => {
  assert.equal(parseLooseYaml('a: "x: y"').a, 'x: y')
})

t('prose is rejected rather than mangled', () => {
  assert.equal(parseLoosePayload('just a sentence'), null)
  assert.equal(parseLoosePayload(''), null)
})


// ── Regression: homogeneous-branch table, non-ASCII headers (2026-08-30) ───
// Reported as "Invalid table data: could not parse as JSON or markdown table".
// The table is valid GFM but omits the outer pipes, which the old isRow()
// required on BOTH ends.

t('table without outer pipes parses', () => {
  const r = parseMarkdownTable(
    ['Ветвь | Доля | Тип',
     '--- | ---: | ---',
     'Первая ветвь | 4.2% | категория',
     'Альфа | 3.1% | категория'].join('\n'),
  )
  assert.deepEqual(r.columns, ['Ветвь', 'Доля', 'Тип'])
  assert.equal(r.rows.length, 2)
  assert.deepEqual(r.rows[0], ['Первая ветвь', '4.2%', 'категория'])
})

t('leading pipe only parses', () => {
  const r = parseMarkdownTable('| A | B\n|---|---\n| 1 | 2')
  assert.deepEqual(r.columns, ['A', 'B'])
  assert.deepEqual(r.rows, [['1', '2']])
})

t('trailing pipe only parses', () => {
  const r = parseMarkdownTable('A | B |\n---|---|\n1 | 2 |')
  assert.deepEqual(r.columns, ['A', 'B'])
  assert.deepEqual(r.rows, [['1', '2']])
})

t('bare-pipe table still needs a separator row', () => {
  // Without this, any prose line containing a pipe would start a table.
  assert.equal(parseMarkdownTable('a | b\nc | d'), null)
})

t('separator alone is not a table', () => {
  assert.equal(parseMarkdownTable('--- | ---\n--- | ---'), null)
})


// ── Regression: A ```datatable with no separator row (2026-08-30) ────
// Captured verbatim from prod: POST /api/workspace/chat, "Найди 100%

const SEPARATORLESS = `Координата | Признак | Код | Доля
L-6.2 | Альфа | 23 | 8.48%
L-7.2 | Без алкоголя | 33 | 5.96%
L-12.1 | ВЦС | 3 | 5.46%`

t("A separator-less datatable parses when declared", () => {
  const r = parseMarkdownTable(SEPARATORLESS, { declared: true })
  assert.deepEqual(r.columns, ['Координата', 'Признак', 'Код', 'Доля'])
  assert.equal(r.rows.length, 3)
  assert.deepEqual(r.rows[0], ['L-6.2', 'Альфа', '23', '8.48%'])
  assert.deepEqual(r.rows[2], ['L-12.1', 'ВЦС', '3', '5.46%'])
})

t('the same payload reaches the table renderer through parseLoosePayload', () => {
  // This is the path InlineTable actually takes; the unit above can pass while
  // the rendered block still errors if preferTable does not declare the table.
  const r = parseLoosePayload(SEPARATORLESS, { preferTable: true })
  assert.ok(r && typeof r === 'object', 'must not be null')
  assert.equal(r.columns.length, 4)
  assert.equal(r.rows.length, 3)
})

t('a separator-less table is still NOT sniffed out of free prose', () => {
  // Without `declared` the separator remains the only discriminator, or every
  // sentence containing a pipe becomes a table.
  assert.equal(parseMarkdownTable(SEPARATORLESS), null)
  assert.equal(parseLoosePayload(SEPARATORLESS), null)
})

t('a single pipe line is not a declared table either', () => {
  assert.equal(parseMarkdownTable('just | prose', { declared: true }), null)
})

t('declared tables still honour an explicit separator', () => {
  const r = parseMarkdownTable('A | B\n---|---\n1 | 2', { declared: true })
  assert.deepEqual(r.columns, ['A', 'B'])
  assert.deepEqual(r.rows, [['1', '2']])
})


// ── Robustness of separator-less declared tables ───────────────────────────

t('a lead-in sentence containing a pipe does not become the header', () => {
  const r = parseMarkdownTable(
    ['Вот ветви | по доле:',
     'Координата | Признак | Доля',
     'L-6.2 | Альфа | 8.48%',
     'L-7.2 | Без алкоголя | 5.96%'].join('\n'),
    { declared: true },
  )
  assert.deepEqual(r.columns, ['Координата', 'Признак', 'Доля'])
  assert.equal(r.rows.length, 2)
})

t('trailing prose after the rows is dropped, not parsed as a row', () => {
  const r = parseMarkdownTable(
    ['A | B', '1 | 2', '3 | 4', 'итого по таблице выше'].join('\n'),
    { declared: true },
  )
  assert.deepEqual(r.columns, ['A', 'B'])
  assert.deepEqual(r.rows, [['1', '2'], ['3', '4']])
})

t('escaped pipes stay inside a cell', () => {
  const r = parseMarkdownTable('A | B\n---|---\nx \\| y | 2', { declared: true })
  assert.deepEqual(r.rows[0], ['x | y', '2'])
})

t('a header with no data rows is not a table', () => {
  assert.equal(parseMarkdownTable('A | B | C', { declared: true }), null)
})

t('blank lines between rows do not split the table', () => {
  const r = parseMarkdownTable('A | B\n\n1 | 2\n\n3 | 4', { declared: true })
  assert.equal(r.rows.length, 2)
})


// ── Malformed-but-obvious payloads (2026-08-31) ────────────────────────────
// "Invalid insight: could not parse", seen in production. Not a key
// problem — it would not parse at all. These are the shapes models actually
// write; each replaced a correct answer with a red error box.

t('JSON with a real newline inside a string', () => {
  const r = parseLoosePayload('{"kind":"insight","headline":"Альфа","body":"строка一\nстрока два"}')
  assert.equal(r.headline, 'Альфа')
  assert.ok(r.body.includes('\n'), 'the newline survives as a newline')
})

t('JSON with a trailing comma', () => {
  assert.equal(parseLoosePayload('{"headline":"Альфа","body":"x",}').headline, 'Альфа')
})

t('single-quoted JSON', () => {
  assert.equal(parseLoosePayload("{'headline':'Альфа','body':'x'}").headline, 'Альфа')
})

t('an apostrophe inside proper JSON is NOT mangled', () => {
  // The single-quote repair must not fire when real double-quoted strings exist.
  const r = parseLoosePayload('{"headline":"it\'s fine","body":"x"}')
  assert.equal(r.headline, "it's fine")
})

t('YAML block scalar body (|)', () => {
  const r = parseLoosePayload('kind: insight\nheadline: Альфа\nbody: |\n  первая\n  вторая')
  assert.equal(r.headline, 'Альфа')
  assert.equal(r.body, 'первая\nвторая')
})

t('YAML folded scalar body (>)', () => {
  const r = parseLoosePayload('headline: Альфа\nbody: >\n  первая\n  вторая')
  assert.equal(r.body, 'первая вторая')
})

t('markdown-bolded keys', () => {
  const r = parseLoosePayload('**kind**: insight\n**headline**: Альфа\n**body**: x')
  assert.equal(r.headline, 'Альфа')
})

t('prose is still refused after all the repairs', () => {
  // The repairs must not turn a sentence into a payload.
  assert.equal(parseLoosePayload('Альфа — самая крупная чистая ветвь.'), null)
  assert.equal(parseLoosePayload(''), null)
  assert.equal(parseLoosePayload('just some words here'), null)
})

console.log(`\n${pass} passed`)
