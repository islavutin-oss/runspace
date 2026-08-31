'use client'

import { Download, FileText, Sheet, FileJson, File as FileIcon } from 'lucide-react'
import { parseLoosePayload } from '../utils/loosePayload'
import { applyAliases, FILE_ALIASES } from '../utils/normalizeBlock'

/**
 * A `file` block: something the agent generated and you can take away.
 *
 * The agent writes the artefact through the workspace's file storage and emits
 * this block with the resulting URL. Rendering it as a real download row —
 * rather than a bare link in a sentence — matters because the whole point of
 * generating a report is that somebody leaves with it.
 */
interface FileSpec {
  name: string
  url: string
  /** csv | markdown | json | pdf | text — decides the icon only. */
  kind?: string
  /** Bytes. Shown so nobody clicks a 40MB download by accident. */
  size?: number
  caption?: string
}

const ICONS: Record<string, typeof FileText> = {
  csv: Sheet,
  markdown: FileText,
  md: FileText,
  json: FileJson,
  text: FileText,
  txt: FileText,
}

function human(bytes?: number): string {
  if (!bytes || bytes < 0) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export default function InlineFile({ json }: { json: string }) {
  let spec: FileSpec
  try {
    // Loose payload + key aliases: a block written as flat YAML, or with
    // `href` for `url` / `inputs` for `fields`, is still this block.
    spec = applyAliases(parseLoosePayload(json), FILE_ALIASES) as any
    if (!spec || typeof spec !== 'object') throw new Error('could not parse')
  } catch (e) {
    return (
      <div className="my-3 rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-600 not-prose">
        Invalid file block: {String(e).slice(0, 90)}
      </div>
    )
  }
  if (!spec?.name || !spec?.url) {
    return (
      <div className="my-3 rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-600 not-prose">
        file — `name` and `url` are both required.
      </div>
    )
  }
  // Only same-origin workspace files and plain relative paths. An agent is not
  // a trusted source of URLs, and a download row is exactly the shape a reader
  const safe = /^\/(?!\/)[\w\-./]+$/.test(spec.url)
  if (!safe) {
    return (
      <div className="my-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-700 not-prose">
        file — refused a URL outside this workspace: {spec.url.slice(0, 60)}
      </div>
    )
  }

  const Icon = ICONS[(spec.kind || '').toLowerCase()] || FileIcon
  const size = human(spec.size)

  return (
    <div className="my-3 not-prose">
      <a
        href={spec.url}
        download={spec.name}
        className="flex items-center gap-3 rounded-lg border border-gray-200 bg-white px-3 py-2.5 shadow-sm transition-colors hover:bg-gray-50"
      >
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-gray-100">
          <Icon className="h-4 w-4 text-gray-500" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[13px] font-medium text-gray-900">{spec.name}</span>
          <span className="block text-[11.5px] text-gray-500">
            {[spec.kind?.toUpperCase(), size].filter(Boolean).join(' · ') || 'file'}
          </span>
        </span>
        <Download className="h-4 w-4 shrink-0 text-gray-400" />
      </a>
      {spec.caption && (
        <p className="mt-1 px-1 text-[12px] text-gray-500">{spec.caption}</p>
      )}
    </div>
  )
}
