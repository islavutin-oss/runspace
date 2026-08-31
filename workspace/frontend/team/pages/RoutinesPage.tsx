'use client'

import { useState, useEffect } from 'react'
import { Clock, Play, RefreshCw, CheckCircle2, AlertCircle, Timer, Plus, Trash2, Power, X } from 'lucide-react'
import { MarkdownContent } from '../../shared/components'
import type { AgentConfig } from '../components/Sidebar'

interface RoutineInfo {
  id: string; schedule: string | number; prompt: string; enabled: boolean; next_run: string | null
  metadata: { agent_id: string; agent_name: string; description: string }
  last_result?: { text: string; duration_ms: number; error: string | null; timestamp: number; tools_used: string[] } | null
}

interface RoutinesPageProps {
  agents: AgentConfig[]
  apiBase?: string
}

const DAY_NAMES: Record<string, string> = { '0': 'Sundays', '1': 'Mondays', '2': 'Tuesdays', '3': 'Wednesdays', '4': 'Thursdays', '5': 'Fridays', '6': 'Saturdays' }

function formatSchedule(schedule: string | number): string {
  if (typeof schedule === 'number') return `Every ${Math.round(schedule / 60)} min`
  const parts = schedule.split(' ')
  if (parts.length !== 5) return schedule
  const [min, hour, , , dow] = parts
  const time = `${hour}:${min.padStart(2, '0')}`
  if (dow === '*') return `Every day at ${time}`
  if (dow === '1-5') return `Weekdays at ${time}`
  return `${DAY_NAMES[dow] || dow} at ${time}`
}

export default function RoutinesPage({ agents, apiBase = '/api/workspace' }: RoutinesPageProps) {
  const [routines, setRoutines] = useState<RoutineInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState<string | null>(null)
  const [showAdd, setShowAdd] = useState(false)
  const [newAgent, setNewAgent] = useState(agents[0]?.id || '')
  const [newTime, setNewTime] = useState('09:00')
  const [newFreq, setNewFreq] = useState<'daily' | 'weekdays' | 'weekly'>('daily')
  const [newDay, setNewDay] = useState('1')
  const [newPrompt, setNewPrompt] = useState('')
  const [newDesc, setNewDesc] = useState('')

  async function fetchRoutines() {
    setLoading(true)
    try { const res = await fetch(`${apiBase}/routines`); if (res.ok) setRoutines((await res.json()).routines || []) } catch {}
    setLoading(false)
  }

  function buildCron(): string {
    const [h, m] = newTime.split(':')
    if (newFreq === 'daily') return `${m} ${h} * * *`
    if (newFreq === 'weekdays') return `${m} ${h} * * 1-5`
    return `${m} ${h} * * ${newDay}`
  }

  async function createRoutine() {
    if (!newPrompt.trim()) return
    await fetch(`${apiBase}/routines`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agent_id: newAgent, schedule: buildCron(), prompt: newPrompt, description: newDesc || 'Custom routine' }),
    })
    setShowAdd(false); setNewPrompt(''); setNewDesc(''); fetchRoutines()
  }

  async function triggerRoutine(id: string) {
    setRunning(id)
    try { await fetch(`${apiBase}/routines/${id}/run`, { method: 'POST' }) } catch {}
    await fetchRoutines(); setRunning(null)
  }

  async function toggleRoutine(id: string, enabled: boolean) {
    await fetch(`${apiBase}/routines/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled }) })
    fetchRoutines()
  }

  async function deleteRoutine(id: string) {
    if (!confirm(`Delete routine "${id}"?`)) return
    await fetch(`${apiBase}/routines/${id}`, { method: 'DELETE' }); fetchRoutines()
  }

  useEffect(() => { fetchRoutines() }, [])

  function getAgent(id: string) { return agents.find(a => a.id === id) }

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-gray-900 flex items-center justify-center"><Timer className="h-5 w-5 text-white" /></div>
          <div>
            <h1 className="text-xl font-bold text-gray-900">Routines</h1>
            <p className="text-xs text-gray-500">Automated tasks your AI team runs on schedule</p>
          </div>
        </div>
        <div className="flex gap-2">
          <button onClick={fetchRoutines} disabled={loading} className="p-2 rounded-lg hover:bg-gray-100">
            <RefreshCw className={`h-4 w-4 text-gray-400 ${loading ? 'animate-spin' : ''}`} />
          </button>
          <button onClick={() => setShowAdd(!showAdd)}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg bg-gray-900 text-white hover:bg-gray-800">
            {showAdd ? <X className="h-3.5 w-3.5" /> : <Plus className="h-3.5 w-3.5" />}
            {showAdd ? 'Cancel' : 'Add Routine'}
          </button>
        </div>
      </div>

      {showAdd && (
        <div className="bg-blue-50/30 border border-blue-200 rounded-xl p-4 space-y-3">
          <div>
            <label className="text-xs font-medium text-gray-600 mb-2 block">Who runs it?</label>
            <div className="flex gap-2">
              {agents.map(a => (
                <button key={a.id} onClick={() => setNewAgent(a.id)}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm border transition-all ${
                    newAgent === a.id ? 'border-gray-900 bg-gray-900 text-white' : 'border-gray-200 hover:border-gray-300'
                  }`}>{a.avatar} {a.name}</button>
              ))}
            </div>
          </div>
          <div>
            <label className="text-xs font-medium text-gray-600 mb-2 block">When?</label>
            <div className="flex items-center gap-3 flex-wrap">
              <div className="flex gap-1">
                {([['daily', 'Every day'], ['weekdays', 'Weekdays'], ['weekly', 'Once a week']] as const).map(([val, label]) => (
                  <button key={val} onClick={() => setNewFreq(val)}
                    className={`px-3 py-1.5 rounded-lg text-sm border ${newFreq === val ? 'border-gray-900 bg-gray-900 text-white' : 'border-gray-200'}`}>{label}</button>
                ))}
              </div>
              {newFreq === 'weekly' && (
                <select value={newDay} onChange={e => setNewDay(e.target.value)} className="text-sm border rounded-lg px-2 py-1.5">
                  {Object.entries(DAY_NAMES).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
              )}
              <div className="flex items-center gap-1.5">
                <span className="text-sm text-gray-500">at</span>
                <input type="time" value={newTime} onChange={e => setNewTime(e.target.value)} className="text-sm border rounded-lg px-2 py-1.5" />
              </div>
            </div>
          </div>
          <div>
            <label className="text-xs font-medium text-gray-600">Description</label>
            <input value={newDesc} onChange={e => setNewDesc(e.target.value)} placeholder="e.g. Daily revenue check"
              className="mt-1 w-full text-sm border rounded-lg px-2 py-1.5" />
          </div>
          <div>
            <label className="text-xs font-medium text-gray-600">What should the agent do?</label>
            <textarea value={newPrompt} onChange={e => setNewPrompt(e.target.value)} rows={3}
              placeholder="Describe the task in plain language…" className="mt-1 w-full text-sm border rounded-lg px-2 py-1.5" />
          </div>
          <button onClick={createRoutine} disabled={!newPrompt.trim()}
            className="px-4 py-1.5 text-sm rounded-lg bg-gray-900 text-white hover:bg-gray-800 disabled:opacity-40">Create Routine</button>
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-16"><RefreshCw className="h-6 w-6 animate-spin text-gray-300" /></div>
      ) : routines.length === 0 ? (
        <div className="text-center py-16 text-gray-400"><Timer className="h-10 w-10 mx-auto mb-3 text-gray-200" /><p className="text-sm">No routines configured</p></div>
      ) : (
        <div className="space-y-4">
          {routines.map(r => {
            const agent = getAgent(r.metadata?.agent_id)
            const isRunning = running === r.id
            const last = r.last_result
            return (
              <div key={r.id} className={`bg-white rounded-xl border border-gray-100 p-5 ${r.enabled ? '' : 'opacity-50'}`}>
                <div className="flex items-start justify-between mb-3">
                  <div className="flex items-center gap-3">
                    <div className="w-10 h-10 rounded-lg flex items-center justify-center text-lg"
                      style={{ backgroundColor: (agent?.color || '#6B7280') + '15' }}>{agent?.avatar || '🤖'}</div>
                    <div>
                      <h3 className="font-semibold text-gray-900">{r.metadata?.description || r.id}</h3>
                      <div className="flex items-center gap-3 text-xs text-gray-500 mt-0.5">
                        <span style={{ color: agent?.color }}>{r.metadata?.agent_name}</span>
                        <span className="flex items-center gap-1"><Clock className="h-3 w-3" /> {formatSchedule(r.schedule)}</span>
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-1">
                    <button onClick={() => triggerRoutine(r.id)} disabled={isRunning}
                      className="flex items-center gap-1 px-2.5 py-1 text-sm rounded-lg border border-gray-200 hover:bg-gray-50">
                      {isRunning ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
                      {isRunning ? 'Running…' : 'Run'}
                    </button>
                    <button onClick={() => toggleRoutine(r.id, !r.enabled)} className="p-1.5 rounded-lg hover:bg-gray-100" title={r.enabled ? 'Disable' : 'Enable'}>
                      <Power className={`h-3.5 w-3.5 ${r.enabled ? 'text-green-600' : 'text-gray-400'}`} />
                    </button>
                    <button onClick={() => deleteRoutine(r.id)} className="p-1.5 rounded-lg hover:bg-gray-100 text-red-400 hover:text-red-600">
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
                <div className="bg-gray-50 rounded-lg px-3 py-2 text-xs text-gray-600 mb-3">
                  <span className="font-medium text-gray-400">Prompt: </span>{r.prompt}
                </div>
                {last && (
                  <div className={`rounded-lg p-3 ${last.error ? 'bg-red-50' : 'bg-green-50/50'}`}>
                    <div className="flex items-center gap-2 mb-2 text-xs">
                      {last.error ? <AlertCircle className="h-3.5 w-3.5 text-red-500" /> : <CheckCircle2 className="h-3.5 w-3.5 text-green-600" />}
                      <span className="text-gray-500">{new Date(last.timestamp * 1000).toLocaleString()} · {last.duration_ms}ms</span>
                    </div>
                    {last.error ? <p className="text-xs text-red-600">{last.error}</p> : (
                      <div className="text-xs text-gray-700 max-h-40 overflow-y-auto">
                        <MarkdownContent text={last.text.slice(0, 500)} />
                      </div>
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
