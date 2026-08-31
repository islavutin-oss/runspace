'use client'

import { useState, useEffect } from 'react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import {
  Hash, CalendarDays, BarChart3, Package, Wallet, FileText,
  Activity, Timer, Settings, Users, Menu, X, ChevronDown, type LucideIcon,
} from 'lucide-react'
import BotPopover from '../../shared/components/BotPopover'

const ICON_MAP: Record<string, LucideIcon> = {
  Hash, CalendarDays, BarChart3, Package, Wallet, FileText,
  Activity, Timer, Settings, Users,
}

export interface ChannelConfig {
  id: string
  label: string
  icon: string  // key into ICON_MAP
  href: string
  type?: 'chat' | 'addon' | 'system'  // chat = #general, addon = dashboard, system = admin/settings
}

export interface AgentConfig {
  id: string
  name: string
  role: string
  avatar: string
  color: string
  group: string
  description?: string
  /** Opening questions for this agent's 1-1 view, from workspace.yml. */
  suggestions?: string[]
  capabilities?: string[]
  messagesHandled?: number
  tasksCompleted?: number
  lastActionTime?: string
  isReal?: boolean
}

export interface SidebarConfig {
  appName: string
  appIcon?: React.ReactNode
  basePath: string           // e.g. "/workspace"
  channels: ChannelConfig[]
  agentGroups: { label: string; agents: AgentConfig[] }[]
  settingsLinks?: ChannelConfig[]
  userName?: string
  userRole?: string
  sidebarColor?: string  // e.g. "#161616"
  unreadCounts?: Record<string, number>  // keyed by channel id or agent id
  onMobileToggle?: (open: boolean) => void
  mobileOpen?: boolean
}

function UnreadBadge({ count }: { count: number }) {
  if (!count) return null
  return (
    <span className="ml-auto shrink-0 min-w-[18px] h-[18px] flex items-center justify-center rounded-full bg-red-500 text-white text-[10px] font-bold px-1">
      {count > 99 ? '99+' : count}
    </span>
  )
}

export default function Sidebar({ config, width }: { config: SidebarConfig; width?: number }) {
  const pathname = usePathname()
  const unreads = config.unreadCounts || {}

  // Collapsible sections — persisted to localStorage
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>(() => {
    if (typeof window === 'undefined') return {}
    try {
      const stored = localStorage.getItem('ws:sidebar:collapsed')
      return stored ? JSON.parse(stored) : {}
    } catch { return {} }
  })

  useEffect(() => {
    try { localStorage.setItem('ws:sidebar:collapsed', JSON.stringify(collapsed)) } catch {}
  }, [collapsed])

  function toggleSection(key: string) {
    setCollapsed(prev => ({ ...prev, [key]: !prev[key] }))
  }

  function isActive(href: string) {
    if (href === config.basePath) return pathname === config.basePath
    return pathname.startsWith(href)
  }

  const sidebarContent = (
    <>
      {/* Logo */}
      <div className="px-4 py-5 border-b border-white/10">
        <Link href={config.basePath} className="flex items-center gap-2.5">
          {config.appIcon || (
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-gray-600 to-gray-800 flex items-center justify-center text-xs font-bold">
              {config.appName[0]}
            </div>
          )}
          <div>
            <span className="font-serif font-semibold text-base tracking-wide">{config.appName}</span>
            <span className="block text-[10px] text-white/40 -mt-0.5 tracking-wider uppercase">Back Office</span>
          </div>
        </Link>
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto py-3 px-2">
        {/* Channels (type=chat) — collapsible */}
        {(() => {
          const chatChannels = config.channels.filter(ch => !ch.type || ch.type === 'chat')
          const dashboardChannels = config.channels.filter(ch => ch.type === 'addon')
          const systemChannels = config.channels.filter(ch => ch.type === 'system')
          return (<>
            {chatChannels.length > 0 && (<>
              <button onClick={() => toggleSection('channels')}
                className="flex items-center gap-1 px-2 mb-1.5 w-full text-left group/sec">
                <ChevronDown className={`h-2.5 w-2.5 text-white/30 transition-transform ${collapsed['channels'] ? '-rotate-90' : ''}`} />
                <span className="text-[10px] font-semibold uppercase tracking-wider text-white/30 group-hover/sec:text-white/50">Channels</span>
              </button>
              {!collapsed['channels'] && chatChannels.map(ch => {
                const Icon = ICON_MAP[ch.icon] || Hash
                const active = isActive(ch.href)
                const unread = unreads[ch.id] || 0
                return (
                  <Link key={ch.id} href={ch.href}
                    onClick={() => config.onMobileToggle?.(false)}
                    className={`flex items-center gap-2.5 px-2.5 py-1.5 rounded-md text-sm mb-0.5 transition-colors ${
                      active ? 'bg-white/10 text-white font-medium' :
                      unread ? 'text-white font-medium hover:bg-white/5' :
                      'text-white/50 hover:text-white/80 hover:bg-white/5'
                    }`}>
                    <Icon className="h-3.5 w-3.5 shrink-0 text-white/40" />
                    <span>{ch.label}</span>
                    <UnreadBadge count={unread} />
                  </Link>
                )
              })}
            </>)}

            {/* Dashboard pages (type=addon) — collapsible */}
            {dashboardChannels.length > 0 && (<>
              <button onClick={() => toggleSection('dashboard')}
                className="flex items-center gap-1 px-2 mt-4 mb-1.5 w-full text-left group/sec">
                <ChevronDown className={`h-2.5 w-2.5 text-white/30 transition-transform ${collapsed['dashboard'] ? '-rotate-90' : ''}`} />
                <span className="text-[10px] font-semibold uppercase tracking-wider text-white/30 group-hover/sec:text-white/50">Dashboard</span>
              </button>
              {!collapsed['dashboard'] && dashboardChannels.map(ch => {
                const Icon = ICON_MAP[ch.icon] || Hash
                const active = isActive(ch.href)
                return (
                  <Link key={ch.id} href={ch.href}
                    onClick={() => config.onMobileToggle?.(false)}
                    className={`flex items-center gap-2.5 px-2.5 py-1.5 rounded-md text-sm mb-0.5 transition-colors ${
                      active ? 'bg-white/10 text-white font-medium' : 'text-white/50 hover:text-white/80 hover:bg-white/5'
                    }`}>
                    <Icon className="h-3.5 w-3.5 shrink-0" />
                    <span>{ch.label}</span>
                  </Link>
                )
              })}
            </>)}
          </>)
        })()}

        {/* Agent groups — collapsible */}
        {config.agentGroups.map(group => {
          const sectionKey = `group-${group.label}`
          return (
            <div key={group.label}>
              <button onClick={() => toggleSection(sectionKey)}
                className="flex items-center gap-1 px-2 mt-4 mb-1.5 w-full text-left group/sec">
                <ChevronDown className={`h-2.5 w-2.5 text-white/30 transition-transform ${collapsed[sectionKey] ? '-rotate-90' : ''}`} />
                <span className="text-[10px] font-semibold uppercase tracking-wider text-white/30 group-hover/sec:text-white/50">{group.label}</span>
              </button>
              {!collapsed[sectionKey] && group.agents.map(bot => {
                const active = pathname === `${config.basePath}/team/${bot.id}`
                const unread = unreads[bot.id] || 0
                return (
                  <BotPopover key={bot.id} bot={bot}>
                    <Link href={`${config.basePath}/team/${bot.id}`}
                      onClick={() => config.onMobileToggle?.(false)}
                      className={`flex items-center gap-2.5 px-2.5 py-1.5 rounded-md text-sm mb-0.5 transition-colors ${
                        active ? 'bg-white/10 text-white font-medium' :
                        unread ? 'text-white font-medium hover:bg-white/5' :
                        'text-white/50 hover:text-white/80 hover:bg-white/5'
                      }`}>
                      <span className="text-lg leading-none">{bot.avatar}</span>
                      <span className="flex-1 truncate">{bot.name}</span>
                      <UnreadBadge count={unread} />
                      {!unread && <span className="w-2 h-2 rounded-full bg-green-400 shrink-0" />}
                    </Link>
                  </BotPopover>
                )
              })}
            </div>
          )
        })}

        {/* System — settings + system-type channels (activity log, routines) */}
        {(() => {
          const sysChannels = config.channels.filter(ch => ch.type === 'system')
          const allSystemItems = [...sysChannels, ...(config.settingsLinks || [])]
          if (allSystemItems.length === 0) return null
          return (<>
            <button onClick={() => toggleSection('system')}
              className="flex items-center gap-1 px-2 mt-4 mb-1.5 w-full text-left group/sec">
              <ChevronDown className={`h-2.5 w-2.5 text-white/30 transition-transform ${collapsed['system'] ? '-rotate-90' : ''}`} />
              <span className="text-[10px] font-semibold uppercase tracking-wider text-white/30 group-hover/sec:text-white/50">System</span>
            </button>
            {!collapsed['system'] && allSystemItems.map(s => {
              const Icon = ICON_MAP[s.icon] || Settings
              const active = isActive(s.href)
              return (
                <Link key={s.id} href={s.href}
                  onClick={() => config.onMobileToggle?.(false)}
                  className={`flex items-center gap-2.5 px-2.5 py-1.5 rounded-md text-sm mb-0.5 transition-colors ${
                    active ? 'bg-white/10 text-white font-medium' : 'text-white/50 hover:text-white/80 hover:bg-white/5'
                  }`}>
                  <Icon className="h-3.5 w-3.5 shrink-0" />
                  <span>{s.label}</span>
                </Link>
              )
            })}
          </>)
        })()}
      </nav>

      {/* Footer */}
      <div className="px-3 py-3 border-t border-white/10">
        <Link href={`${config.basePath}/profile`}
          onClick={() => config.onMobileToggle?.(false)}
          className="flex items-center gap-2 px-2 py-1.5 rounded-md text-xs text-white/40 hover:text-white/70 hover:bg-white/5 transition-colors">
          <div className="w-5 h-5 rounded-full bg-white/10 flex items-center justify-center text-[10px]">
            {(config.userName || 'U').split(' ').map(w => w[0]).join('').slice(0, 2)}
          </div>
          <span>{config.userName || 'Profile'} · {config.userRole || 'Owner'}</span>
        </Link>
      </div>
    </>
  )

  return (
    <>
      {/* Mobile hamburger */}
      <button
        onClick={() => config.onMobileToggle?.(!config.mobileOpen)}
        className="fixed top-3 left-3 z-50 p-2 rounded-lg bg-gray-900 text-white lg:hidden"
      >
        {config.mobileOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
      </button>

      {/* Mobile overlay */}
      {config.mobileOpen && (
        <div className="fixed inset-0 bg-black/50 z-30 lg:hidden" onClick={() => config.onMobileToggle?.(false)} />
      )}

      {/* Sidebar — always visible on desktop, toggled on mobile */}
      <aside
        className={`h-screen text-white flex flex-col fixed left-0 top-0 z-40 transition-transform lg:translate-x-0 ${
          config.mobileOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
        style={{ width: width || 240, backgroundColor: config.sidebarColor || '#1a1a2e' }}
      >
        {sidebarContent}
      </aside>
    </>
  )
}
