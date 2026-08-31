'use client'

import AgentChat from './AgentChat'
import type { AgentConfig } from '../components/Sidebar'

interface BotDMPageProps {
  bot: AgentConfig
  userName: string
  apiBase?: string
}

/**
 * BotDMPage is now a thin wrapper around AgentChat for backwards compatibility.
 * All chat logic lives in AgentChat.
 */
export default function BotDMPage({ bot, userName, apiBase = '/api/workspace' }: BotDMPageProps) {
  return <AgentChat agent={bot} userName={userName} apiBase={apiBase} />
}
