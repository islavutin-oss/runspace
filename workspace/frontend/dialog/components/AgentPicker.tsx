'use client'

import { useEffect, useRef, useState } from 'react'
import { ChevronDown } from 'lucide-react'

export interface PickerAgent {
  id: string
  name: string
  avatar: string
  color: string
  role?: string
}

interface AgentPickerProps {
  agents: PickerAgent[]
  activeId: string
  onChange(id: string): void
  /** Hide the picker entirely when only one agent is available. */
  autoHideSingle?: boolean
}

export default function AgentPicker({ agents, activeId, onChange, autoHideSingle = true }: AgentPickerProps) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [open])

  if (autoHideSingle && agents.length <= 1) {
    const only = agents[0]
    if (!only) return null
    return (
      <div className="flex items-center gap-2 px-2 py-1 text-sm text-gray-700">
        <span className="text-base">{only.avatar}</span>
        <span className="font-bold">{only.name}</span>
        {only.role && <span className="text-xs text-gray-400">{only.role}</span>}
      </div>
    )
  }

  const active = agents.find(a => a.id === activeId) || agents[0]
  if (!active) return null

  return (
    <div ref={ref} className="relative">
      <button onClick={() => setOpen(o => !o)}
        className="flex items-center gap-2 px-2 py-1 rounded-md hover:bg-gray-100 text-sm text-gray-700 transition-colors">
        <span className="text-base">{active.avatar}</span>
        <span className="font-bold">{active.name}</span>
        <ChevronDown className="h-3.5 w-3.5 text-gray-400" />
      </button>
      {open && (
        <div className="absolute left-0 top-full mt-1 w-56 bg-white border border-gray-200 rounded-lg shadow-lg overflow-hidden z-30">
          {agents.map(a => (
            <button key={a.id}
              onClick={() => { onChange(a.id); setOpen(false) }}
              className={`w-full flex items-center gap-2 px-3 py-2 text-sm text-left hover:bg-gray-50 ${a.id === activeId ? 'bg-gray-50' : ''}`}>
              <span className="text-base">{a.avatar}</span>
              <span className="font-medium text-gray-800">{a.name}</span>
              {a.role && <span className="text-xs text-gray-400 ml-auto">{a.role}</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
