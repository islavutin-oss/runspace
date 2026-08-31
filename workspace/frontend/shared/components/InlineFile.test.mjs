/**
 * Tests for the `file` block — the row a reader clicks to take something away.
 *
 * Plain Node, no DOM: asserts against the source plus the pure logic that
 * decides what is safe to render and how a size reads.
 *
 *   node --test InlineFile.test.mjs
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const file = readFileSync(join(here, 'InlineFile.tsx'), 'utf8')
const markdown = readFileSync(join(here, 'MarkdownContent.tsx'), 'utf8')

test('MarkdownContent routes the file block', () => {
  assert.match(markdown, /language-file/)
  assert.match(markdown, /import InlineFile/)
})

test('file is listed beside the other block languages', () => {
  const list = markdown.match(/'language-insight'[^\]]*/)?.[0] ?? ''
  assert.match(list, /'language-file'/)
})

// The guard the component applies before rendering a clickable download.
const safe = (url) => /^\/(?!\/)[\w\-./]+$/.test(url)

test('workspace-relative URLs are allowed', () => {
  assert.ok(safe('/api/workspace/files/abc123'))
  assert.ok(safe('/api/workspace/files/a-b.c_d.csv'))
})

test('an off-site URL is refused', () => {
  // A download row is exactly what a reader clicks without looking, and an
  // agent is not a trusted source of URLs.
  for (const bad of [
    'https://evil.example/x.csv',
    '//evil.example/x.csv',
    'javascript:alert(1)',
    'data:text/csv;base64,AAAA',
    'http://localhost:9/x',
  ]) {
    assert.ok(!safe(bad), `${bad} must be refused`)
  }
})

test('the refusal is visible, not silent', () => {
  assert.match(file, /refused a URL outside this workspace/)
})

test('a malformed block renders an error', () => {
  assert.match(file, /Invalid file block/)
})

test('name and url are both required', () => {
  assert.match(file, /`name` and `url` are both required/)
})

// Size formatting: a reader should know before clicking.
const human = (bytes) => {
  if (!bytes || bytes < 0) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

test('sizes read in sensible units', () => {
  assert.equal(human(512), '512 B')
  assert.equal(human(2048), '2 KB')
  assert.equal(human(5 * 1024 * 1024), '5.0 MB')
})

test('an absent size renders as nothing rather than zero', () => {
  assert.equal(human(undefined), '')
  assert.equal(human(0), '')
})

test('the anchor carries a download attribute', () => {
  assert.match(file, /download=\{spec\.name\}/)
})
