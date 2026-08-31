'use client'

import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import InlineChart from './InlineChart'
import InlineTable from './InlineTable'
import InlineMermaid from './InlineMermaid'
import InsightCard from './InsightCard'
import KPIBlock from './KPIBlock'
import InlineForm from './InlineForm'
import InlineFile from './InlineFile'

// Only allow safe URL protocols — blocks javascript:, data:text/html, vbscript:, etc.
function isSafeUrl(url: string | undefined): boolean {
  if (!url) return false
  try {
    const parsed = new URL(url, 'https://placeholder.invalid')
    return ['http:', 'https:', 'mailto:'].includes(parsed.protocol)
  } catch {
    return false
  }
}

// Slack-style @-mention pre-processing: convert `@token` (inline, word-boundary)
// into markdown link `[@token](#mention:agent|user:token)`. The custom <a>
function rewriteMentions(text: string, kinds: Record<string, 'agent' | 'user'>): string {
  return text.replace(/(^|[^\w/@])@([a-zA-Z][a-zA-Z0-9_.-]{1,63})/g, (whole, prefix, token) => {
    // Trim trailing punctuation (`.`, `-`) so `@user.handle.` doesn't
    // include the period.
    let trimmed = token
    while (trimmed.length > 1 && /[.\-]$/.test(trimmed)) trimmed = trimmed.slice(0, -1)
    const lower = trimmed.toLowerCase()
    const kind = kinds[lower] || 'unknown'
    return `${prefix}[@${trimmed}](#mention:${kind}:${lower})`
  })
}

interface MarkdownContentProps {
  text: string
  className?: string
  mentionableKinds?: Record<string, 'agent' | 'user'>  // slug → kind, used to colorize @-mentions
}

export default function MarkdownContent({ text, className = '', mentionableKinds = {} }: MarkdownContentProps) {
  const processed = rewriteMentions(text, mentionableKinds)
  return (
    // Typography, deliberately: agent replies here are long-form — a lead
    // paragraph, a heading, a numbered rationale, a table, then caveats. At the
    <div
      className={
        'prose prose-sm prose-gray max-w-none ' +
        'prose-p:my-2 prose-p:leading-relaxed ' +
        'prose-headings:font-semibold prose-headings:text-gray-900 ' +
        'prose-headings:mt-4 prose-headings:mb-1.5 ' +
        'prose-h1:text-[15px] prose-h2:text-[14px] prose-h3:text-[13px] prose-h4:text-[13px] ' +
        'prose-ul:my-2 prose-ol:my-2 prose-ul:pl-5 prose-ol:pl-5 ' +
        'prose-li:my-1 prose-li:leading-relaxed prose-li:pl-0.5 ' +
        'marker:text-gray-400 ' +
        'prose-strong:font-semibold prose-strong:text-gray-900 ' +
        'prose-blockquote:my-2 prose-blockquote:border-l-2 prose-blockquote:border-gray-300 ' +
        'prose-blockquote:pl-3 prose-blockquote:not-italic prose-blockquote:text-gray-600 ' +
        'prose-hr:my-4 prose-hr:border-gray-200 ' +
        'prose-table:my-3 prose-table:text-[12px] ' +
        'prose-th:font-semibold prose-th:text-gray-700 prose-th:border-b prose-th:border-gray-300 ' +
        'prose-th:px-2 prose-th:py-1.5 prose-th:text-left ' +
        'prose-td:border-b prose-td:border-gray-100 prose-td:px-2 prose-td:py-1.5 prose-td:align-top ' +
        'prose-code:text-xs prose-code:bg-gray-100 prose-code:px-1 prose-code:rounded ' +
        'prose-pre:my-2 prose-pre:text-xs ' +
        '[&>*:first-child]:mt-0 [&>*:last-child]:mb-0 ' +
        className
      }
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => {
            // Mention chip — Slack-style colored badge for @-mentions.
            // Format: `#mention:<kind>:<slug>`.
            if (href && href.startsWith('#mention:')) {
              const [kind] = href.slice('#mention:'.length).split(':')
              // Use inline styles (NOT Tailwind classes) so we don't rely on
              // build-time class compilation. If chips still don't show after
              // this, the regex/rewrite chain isn't firing — not styling.
              const baseStyle: React.CSSProperties = {
                display: 'inline-block',
                padding: '1px 6px',
                margin: '0 1px',
                borderRadius: 4,
                fontWeight: 500,
                textDecoration: 'none',
                fontSize: 'inherit',
              }
              const colored: React.CSSProperties = kind === 'agent'
                ? { ...baseStyle, background: '#ede9fe', color: '#6d28d9' }       // purple
                : kind === 'user'
                  ? { ...baseStyle, background: '#dbeafe', color: '#1d4ed8' }      // blue
                  // Neutral, as the note above the rewriter promises. This was red so an
                  // unresolved chip stood out while the mention chain was being built —
                  // but plenty of legitimate text carries an @handle that is nobody in
                  // this workspace (a Telegram account, a GitHub user), and painting
                  // those as errors makes an ordinary answer look broken.
                  : { ...baseStyle, background: '#f3f4f6', color: '#4b5563' }
              return <span style={colored} data-mention-kind={kind || 'unknown'}>{children}</span>
            }
            if (!isSafeUrl(href)) {
              return <span>{children}</span>
            }
            return (
              <a href={href} target="_blank" rel="noopener noreferrer"
                className="text-blue-600 hover:text-blue-800 hover:underline break-all">
                {children}
              </a>
            )
          },
          img: ({ alt }) => {
            // Block all markdown-embedded images (only render via ChatMessage.images)
            return <span className="text-gray-400 text-xs">[image: {alt || 'blocked'}]</span>
          },
          code: ({ className: codeClassName, children }) => {
            const text = String(children).trim()
            if (codeClassName === 'language-chart')      return <InlineChart json={text} />
            if (codeClassName === 'language-datatable')  return <InlineTable json={text} />
            if (codeClassName === 'language-mermaid')    return <InlineMermaid code={text} />
            if (codeClassName === 'language-insight')    return <InsightCard json={text} />
            if (codeClassName === 'language-kpi')        return <KPIBlock json={text} />
            if (codeClassName === 'language-form')       return <InlineForm json={text} />
            if (codeClassName === 'language-file')       return <InlineFile json={text} />
            return <code className={codeClassName}>{children}</code>
          },
          pre: ({ children }) => {
            // react-markdown wraps code blocks in <pre><code>. If the code block
            // rendered a widget (a div), unwrap so we don't get <pre><div></div></pre>.
            const child = Array.isArray(children) ? children[0] : children
            if (child && typeof child === 'object' && 'props' in child) {
              const lang = child.props?.className || ''
              if ([
                'language-chart', 'language-datatable', 'language-mermaid',
                'language-insight', 'language-kpi', 'language-form', 'language-file',
              ].includes(lang)) {
                return <>{children}</>
              }
            }
            return <pre>{children}</pre>
          },
        }}
      >{processed}</ReactMarkdown>
    </div>
  )
}
