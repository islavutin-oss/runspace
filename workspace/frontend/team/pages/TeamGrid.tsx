'use client'

import Link from 'next/link'
import { MessageSquare, CheckCircle2, Users } from 'lucide-react'
import BotAvatar from '../../shared/components/BotAvatar'
import type { AgentConfig } from '../components/Sidebar'

interface TeamGridProps {
  agents: AgentConfig[]
  basePath?: string
}

export default function TeamGrid({ agents, basePath = '/workspace' }: TeamGridProps) {
  return (
    <div className="p-6 max-w-7xl mx-auto space-y-5">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-xl bg-gray-900 flex items-center justify-center"><Users className="h-5 w-5 text-white" /></div>
        <div>
          <h1 className="text-xl font-bold text-gray-900">AI Team</h1>
          <p className="text-xs text-gray-500">{agents.length} AI employees managing your business</p>
        </div>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {agents.map(bot => (
          <Link key={bot.id} href={`${basePath}/team/${bot.id}`}
            className="bg-white rounded-xl border border-gray-100 p-4 hover:shadow-md hover:border-gray-200 transition-all group">
            <div className="flex items-start gap-3 mb-3">
              <BotAvatar avatar={bot.avatar} color={bot.color} size="lg" status="online" />
              <div className="flex-1 min-w-0">
                <h3 className="font-semibold text-gray-900 group-hover:text-gray-700">{bot.name}</h3>
                <p className="text-xs font-medium" style={{ color: bot.color }}>{bot.role}</p>
                {bot.isReal && <span className="inline-block mt-1 text-[10px] px-1.5 py-0.5 rounded bg-green-50 text-green-700 font-medium">LIVE</span>}
              </div>
            </div>
            {bot.description && <p className="text-xs text-gray-500 mb-3 line-clamp-2">{bot.description}</p>}
            <div className="flex items-center gap-4 text-xs text-gray-400">
              {bot.messagesHandled && <span className="flex items-center gap-1"><MessageSquare className="h-3 w-3" /> {bot.messagesHandled.toLocaleString()}</span>}
              {bot.tasksCompleted && <span className="flex items-center gap-1"><CheckCircle2 className="h-3 w-3" /> {bot.tasksCompleted}</span>}
              {bot.lastActionTime && <span className="ml-auto text-[10px]">{bot.lastActionTime}</span>}
            </div>
          </Link>
        ))}
      </div>
    </div>
  )
}
