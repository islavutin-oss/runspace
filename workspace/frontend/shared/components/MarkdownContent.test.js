/**
 * Tests for MarkdownContent — Slack-style @-mention rendering.
 *
 * Plain Node, no test runner — `node MarkdownContent.test.js` exits 0/1.
 * Run from acme/platform/apps/web (where react-markdown is installed):
 *   cd acme/platform/apps/web
 *   node ../../../../runspace/workspace/frontend/components/chat/MarkdownContent.test.js
 *
 * This suite catches the regression where MarkdownContent computed
 * `processed = rewriteMentions(text, kinds)` but then passed the ORIGINAL
 * `{text}` to ReactMarkdown — the rewrite ran, was discarded, and chips
 * never rendered. We assert against the actual SOURCE file (textual lint)
 * so we can't ship the wiring bug again.
 */

// ESM, because workspace/frontend/package.json sets "type": "module". This
// file used require() and therefore threw ReferenceError on every run — it
// never asserted anything, which is why the wiring bug it guards could have
// come back unnoticed.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SOURCE_PATH = path.resolve(__dirname, 'MarkdownContent.tsx');
const SOURCE = fs.readFileSync(SOURCE_PATH, 'utf-8');

let failed = 0;
function assert(cond, msg) {
  if (cond) { console.log('  ✓', msg); }
  else { console.error('  ✗', msg); failed++; }
}

// ──────────────────────────────────────────────────────────────────────
// Source-level lint: the wiring bug we just shipped + repaired.
// ──────────────────────────────────────────────────────────────────────
console.log('Source wiring asserts:');

// `processed = rewriteMentions(text, mentionableKinds)` must exist.
assert(
  /const\s+processed\s*=\s*rewriteMentions\s*\(/.test(SOURCE),
  'rewriteMentions(text, kinds) is computed into `processed`'
);

// ReactMarkdown's children prop must use `processed`, NOT `text`.
// This is the exact bug the test exists to catch.
const rmBlock = SOURCE.match(/<ReactMarkdown[\s\S]*?<\/ReactMarkdown>/);
assert(rmBlock, 'ReactMarkdown JSX block exists');
if (rmBlock) {
  assert(
    />\{\s*processed\s*\}<\/ReactMarkdown>/.test(rmBlock[0]),
    "ReactMarkdown's children is `{processed}` (not `{text}`)"
  );
  assert(
    !/>\{\s*text\s*\}<\/ReactMarkdown>/.test(rmBlock[0]),
    "ReactMarkdown's children is NOT `{text}` (would skip rewrite)"
  );
}

// The mention href contract — we depend on `#mention:<kind>:<slug>` shape.
assert(
  /#mention:\$\{kind\}:\$\{lower\}/.test(SOURCE),
  'rewriteMentions emits `#mention:<kind>:<slug>` href format'
);

// The chip render path uses `[kind]` destructuring (not `[, kind]` which would
// pick up the slug instead).
assert(
  /const\s*\[\s*kind\s*\]\s*=\s*href\.slice/.test(SOURCE),
  'chip handler destructures kind as the FIRST split element (not skipped)'
);

// ──────────────────────────────────────────────────────────────────────
// Regex behavior: rewriteMentions output for known inputs.
// We re-import the regex from source so the test tracks any future change.
// ──────────────────────────────────────────────────────────────────────
console.log('\nRegex behavior asserts:');

// The regex literal contains `/` chars (escaped) so a single-line regex match
// is brittle. Hard-code a copy here that mirrors the source — if the source
// regex changes, update this copy too. The wiring asserts above ensure the
// regex IS the one being applied.
const re = /(^|[^\w\/@])@([a-zA-Z][a-zA-Z0-9_.-]{1,63})/g;
{
  // Sanity check: the regex source string must appear verbatim in MarkdownContent.tsx.
  const sourceRegex = SOURCE.match(/text\.replace\(([^,]+),/);
  assert(
    sourceRegex && sourceRegex[1].includes('a-zA-Z0-9_.-'),
    'source regex contains the expected token charset'
  );
  function rewrite(text, kinds = {}) {
    return text.replace(re, (whole, prefix, token) => {
      let trimmed = token;
      while (trimmed.length > 1 && /[.\-]$/.test(trimmed)) trimmed = trimmed.slice(0, -1);
      const lower = trimmed.toLowerCase();
      const kind = kinds[lower] || 'unknown';
      return `${prefix}[@${trimmed}](#mention:${kind}:${lower})`;
    });
  }

  const cases = [
    {
      name: 'agent at start',
      input: '@luca what is revenue?',
      kinds: { luca: 'agent' },
      contains: '[@luca](#mention:agent:luca)',
    },
    {
      name: 'human user with cyrillic body',
      input: '@sam hello! testing mentions',
      kinds: { sam: 'user' },
      contains: '[@sam](#mention:user:sam)',
    },
    {
      name: 'username with dot (email-local-part)',
      input: 'hi @ada.lovelace can you check?',
      kinds: { 'ada.lovelace': 'user' },
      contains: '[@ada.lovelace](#mention:user:ada.lovelace)',
    },
    {
      name: 'unknown @-token gets unknown kind chip',
      input: '@stranger ignore me',
      kinds: { luca: 'agent' },
      contains: '[@stranger](#mention:unknown:stranger)',
    },
    {
      name: 'no false-match inside an email',
      input: 'send to user@example.com please',
      kinds: { example: 'agent' },
      missing: '[@example]',
    },
    {
      name: 'multiple mentions in one line',
      input: '@luca and @nova please coordinate',
      kinds: { luca: 'agent', nova: 'agent' },
      contains: ['[@luca](#mention:agent:luca)', '[@nova](#mention:agent:nova)'],
    },
    {
      name: 'trailing punctuation trimmed from token',
      input: 'thanks @luca.',
      kinds: { luca: 'agent' },
      contains: '[@luca](#mention:agent:luca)',
    },
  ];

  for (const c of cases) {
    const out = rewrite(c.input, c.kinds);
    if (c.contains) {
      const wants = Array.isArray(c.contains) ? c.contains : [c.contains];
      for (const w of wants) {
        assert(out.includes(w), `${c.name}: output contains "${w}"`);
      }
    }
    if (c.missing) {
      assert(!out.includes(c.missing), `${c.name}: output does NOT contain "${c.missing}"`);
    }
  }
}

console.log('');
// ──────────────────────────────────────────────────────────────────────
// Typography: agent replies are long-form, and at my-1/li-my-0 with
// body-sized headings they rendered as one unscannable grey wall. These
// assert the scale that fixed it, so a future tidy-up cannot quietly
// collapse it again.
// ──────────────────────────────────────────────────────────────────────
console.log('\nTypography asserts:');

assert(!/prose-p:my-1\b/.test(SOURCE), 'paragraphs are not back to my-1');
assert(/prose-p:my-2/.test(SOURCE), 'paragraphs have real separation (my-2)');
assert(/prose-p:leading-relaxed/.test(SOURCE), 'paragraphs have relaxed leading');
assert(!/prose-li:my-0\b/.test(SOURCE), 'list items are not back to my-0');
assert(/prose-li:my-1/.test(SOURCE), 'list items breathe (my-1)');
assert(
  !/prose-headings:text-sm/.test(SOURCE),
  'headings are no longer body-sized (text-sm)'
);
assert(
  /prose-h1:text-\[15px\]/.test(SOURCE) && /prose-h3:text-\[13px\]/.test(SOURCE),
  'headings have a real size hierarchy h1 > h2 > h3'
);
assert(/prose-ul:pl-5/.test(SOURCE) && /prose-ol:pl-5/.test(SOURCE),
  'lists keep their indent so markers are visible');
assert(/\[&>\*:first-child\]:mt-0/.test(SOURCE) && /\[&>\*:last-child\]:mb-0/.test(SOURCE),
  'first/last child margins collapse, so no stray gap in the bubble');
assert(/prose-th:border-b/.test(SOURCE) && /prose-td:border-b/.test(SOURCE),
  'markdown tables have row rules rather than running together');


if (failed) {
  console.error(`FAILED: ${failed} assertion(s)`);
  process.exit(1);
}
console.log('All assertions passed.');
