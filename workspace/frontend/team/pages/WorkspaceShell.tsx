'use client'

/**
 * Configurable top-level shell for the agentino workspace UI.
 *
 * Consumers pick which surface(s) they want via the `modes` prop:
 *
 *   <WorkspaceShell agents={...} modes={['chat']} />              chat only
 *   <WorkspaceShell agents={...} modes={['workspace']} />         workspace only
 *   <WorkspaceShell agents={...} modes={['chat', 'workspace']} /> both, with switcher
 *
 * - chat       → ChatGPT/Claude-style DialogChat (this package's new feature)
 * - workspace  → Slack-style: agent tab bar + per-agent AgentChat (1-on-1 DMs)
 *
 * The workspace mode here is intentionally self-contained (no router
 * dependency). For tenants that want the full WorkspaceLayout sidebar with
 * channels, mount WorkspaceLayout directly in your app — this shell is the
 * "drop-in, zero-routing" path.
 */

import { useEffect, useMemo, useState } from 'react'
import type { AgentConfig } from './WorkspaceLayout'
import DialogChat from '../../dialog/pages/DialogChat'
import AgentChat from './AgentChat'
import ModeSwitcher, { type WorkspaceMode } from '../components/ModeSwitcher'
import DashboardPanel, { type DashboardSpec, type DashboardMessage } from '../components/DashboardPanel'
import type { ThreadStore } from '../../dialog/components/threadStore'
import type { DialogChatLabels } from '../../dialog/pages/DialogChat'

export interface WorkspaceShellLabels {
  modes?: Partial<Record<WorkspaceMode, string>>
  dialog?: DialogChatLabels
}

interface WorkspaceShellProps {
  agents: AgentConfig[]
  apiBase?: string
  /** Which surface(s) to expose. Defaults to ['chat']. */
  modes?: WorkspaceMode[]
  /** Initial mode (must be in `modes`). Defaults to the first entry. */
  defaultMode?: WorkspaceMode

  /** Identity used to scope dialog threads. */
  userId?: string
  userName?: string

  /** Pluggable dialog thread store (defaults to LocalStorageThreadStore). */
  store?: ThreadStore

  /** Sidebar header in chat mode. */
  workspaceName?: string
  /** Initial agent for chat mode. */
  defaultAgentId?: string

  /** Dashboards exposed when 'dashboard' is in `modes`. */
  dashboards?: DashboardSpec[]
  /** Optional URL params appended to every dashboard iframe (e.g. {focus:"1.5.2"}). */
  dashboardUrlParams?: Record<string, string | undefined>
  /** Receives postMessage events from any embedded dashboard. */
  onDashboardMessage?(event: DashboardMessage): void

  /** Controlled mode (override the internal switcher state). */
  activeMode?: WorkspaceMode
  onActiveModeChange?(mode: WorkspaceMode): void

  /** Controlled active dashboard sub-tab id (when more than one dashboard). */
  activeDashboardId?: string
  onDashboardActiveChange?(id: string): void

  /** Localised UI strings — falls back to English. */
  labels?: WorkspaceShellLabels
}

export default function WorkspaceShell({
  agents,
  apiBase = '/api/workspace',
  modes,
  defaultMode,
  userId,
  userName,
  store,
  workspaceName,
  defaultAgentId,
  dashboards = [],
  dashboardUrlParams,
  onDashboardMessage,
  activeMode: activeModeProp,
  onActiveModeChange,
  activeDashboardId,
  onDashboardActiveChange,
  labels,
}: WorkspaceShellProps) {
  const enabled: WorkspaceMode[] = useMemo(() => {
    const list: WorkspaceMode[] = (modes && modes.length > 0) ? modes : ['chat']
    return Array.from(new Set(list))
  }, [modes])

  const [internalActive, setInternalActive] = useState<WorkspaceMode>(
    defaultMode && enabled.includes(defaultMode) ? defaultMode : enabled[0]
  )
  const active = activeModeProp ?? internalActive
  const setActive = (m: WorkspaceMode) => {
    if (onActiveModeChange) onActiveModeChange(m)
    if (activeModeProp === undefined) setInternalActive(m)
  }

  useEffect(() => {
    if (!enabled.includes(active)) setActive(enabled[0])
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, active])

  return (
    <div className="h-full flex flex-col bg-white">
      {enabled.length > 1 && (
        <ModeSwitcher active={active} options={enabled} onChange={setActive} labels={labels?.modes} />
      )}

      <div className="flex-1 min-h-0">
        {active === 'chat' && (
          <DialogChat
            agents={agents}
            apiBase={apiBase}
            userId={userId}
            userName={userName}
            store={store}
            workspaceName={workspaceName}
            defaultAgentId={defaultAgentId}
            labels={labels?.dialog}
          />
        )}
        {active === 'workspace' && (
          <SimpleWorkspaceMode
            agents={agents}
            apiBase={apiBase}
            userName={userName}
            defaultAgentId={defaultAgentId}
          />
        )}
        {active === 'dashboard' && (
          <DashboardPanel
            dashboards={dashboards}
            urlParams={dashboardUrlParams}
            onMessage={onDashboardMessage}
            activeId={activeDashboardId}
            onActiveChange={onDashboardActiveChange}
          />
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Slack-style "workspace" surface — agent tab bar + per-agent AgentChat.
// Self-contained: no routing assumptions, no Supabase channels required.
// ---------------------------------------------------------------------------

function SimpleWorkspaceMode({
  agents, apiBase, userName, defaultAgentId,
}: {
  agents: AgentConfig[]
  apiBase: string
  userName?: string
  defaultAgentId?: string
}) {
  const [activeId, setActiveId] = useState<string>(defaultAgentId || agents[0]?.id || '')

  useEffect(() => {
    if (!agents.find(a => a.id === activeId) && agents[0]) setActiveId(agents[0].id)
  }, [agents, activeId])

  const active = agents.find(a => a.id === activeId)
  if (!active) {
    return (
      <div className="h-full flex items-center justify-center text-sm text-gray-400">
        No agents configured.
      </div>
    )
  }

  return (
    <div className="h-full flex flex-col">
      {agents.length > 1 && (
        <div className="flex items-center gap-1 px-3 py-2 border-b border-gray-200 bg-gray-50 shrink-0 overflow-x-auto">
          {agents.map(a => {
            const isActive = a.id === activeId
            return (
              <button key={a.id} onClick={() => setActiveId(a.id)}
                className={`flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md transition-colors whitespace-nowrap ${
                  isActive ? 'bg-white shadow-sm text-gray-900' : 'text-gray-600 hover:bg-gray-100'
                }`}
                style={isActive ? { borderColor: a.color + '33' } : undefined}>
                <span className="text-sm">{a.avatar}</span>
                <span className="font-medium">{a.name}</span>
              </button>
            )
          })}
        </div>
      )}
      <div className="flex-1 min-h-0">
        <AgentChat key={active.id} agent={active} apiBase={apiBase} userName={userName} />
      </div>
    </div>
  )
}
