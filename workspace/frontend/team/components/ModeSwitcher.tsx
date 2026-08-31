'use client'

import { MessageSquare, LayoutGrid, BarChart3 } from 'lucide-react'

export type WorkspaceMode = 'chat' | 'workspace' | 'dashboard'

interface ModeSwitcherProps {
  active: WorkspaceMode
  options: WorkspaceMode[]
  onChange(next: WorkspaceMode): void
  className?: string
  /** "fixed top-right" overlay (default) or inline. */
  layout?: 'overlay' | 'inline'
  /** Override the mode labels (e.g. for localisation). */
  labels?: Partial<Record<WorkspaceMode, string>>
}

const DEFAULT_LABELS: Record<WorkspaceMode, { label: string; Icon: any }> = {
  chat:      { label: 'Chat',      Icon: MessageSquare },
  workspace: { label: 'Workspace', Icon: LayoutGrid    },
  dashboard: { label: 'Dashboard', Icon: BarChart3     },
}

/**
 * Tiny segmented toggle for WorkspaceShell. When only one mode is enabled
 * WorkspaceShell skips rendering this entirely.
 */
export default function ModeSwitcher({ active, options, onChange, className = '', layout = 'overlay', labels }: ModeSwitcherProps) {
  const base = 'flex items-center gap-0.5 rounded-full border border-gray-200 bg-white/90 backdrop-blur px-1 py-1 shadow-sm'
  const overlay = layout === 'overlay' ? 'fixed top-2 right-3 z-50' : ''
  return (
    <div className={`${overlay} ${base} ${className}`}>
      {options.map(mode => {
        const isActive = mode === active
        const def = DEFAULT_LABELS[mode]
        const label = labels?.[mode] ?? def.label
        const Icon = def.Icon
        return (
          <button key={mode} onClick={() => onChange(mode)}
            className={`flex items-center gap-1.5 px-3 py-1 text-xs rounded-full transition-colors ${
              isActive ? 'bg-gray-900 text-white' : 'text-gray-600 hover:bg-gray-100'
            }`}>
            <Icon className="h-3.5 w-3.5" />
            {label}
          </button>
        )
      })}
    </div>
  )
}
