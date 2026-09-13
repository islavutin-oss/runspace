'use client'

import { Wrench } from 'lucide-react'

/** The "Used:" line under a reply. `tools` is the call sequence as the
 *  runtime reported it; a reader wants to know *which* tools ran, so the
 *  same tool called twelve times is listed once, in first-use order, under
 *  its label when the app gave it one (`tool_labels` in workspace.yml). */
export function describeTools(tools: string[], labels?: Record<string, string>): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const t of tools) {
    if (!t || seen.has(t)) continue
    seen.add(t)
    out.push(labels?.[t] || t.replace(/_/g, ' '))
  }
  return out
}

export default function ToolBadge({ tools, labels }: { tools: string[]; labels?: Record<string, string> }) {
  const names = describeTools(tools, labels)
  if (!names.length) return null
  return (
    <div className="flex items-center gap-1 text-[11px] text-gray-400 mb-1">
      <Wrench className="h-3 w-3" />
      <span>Used: {names.join(', ')}</span>
    </div>
  )
}
