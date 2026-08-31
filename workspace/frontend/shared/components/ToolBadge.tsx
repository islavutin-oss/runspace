'use client'

import { Wrench } from 'lucide-react'

export default function ToolBadge({ tools }: { tools: string[] }) {
  if (!tools.length) return null
  return (
    <div className="flex items-center gap-1 text-[11px] text-gray-400 mb-1">
      <Wrench className="h-3 w-3" />
      <span>Used: {tools.map(t => t.replace(/_/g, ' ')).join(', ')}</span>
    </div>
  )
}
