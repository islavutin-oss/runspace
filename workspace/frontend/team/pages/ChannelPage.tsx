'use client'

import { useState, useRef, useEffect } from 'react'
import { Hash, ArrowDown } from 'lucide-react'
import { MessageBubble, MessageComposer, TypingIndicator, type ChatMessage } from '../../shared/components'
import ThreadPanel from '../components/ThreadPanel'
// import DateDivider, { getDateKey } from '../../shared/components/DateDivider'
import ResizeHandle from '../components/ResizeHandle'
import type { AgentConfig } from '../components/Sidebar'
import { useChannelMessages, type DbMessage } from '../../shared/hooks/useChannelMessages'

interface ChannelPageProps {
  channelSlug: string
  channelName?: string
  agents: AgentConfig[]
  apiBase?: string
  userName?: string
  userId?: string
}

function mapMessage(msg: DbMessage): ChatMessage {
  return {
    id: msg.id,
    role: msg.sender_type === 'agent' ? 'bot' : 'user',
    text: msg.content,
    botId: msg.sender_type === 'agent' ? msg.sender_id : undefined,
    botName: msg.sender_type === 'agent' ? msg.sender_name : undefined,
    botAvatar: msg.sender_avatar || undefined,
    botColor: msg.sender_color || undefined,
    toolsUsed: msg.tools_used?.length ? msg.tools_used : undefined,
    reactions: msg.reactions?.length ? msg.reactions : undefined,
    edited: msg.edited || undefined,
    deleted: msg.deleted || undefined,
    attachments: msg.attachments?.length ? msg.attachments : undefined,
    time: new Date(msg.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
  }
}

export default function ChannelPage({
  channelSlug,
  channelName,
  agents,
  apiBase = '/api/workspace',
  userName = 'You',
  userId = '',
}: ChannelPageProps) {
  const { messages: dbMessages, loading, sending, sendMessage } = useChannelMessages(channelSlug, { apiBase })

  const [threadId, setThreadId] = useState<string | null>(null)
  const [isAtBottom, setIsAtBottom] = useState(true)
  const [hasNewMessage, setHasNewMessage] = useState(false)
  const [threadWidth, setThreadWidth] = useState(() => {
    if (typeof window === 'undefined') return 320
    const saved = localStorage.getItem('ws:threadPanelWidth')
    return saved ? Math.max(280, Math.min(600, Number(saved))) : 320
  })
  const scrollRef = useRef<HTMLDivElement>(null)

  const messages = dbMessages.map(mapMessage)

  // Thread replies: filter messages that have thread_id matching selected thread
  const threadReplies = threadId
    ? dbMessages.filter(m => m.thread_id === threadId).map(mapMessage)
    : []

  // Reply counts per message
  const replyCounts: Record<string, number> = {}
  for (const m of dbMessages) {
    if (m.thread_id) {
      replyCounts[m.thread_id] = (replyCounts[m.thread_id] || 0) + 1
    }
  }

  // Top-level messages only (no thread_id)
  const topMessages = messages.filter((_, i) => !dbMessages[i]?.thread_id)

  // Auto-scroll
  useEffect(() => {
    if (isAtBottom && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    } else {
      setHasNewMessage(true)
    }
  }, [topMessages.length])

  const handleScroll = () => {
    const el = scrollRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60
    setIsAtBottom(atBottom)
    if (atBottom) setHasNewMessage(false)
  }

  const scrollToBottom = () => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
    setHasNewMessage(false)
  }

  const handleSend = async (text: string) => {
    await sendMessage(text)
  }

  const handleThreadReply = async (text: string) => {
    if (!threadId) return
    await sendMessage(text, threadId)
  }

  const displayName = channelName || channelSlug

  if (loading) {
    return <div className="flex-1 flex items-center justify-center text-gray-400">Loading...</div>
  }

  return (
    <div className="flex flex-1 overflow-hidden">
      {/* Main channel */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Header */}
        <div className="flex items-center gap-2 px-4 py-3 border-b border-gray-100 bg-white">
          <Hash className="h-4 w-4 text-gray-400" />
          <h2 className="font-semibold text-gray-900 text-sm" style={{textTransform: 'lowercase'}}>{displayName}</h2>
        </div>

        {/* Messages */}
        <div className="relative flex-1">
          <div ref={scrollRef} onScroll={handleScroll} className="absolute inset-0 overflow-y-auto bg-white">
            <div className="px-5 pb-4 space-y-0.5">
              {topMessages.length === 0 ? (
                <div className="flex items-center justify-center h-full text-gray-400 text-sm pt-20">
                  <p style={{textTransform: 'lowercase'}}>No messages in #{displayName} yet. Start a conversation.</p>
                </div>
              ) : (
                topMessages.map((msg, i) => {
                  const prev = i > 0 ? topMessages[i - 1] : null
                  const showHeader = !prev || prev.role !== msg.role || (msg.role === 'bot' && prev.botId !== msg.botId)
                  return (
                    <div key={msg.id}>
                      <MessageBubble
                        message={{ ...msg, replyCount: replyCounts[msg.id] }}
                        showHeader={showHeader}
                        userName={userName}
                        onOpenThread={() => setThreadId(msg.id)}
                      />
                    </div>
                  )
                })
              )}
            </div>
          </div>

          {/* Scroll to bottom */}
          {hasNewMessage && (
            <button onClick={scrollToBottom}
              className="absolute bottom-3 left-1/2 -translate-x-1/2 flex items-center gap-1.5 px-3 py-1.5 bg-gray-900 text-white text-xs font-medium rounded-full shadow-lg hover:bg-gray-800 z-10">
              <ArrowDown className="h-3 w-3" /> New messages
            </button>
          )}
        </div>

        {/* Composer */}
        <div className="px-4 lg:px-5 pb-3 bg-white shrink-0">
          <MessageComposer
            onSend={handleSend}
            agents={agents}
            placeholder={`Message #${displayName.toLowerCase()} — tag ${agents.map(a => `@${a.name}`).join(', ')}`}
            disabled={sending}
          />
        </div>
      </div>

      {/* Thread panel */}
      {threadId && (
        <>
          <ResizeHandle
            direction="horizontal"
            onResize={(delta) => setThreadWidth(w => Math.max(280, Math.min(600, w - delta)))}
            onResizeEnd={() => localStorage.setItem('ws:threadPanelWidth', String(threadWidth))}
          />
          <ThreadPanel
            parentMessage={messages.find(m => m.id === threadId)!}
            replies={threadReplies}
            onClose={() => setThreadId(null)}
            onSendReply={handleThreadReply}
            typing={false}
            thinkingText=""
            userName={userName}
            channelName={`#${displayName.toLowerCase()}`}
            width={threadWidth}
          />
        </>
      )}
    </div>
  )
}
