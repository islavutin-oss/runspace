'use client'

import { useState, useRef } from 'react'
import { MessageSquare, CheckCircle2, Zap } from 'lucide-react'

interface BotInfo {
  id: string
  name: string
  role: string
  avatar: string
  color: string
  description?: string
  capabilities?: string[]
  messagesHandled?: number
  tasksCompleted?: number
  lastActionTime?: string
  isReal?: boolean
}

export default function BotPopover({ bot, children }: { bot: BotInfo; children: React.ReactNode }) {
  const [show, setShow] = useState(false)
  const timeout = useRef<ReturnType<typeof setTimeout>>()

  function enter() { clearTimeout(timeout.current); timeout.current = setTimeout(() => setShow(true), 400) }
  function leave() { clearTimeout(timeout.current); timeout.current = setTimeout(() => setShow(false), 200) }

  return (
    <div className="relative" onMouseEnter={enter} onMouseLeave={leave}>
      {children}
      {show && (
        <div className="absolute left-full top-0 ml-2 z-50 w-64 bg-[#1e1e3a] rounded-xl shadow-2xl border border-white/10 overflow-hidden animate-fade-in"
          onMouseEnter={() => clearTimeout(timeout.current)} onMouseLeave={leave}>
          <div className="p-4 pb-3" style={{ borderBottom: `2px solid ${bot.color}30` }}>
            <div className="flex items-center gap-3">
              <div className="w-12 h-12 rounded-xl flex items-center justify-center text-2xl"
                style={{ backgroundColor: bot.color + '20' }}>{bot.avatar}</div>
              <div>
                <p className="font-bold text-white text-sm">{bot.name}</p>
                <p className="text-[11px] font-medium" style={{ color: bot.color }}>{bot.role}</p>
                <div className="flex items-center gap-1 mt-0.5">
                  <span className="w-1.5 h-1.5 rounded-full bg-green-400" />
                  <span className="text-[10px] text-white/40">Active</span>
                  {bot.isReal && <span className="text-[10px] px-1 py-px rounded bg-green-400/20 text-green-300 ml-1">LIVE</span>}
                </div>
              </div>
            </div>
          </div>
          {(bot.messagesHandled || bot.tasksCompleted) && (
            <div className="px-4 py-2 flex items-center gap-4 border-b border-white/5">
              {bot.messagesHandled && <span className="flex items-center gap-1.5 text-[11px] text-white/50"><MessageSquare className="h-3 w-3" /> {bot.messagesHandled.toLocaleString()}</span>}
              {bot.tasksCompleted && <span className="flex items-center gap-1.5 text-[11px] text-white/50"><CheckCircle2 className="h-3 w-3" /> {bot.tasksCompleted}</span>}
              {bot.lastActionTime && <span className="flex items-center gap-1.5 text-[11px] text-white/50"><Zap className="h-3 w-3" /> {bot.lastActionTime}</span>}
            </div>
          )}
          {bot.description && (
            <div className="px-4 py-2.5 border-b border-white/5">
              <p className="text-[11px] text-white/60 leading-relaxed">{bot.description}</p>
            </div>
          )}
          {bot.capabilities && bot.capabilities.length > 0 && (
            <div className="px-4 py-2.5">
              <div className="flex flex-wrap gap-1">
                {bot.capabilities.slice(0, 4).map(cap => (
                  <span key={cap} className="text-[10px] px-1.5 py-0.5 rounded font-medium"
                    style={{ backgroundColor: bot.color + '18', color: bot.color }}>{cap}</span>
                ))}
                {bot.capabilities.length > 4 && <span className="text-[10px] text-white/30 self-center">+{bot.capabilities.length - 4}</span>}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
