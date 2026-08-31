// Type declarations for @runspace
// Consumed by projects using the workspace frontend via TypeScript path alias.

declare module '@runspace/ui/team/pages/WorkspaceLayout' {
  import { Context } from 'react'

  export interface AgentConfig {
    id: string; name: string; role: string; avatar: string; color: string; group: string
    description?: string; capabilities?: string[]; isReal?: boolean
  }

  export const AgentsContext: Context<AgentConfig[]>
  export const UserContext: Context<{ name: string; role: string }>
  export const DemoContext: Context<{ messages: any[]; threads: Record<string, any[]> } | null>
  export const RefreshContext: Context<() => void>
  export function useAgents(): AgentConfig[]
  export function useUser(): { name: string; role: string }
  export function useDemo(): { messages: any[]; threads: Record<string, any[]> } | null
  export function useRefresh(): () => void

  export default function WorkspaceLayout(props: {
    children: React.ReactNode; apiBase?: string
    agentGroupLabels?: { backoffice?: string; customer?: string }
  }): JSX.Element
}

declare module '@runspace/ui/team/pages/GeneralChannel' {
  import type { AgentConfig } from '@runspace/team/pages/WorkspaceLayout'
  export default function GeneralChannel(props: {
    agents: AgentConfig[]; apiBase?: string; userName?: string
    initialMessages?: any[]; initialThreads?: Record<string, any[]>
  }): JSX.Element
}

declare module '@runspace/ui/team/pages/AgentChat' {
  import type { AgentConfig } from '@runspace/team/pages/WorkspaceLayout'
  // AgentChat is the slack-style channel + bot DM surface. Stays exactly
  // as it always was — no `layout`, no `autoSendPrompt`. ChatGPT-style
  // chat is its own component (DialogChat) with no shared rendering.
  export default function AgentChat(props: {
    agent: AgentConfig; apiBase?: string; userName?: string
  }): JSX.Element
}

declare module '@runspace/ui/dialog/pages/DialogChat' {
  import type { AgentConfig } from '@runspace/team/pages/WorkspaceLayout'
  import type { ThreadStore } from '@runspace/dialog/components/threadStore'
  export default function DialogChat(props: {
    agents: AgentConfig[]
    apiBase?: string
    userId?: string
    userName?: string
    store?: ThreadStore
    workspaceName?: string
    defaultAgentId?: string
  }): JSX.Element
}

declare module '@runspace/ui/team/pages/WorkspaceShell' {
  import type { AgentConfig } from '@runspace/team/pages/WorkspaceLayout'
  import type { ThreadStore } from '@runspace/dialog/components/threadStore'
  import type { DashboardSpec } from '@runspace/team/components/DashboardPanel'
  export type WorkspaceMode = 'chat' | 'workspace' | 'dashboard'
  export interface WorkspaceShellLabels {
    modes?: Partial<Record<WorkspaceMode, string>>
    dialog?: any  // matches DialogChat's labels prop
  }
  export default function WorkspaceShell(props: {
    agents: AgentConfig[]
    apiBase?: string
    modes?: WorkspaceMode[]
    defaultMode?: WorkspaceMode
    userId?: string
    userName?: string
    store?: ThreadStore
    workspaceName?: string
    defaultAgentId?: string
    dashboards?: DashboardSpec[]
    dashboardUrlParams?: Record<string, string | undefined>
    onDashboardMessage?(event: { type: string; [key: string]: any }): void
    activeMode?: WorkspaceMode
    onActiveModeChange?(m: WorkspaceMode): void
    activeDashboardId?: string
    onDashboardActiveChange?(id: string): void
    labels?: WorkspaceShellLabels
  }): JSX.Element
}

declare module '@runspace/ui/team/components/ModeSwitcher' {
  export type WorkspaceMode = 'chat' | 'workspace' | 'dashboard'
  export default function ModeSwitcher(props: {
    active: WorkspaceMode
    options: WorkspaceMode[]
    onChange(next: WorkspaceMode): void
    className?: string
    layout?: 'overlay' | 'inline'
  }): JSX.Element
}

declare module '@runspace/ui/team/components/DashboardPanel' {
  export interface DashboardSpec {
    id: string
    label: string
    src: string
    icon?: string
  }
  export interface DashboardMessage {
    type: string
    [key: string]: any
  }
  export default function DashboardPanel(props: {
    dashboards: DashboardSpec[]
    defaultId?: string
    urlParams?: Record<string, string | undefined>
    activeId?: string
    onActiveChange?(id: string): void
    onMessage?(event: DashboardMessage): void
  }): JSX.Element
}

declare module '@runspace/ui/dialog/components/threadStore' {
  export interface Thread {
    id: string; userId: string; agentId: string; title: string
    createdAt: number; updatedAt: number; messageCount: number
  }
  export interface ThreadStore {
    list(userId: string, agentId?: string): Promise<Thread[]>
    create(userId: string, agentId: string, title?: string): Promise<Thread>
    rename(id: string, title: string): Promise<Thread | null>
    remove(id: string): Promise<boolean>
    touch(id: string, opts?: { incrementMessages?: number; titleIfEmpty?: string }): Promise<Thread | null>
  }
  export class LocalStorageThreadStore implements ThreadStore {
    constructor(storageKey?: string)
    list(userId: string, agentId?: string): Promise<Thread[]>
    create(userId: string, agentId: string, title?: string): Promise<Thread>
    rename(id: string, title: string): Promise<Thread | null>
    remove(id: string): Promise<boolean>
    touch(id: string, opts?: { incrementMessages?: number; titleIfEmpty?: string }): Promise<Thread | null>
  }
  export interface SupabaseLikeClient {
    from(table: string): any
  }
  export class SupabaseThreadStore implements ThreadStore {
    constructor(opts: { client: SupabaseLikeClient; tenantId?: string; table?: string })
    list(userId: string, agentId?: string): Promise<Thread[]>
    create(userId: string, agentId: string, title?: string): Promise<Thread>
    rename(id: string, title: string): Promise<Thread | null>
    remove(id: string): Promise<boolean>
    touch(id: string, opts?: { incrementMessages?: number; titleIfEmpty?: string }): Promise<Thread | null>
  }
  export function getDefaultThreadStore(): ThreadStore
}

declare module '@runspace/ui/dialog/components/ThreadListSidebar' {
  import type { Thread } from '@runspace/dialog/components/threadStore'
  export default function ThreadListSidebar(props: {
    threads: Thread[]; activeId: string | null; loading?: boolean
    onSelect(id: string): void; onCreate(): void
    onRename(id: string, title: string): void; onDelete(id: string): void
    workspaceName?: string
  }): JSX.Element
}

declare module '@runspace/ui/dialog/components/AgentPicker' {
  export interface PickerAgent { id: string; name: string; avatar: string; color: string; role?: string }
  export default function AgentPicker(props: {
    agents: PickerAgent[]; activeId: string
    onChange(id: string): void; autoHideSingle?: boolean
  }): JSX.Element
}

declare module '@runspace/ui/dialog/hooks/useThreads' {
  import type { Thread, ThreadStore } from '@runspace/dialog/components/threadStore'
  export interface UseThreadsResult {
    threads: Thread[]
    activeThread: Thread | null
    loading: boolean
    selectThread(id: string | null): void
    createThread(title?: string): Promise<Thread>
    renameThread(id: string, title: string): Promise<void>
    deleteThread(id: string): Promise<void>
    touchActive(opts?: { incrementMessages?: number; titleIfEmpty?: string }): Promise<void>
    refresh(): Promise<void>
  }
  export function useThreads(opts: {
    userId: string; agentId?: string; store?: ThreadStore; initialActiveId?: string
  }): UseThreadsResult
}

declare module '@runspace/ui/team/pages/ActivityLogPage' {
  import type { AgentConfig } from '@runspace/team/pages/WorkspaceLayout'
  export default function ActivityLogPage(props: {
    agents: AgentConfig[]; apiBase?: string
  }): JSX.Element
}

declare module '@runspace/ui/team/pages/SettingsPage' {
  export default function SettingsPage(props: {
    apiBase?: string; sections?: any[]; settingsApi?: string; [key: string]: any
  }): JSX.Element
}

declare module '@runspace/ui/team/pages/BotDMPage' {
  import type { AgentConfig } from '@runspace/team/pages/WorkspaceLayout'
  export default function BotDMPage(props: {
    bot: AgentConfig; apiBase?: string; userName?: string; [key: string]: any
  }): JSX.Element
}

declare module '@runspace/ui/team/pages/ChannelPage' {
  import type { AgentConfig } from '@runspace/team/pages/WorkspaceLayout'
  export default function ChannelPage(props: {
    channelSlug: string
    channelName?: string
    agents: AgentConfig[]
    apiBase?: string
    userName?: string
    [key: string]: any
  }): JSX.Element
}

// shared/ primitives — used by both team and dialog modes
declare module '@runspace/ui/shared/components/MarkdownContent' {
  export default function MarkdownContent(props: { text: string }): JSX.Element
}

declare module '@runspace/ui/shared/components/BotAvatar' {
  export default function BotAvatar(props: {
    avatar: string; color: string; [key: string]: any
  }): JSX.Element
}

declare module '@runspace/ui/shared/components' {
  export function MarkdownContent(props: { text: string }): JSX.Element
  export interface ChatMessage {
    id: string; role: 'user' | 'bot'; text: string; time: string
    botId?: string; botName?: string; botAvatar?: string; botColor?: string
    timestamp?: number; toolsUsed?: string[]; edited?: boolean; deleted?: boolean
    images?: string[]; attachments?: any[]
  }
}

// Wildcard declarations: any subpath under @runspace/ui that doesn't have
// an explicit `declare module` block above resolves to `any`. Pragmatic
// for components consumers grab à la carte (KPICard, BotAvatar, …) without
// us having to maintain a full type catalog. Strict consumers can layer
// their own declaration on top.
declare module '@runspace/ui/shared/components/*' {
  const Component: any
  export default Component
  export { Component as default }
}
declare module '@runspace/ui/shared/components' {
  const m: any
  export default m
  export = m
}
declare module '@runspace/ui/team/components/*' {
  const Component: any
  export default Component
  export const KanbanStage: any
}
declare module '@runspace/ui/team/pages/*' {
  const Component: any
  export default Component
}
declare module '@runspace/ui/dialog/*' {
  const m: any
  export default m
}
