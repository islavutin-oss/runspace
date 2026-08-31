/**
 * Tests for the `form` block.
 *
 * Plain Node, no runner and no DOM — the component is React, so this asserts
 * against the SOURCE (a textual lint) plus the pure behaviour that decides
 * whether a submission is correct: placeholder substitution and the required
 * field gate. Those are the two places a mistake would silently send an empty
 * or half-filled enquiry, which is worse than not sending one.
 *
 *   node --test InlineForm.test.mjs
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const form = readFileSync(join(here, 'InlineForm.tsx'), 'utf8')
const markdown = readFileSync(join(here, 'MarkdownContent.tsx'), 'utf8')

// --- wiring ----------------------------------------------------------------

test('MarkdownContent routes the form block', () => {
  assert.match(markdown, /language-form/, 'a fenced ```form must reach InlineForm')
  assert.match(markdown, /import InlineForm/, 'InlineForm must be imported')
})

test('form is listed beside the other block languages', () => {
  // The list decides which fences are treated as widgets rather than code.
  const list = markdown.match(/'language-insight'[^\]]*/)?.[0] ?? ''
  assert.match(list, /'language-form'/)
})

test('the component is exported from the package index', () => {
  const index = readFileSync(join(here, 'index.ts'), 'utf8')
  assert.match(index, /InlineForm/)
})

// --- behaviour that decides whether a submission is correct ----------------

/** The substitution the component performs on submit. */
const fill = (prompt, values) => prompt.replace(/\{(\w+)\}/g, (_, k) => values[k] || '')

test('placeholders are replaced with what was typed', () => {
  const out = fill('Please pass this on: {name} ({contact}) — {detail}', {
    name: 'Ada', contact: 'ada@example.test', detail: 'Edge QA',
  })
  assert.equal(out, 'Please pass this on: Ada (ada@example.test) — Edge QA')
})

test('an unknown placeholder becomes empty rather than the literal token', () => {
  // Sending "{company}" to an agent would have it record a brace as a value.
  const out = fill('{name} at {company}', { name: 'Ada' })
  assert.equal(out, 'Ada at ')
  assert.ok(!out.includes('{'), 'no unsubstituted braces may survive')
})

test('a value containing braces does not re-substitute', () => {
  const out = fill('{detail}', { detail: 'uses {curly} braces', name: 'nope' })
  assert.equal(out, 'uses {curly} braces')
})

/** The gate the submit button applies. */
const missing = (fields, values) =>
  fields.filter((f) => f.required && !(values[f.key] || '').trim())

test('a required field left blank blocks submission', () => {
  const fields = [{ key: 'name', required: true }, { key: 'detail' }]
  assert.equal(missing(fields, {}).length, 1)
  assert.equal(missing(fields, { name: 'Ada' }).length, 0)
})

test('whitespace does not satisfy a required field', () => {
  const fields = [{ key: 'name', required: true }]
  assert.equal(missing(fields, { name: '   ' }).length, 1)
})

test('optional fields never block submission', () => {
  const fields = [{ key: 'detail' }]
  assert.equal(missing(fields, {}).length, 0)
})

// --- refusals --------------------------------------------------------------

test('a malformed block renders an error rather than nothing', () => {
  assert.match(form, /Invalid form block/, 'bad JSON must be visible, not silent')
})

test('an empty fields list is refused', () => {
  assert.match(form, /expected a non-empty/, 'a form with no fields must say so')
})

test('submission is disabled when no chat host is wired up', () => {
  assert.match(form, /useIsWidgetIntentWired/)
  assert.match(form, /No chat host is wired up/)
})

test('the form does not invent a submit endpoint', () => {
  assert.ok(!/fetch\(/.test(form), 'submission must go through the widget intent, not a new route')
})
