'use client'

import { useState, useEffect, useCallback, createContext, useContext } from 'react'
import type { CSSProperties } from 'react'
import { usePathname, useRouter } from 'next/navigation'
import Sidebar, { type SidebarConfig, type AgentConfig } from '../components/Sidebar'
export type { AgentConfig }
import ResizeHandle from '../components/ResizeHandle'

// ── RBAC: staff role visible-pages allowlist ─────────────────────────
//
const STAFF_VISIBLE_PAGE_IDS: ReadonlySet<string> = new Set([
  'bookings',
  'profile',
])
function isStaffRole(role: string | undefined | null): boolean {
  return (role || '').toLowerCase() === 'staff'
}

export const AgentsContext = createContext<AgentConfig[]>([])
export const UserContext = createContext<{ name: string; role: string; email?: string }>({ name: 'User', role: 'Owner' })
export const DemoContext = createContext<{ messages: any[]; threads: Record<string, any[]> } | null>(null)
export const RefreshContext = createContext<() => void>(() => {})
export function useAgents() { return useContext(AgentsContext) }
export function useUser() { return useContext(UserContext) }
export function useDemo() { return useContext(DemoContext) }
export function useRefresh() { return useContext(RefreshContext) }

interface WorkspaceConfig {
  name: string; icon: string; brand_color: string; sidebar_color?: string
  apps: AgentConfig[]; channels: { id: string; label: string; icon: string; type?: string }[]
  user: { name: string; role: string }
  demo?: { messages: any[]; threads: Record<string, any[]> }
}

interface WorkspaceLayoutProps {
  children: React.ReactNode
  apiBase?: string
  agentGroupLabels?: { backoffice?: string; customer?: string }
  /** Groups to leave out of the sidebar. Use when a group already has a
   *  surface of its own — a customer-facing agent given a dedicated chat page
   *  does not also need a row in the team sidebar, where it reads as another
   *  colleague rather than the thing a visitor is meant to talk to. */
  hideAgentGroups?: Array<'backoffice' | 'customer'>
}

export default function WorkspaceLayout({
  children,
  apiBase = '',
  agentGroupLabels = {},
  hideAgentGroups = [],
}: WorkspaceLayoutProps) {
  const [ws, setWs] = useState<WorkspaceConfig | null>(null)
  const [mobileOpen, setMobileOpen] = useState(false)
  const [sidebarWidth, setSidebarWidth] = useState(240)

  // Restore persisted sidebar width after mount (avoids SSR hydration mismatch)
  useEffect(() => {
    const stored = localStorage.getItem('ws:sidebarWidth')
    if (stored) setSidebarWidth(Math.max(200, Math.min(400, parseInt(stored))))
  }, [])

  const handleSidebarResize = useCallback((delta: number) => {
    setSidebarWidth(w => Math.max(200, Math.min(400, w + delta)))
  }, [])

  const handleSidebarResizeEnd = useCallback(() => {
    setSidebarWidth(w => { localStorage.setItem('ws:sidebarWidth', String(w)); return w })
  }, [])

  const [me, setMe] = useState<{ first_name?: string; last_name?: string; email?: string } | null>(null)
  const [meLoaded, setMeLoaded] = useState(false)

  const loadConfig = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/workspace/config`)
      if (res.ok) setWs(await res.json())
    } catch {}
  }, [apiBase])

  // Fetch real user identity from JWT-backed /api/admin/me
  const loadMe = useCallback(async () => {
    try {
      const res = await fetch('/api/admin/me')
      if (res.ok) setMe(await res.json())
    } catch {}
    setMeLoaded(true)
  }, [])

  useEffect(() => { loadConfig(); loadMe() }, [loadConfig, loadMe])

  // Close mobile sidebar on Escape
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === 'Escape' && mobileOpen) setMobileOpen(false)
    }
    document.addEventListener('keydown', handleKey)
    return () => document.removeEventListener('keydown', handleKey)
  }, [mobileOpen])

  const agents = ws?.apps || []
  const fullName = me?.first_name && me?.last_name ? `${me.first_name} ${me.last_name}` : null
  const realName = fullName || me?.first_name || me?.email?.split('@')[0] || ws?.user?.name || 'User'
  const realRole = (me as any)?.role || ws?.user?.role || 'Owner'
  const user = { name: realName, role: realRole.charAt(0).toUpperCase() + realRole.slice(1), email: me?.email }
  const hidden = new Set(hideAgentGroups)
  const backoffice = hidden.has('backoffice') ? [] : agents.filter(a => a.group === 'backoffice')
  const customer = hidden.has('customer') ? [] : agents.filter(a => a.group === 'customer')

  // RBAC: when role=staff, build a stripped-down sidebar (Bookings only,
  // no AI agents, no settings). Frontend route-guard below redirects
  // any other /workspace/<page> to /workspace/bookings; backend
  // require_role guards the corresponding API surface.
  const staff = isStaffRole(realRole)

  // ── Frontend route guard: redirect staff away from non-allowed pages
  const pathname = usePathname()
  const router = useRouter()
  useEffect(() => {
    if (!meLoaded || !staff) return
    if (!pathname?.startsWith('/workspace')) return
    // Strip /workspace/ prefix → page id ('', 'bookings', 'finance/...').
    const tail = pathname.slice('/workspace/'.length)
    const pageId = tail.split('/')[0] || 'bookings'  // empty path → bookings
    if (!STAFF_VISIBLE_PAGE_IDS.has(pageId)) {
      router.replace('/workspace/bookings')
    }
  }, [pathname, meLoaded, staff, router])

  const allChannels = (ws?.channels || []).map(ch => ({
    id: ch.id, label: ch.label, icon: ch.icon,
    href: `/workspace/${ch.id}`,
    type: (['addon', 'system'].includes(ch.type || '') ? ch.type : 'chat') as 'chat' | 'addon' | 'system',
  }))

  const config: SidebarConfig = {
    appName: ws?.name?.replace(' Back Office', '') || 'Workspace',
    appIcon: ws ? (
      <div className="w-8 h-8 rounded-lg flex items-center justify-center text-base"
        style={{ background: `linear-gradient(135deg, ${ws.brand_color}, ${ws.brand_color}bb)` }}>
        {ws.icon}
      </div>
    ) : undefined,
    basePath: '/workspace',
    channels: staff
      ? allChannels.filter(ch => STAFF_VISIBLE_PAGE_IDS.has(ch.id))
      : allChannels,
    agentGroups: staff ? [] : [
      ...(backoffice.length ? [{ label: agentGroupLabels.backoffice || 'AI Team', agents: backoffice }] : []),
      ...(customer.length ? [{ label: agentGroupLabels.customer || 'Customer-Facing', agents: customer }] : []),
    ],
    settingsLinks: staff
      ? []
      : [{ id: 'settings', label: 'Settings', icon: 'Settings', href: '/workspace/settings' }],
    userName: user.name,
    sidebarColor: ws?.sidebar_color,
    userRole: user.role,
    mobileOpen,
    onMobileToggle: setMobileOpen,
  }

  // Loading skeleton — wait for both workspace config AND user identity
  if (!ws || !meLoaded) {
    return (
      <div className="flex min-h-screen bg-gray-50">
        {/* Skeleton sidebar */}
        <aside className="h-screen bg-gray-900 fixed left-0 top-0 hidden lg:flex flex-col" style={{ width: sidebarWidth }}>
          <div className="px-4 py-5 border-b border-white/10">
            <div className="flex items-center gap-2.5">
              <div className="w-8 h-8 rounded-lg bg-white/10 animate-pulse" />
              <div className="space-y-1.5">
                <div className="w-24 h-3 bg-white/10 rounded animate-pulse" />
                <div className="w-16 h-2 bg-white/5 rounded animate-pulse" />
              </div>
            </div>
          </div>
          <div className="flex-1 px-4 py-4 space-y-3">
            {[...Array(3)].map((_, i) => (
              <div key={i} className="w-full h-7 bg-white/5 rounded-md animate-pulse" />
            ))}
            <div className="pt-4" />
            {[...Array(4)].map((_, i) => (
              <div key={i} className="flex items-center gap-2">
                <div className="w-6 h-6 bg-white/10 rounded animate-pulse" />
                <div className="w-20 h-3 bg-white/5 rounded animate-pulse" />
              </div>
            ))}
          </div>
        </aside>
        {/* Skeleton content */}
        <main
            className="flex-1 min-h-screen ml-[var(--ws-sidebar)] max-lg:ml-0"
            // An inline margin-left outranks `max-lg:ml-0`, so the phone
            // layout kept a 240px indent while the sidebar was off-canvas.
            style={{ "--ws-sidebar": `${sidebarWidth}px` } as CSSProperties}
          >
          <div className="h-12 border-b border-gray-200 bg-white flex items-center px-5">
            <div className="w-32 h-4 bg-gray-200 rounded animate-pulse" />
          </div>
          <div className="px-5 py-6 space-y-4">
            {[...Array(5)].map((_, i) => (
              <div key={i} className="flex gap-3">
                <div className="w-9 h-9 bg-gray-200 rounded-lg animate-pulse shrink-0" />
                <div className="space-y-2 flex-1">
                  <div className="w-24 h-3 bg-gray-200 rounded animate-pulse" />
                  <div className="w-3/4 h-3 bg-gray-100 rounded animate-pulse" />
                  <div className="w-1/2 h-3 bg-gray-100 rounded animate-pulse" />
                </div>
              </div>
            ))}
          </div>
        </main>
      </div>
    )
  }

  return (
    <RefreshContext.Provider value={loadConfig}>
      <AgentsContext.Provider value={agents}>
        <UserContext.Provider value={user}>
          <DemoContext.Provider value={ws?.demo || null}>
            <div className="flex min-h-screen bg-gray-50">
              <Sidebar config={config} width={sidebarWidth} />
              <div className="hidden lg:block shrink-0 fixed top-0 z-40" style={{ left: sidebarWidth }}>
                <ResizeHandle direction="horizontal" onResize={handleSidebarResize} onResizeEnd={handleSidebarResizeEnd}
                  className="h-screen" />
              </div>
              <main
            className="flex-1 min-h-screen ml-[var(--ws-sidebar)] max-lg:ml-0"
            // An inline margin-left outranks `max-lg:ml-0`, so the phone
            // layout kept a 240px indent while the sidebar was off-canvas.
            style={{ "--ws-sidebar": `${sidebarWidth}px` } as CSSProperties}
          >
                {children}
              </main>
            </div>
          </DemoContext.Provider>
        </UserContext.Provider>
      </AgentsContext.Provider>
    </RefreshContext.Provider>
  )
}
