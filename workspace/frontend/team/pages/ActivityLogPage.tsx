'use client'

import { useState, useEffect } from 'react'
import { Activity, RefreshCw, Wrench, MessageSquare } from 'lucide-react'
import type { AgentConfig } from '../components/Sidebar'

interface ActivityEvent {
  timestamp: number; time_iso: string; actor: string; actor_name: string
  action: string; detail: string; entity_type: string; entity_id: string
}

interface ActivityLogPageProps {
  agents: AgentConfig[]
  apiBase?: string
}

export default function ActivityLogPage({ agents, apiBase = '/api/workspace' }: ActivityLogPageProps) {
  const [events, setEvents] = useState<ActivityEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState<string | null>(null)

  async function fetch_() {
    setLoading(true)
    try {
      let url = `${apiBase}/activity?limit=100`
      if (filter) url += `&actor=${filter}`
      const res = await fetch(url)
      if (res.ok) setEvents((await res.json()).events || [])
    } catch {}
    setLoading(false)
  }

  useEffect(() => { fetch_() }, [filter])

  const grouped = events.reduce<Record<string, ActivityEvent[]>>((acc, ev) => {
    const date = ev.time_iso?.slice(0, 10) || 'Unknown'
    ;(acc[date] ??= []).push(ev)
    return acc
  }, {})

  function getAgent(id: string) { return agents.find(a => a.id === id) }

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-gray-900 flex items-center justify-center">
            <Activity className="h-5 w-5 text-white" />
          </div>
          <div>
            <h1 className="text-xl font-bold text-gray-900">Activity Log</h1>
            <p className="text-xs text-gray-500">Everything your AI team has done</p>
          </div>
        </div>
        <button onClick={fetch_} disabled={loading} className="p-2 rounded-lg hover:bg-gray-100">
          <RefreshCw className={`h-4 w-4 text-gray-400 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      <div className="flex gap-2 flex-wrap">
        <button onClick={() => setFilter(null)}
          className={`px-3 py-1 text-sm rounded-lg border ${!filter ? 'bg-gray-900 text-white border-gray-900' : 'border-gray-200'}`}>All</button>
        {agents.map(a => (
          <button key={a.id} onClick={() => setFilter(a.id)}
            className={`px-3 py-1 text-sm rounded-lg border ${filter === a.id ? 'bg-gray-900 text-white border-gray-900' : 'border-gray-200'}`}>
            {a.avatar} {a.name}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-16"><RefreshCw className="h-6 w-6 animate-spin text-gray-300" /></div>
      ) : events.length === 0 ? (
        <div className="text-center py-16 text-gray-400"><Activity className="h-10 w-10 mx-auto mb-3 text-gray-200" /><p className="text-sm">No activity yet</p></div>
      ) : (
        <div className="space-y-6">
          {Object.entries(grouped).map(([date, dayEvents]) => (
            <div key={date}>
              <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
                {date === new Date().toISOString().slice(0, 10) ? 'Today' : date}
              </h3>
              <div className="bg-white rounded-xl border border-gray-100 divide-y">
                {dayEvents.map((ev, i) => {
                  const agent = getAgent(ev.actor)
                  const isToolCall = ev.action === 'tool_call'
                  return (
                    <div key={`${ev.timestamp}-${i}`} className="flex gap-3 px-4 py-3 hover:bg-gray-50/50">
                      <div className="w-8 h-8 rounded-lg flex items-center justify-center text-sm shrink-0"
                        style={{ backgroundColor: (agent?.color || '#6B7280') + '15' }}>
                        {agent?.avatar || '⚡'}
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-0.5">
                          <span className="text-sm font-medium" style={{ color: agent?.color }}>{ev.actor_name}</span>
                          <span className={`flex items-center gap-1 text-[11px] ${isToolCall ? 'text-blue-500' : 'text-green-500'}`}>
                            {isToolCall ? <Wrench className="h-3 w-3" /> : <MessageSquare className="h-3 w-3" />}
                            {isToolCall ? ev.entity_id : ev.action}
                          </span>
                        </div>
                        <p className="text-xs text-gray-500 truncate">{ev.detail}</p>
                      </div>
                      <span className="text-[10px] text-gray-400 shrink-0 pt-1">{ev.time_iso?.slice(11, 16)}</span>
                    </div>
                  )
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
