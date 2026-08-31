'use client'

import { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import { Hash, ArrowDown } from 'lucide-react'
import { MessageBubble, MessageComposer, TypingIndicator, type ChatMessage } from '../../shared/components'
import ThreadPanel from '../components/ThreadPanel'
import DateDivider, { getDateKey } from '../../shared/components/DateDivider'
import ResizeHandle from '../components/ResizeHandle'
import type { AgentConfig } from '../components/Sidebar'
import { chatStream } from '../../shared/hooks/useChatStream'

// Supabase Realtime for live updates from other users
let _supabaseClient: any = null
function getSupabaseClient() {
  if (_supabaseClient) return _supabaseClient
  const url = typeof window !== 'undefined' ? (window as any).__NEXT_DATA__?.props?.pageProps?.supabaseUrl || process.env.NEXT_PUBLIC_SUPABASE_URL : ''
  const key = typeof window !== 'undefined' ? (window as any).__NEXT_DATA__?.props?.pageProps?.supabaseAnonKey || process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY : ''
  if (!url || !key) return null
  try {
    const { createClient } = require('@supabase/supabase-js')
    _supabaseClient = createClient(url, key)
    return _supabaseClient
  } catch { return null }
}

interface MentionUser {
  id: string
  name: string
  email?: string
  avatar?: string
  color?: string
}

interface GeneralChannelProps {
  agents: AgentConfig[]
  apiBase?: string
  userName?: string
  initialMessages?: ChatMessage[]
  initialThreads?: Record<string, ChatMessage[]>
  /** Channel slug to read and post to. Defaults to `general`, which is what
   *  every caller assumed before a workspace could declare more than one. */
  channel?: string
}

export default function GeneralChannel({ agents, apiBase = '/api/workspace', userName = 'You', initialMessages = [], initialThreads = {}, channel = 'general' }: GeneralChannelProps) {
  // Tenant users for the @-mention list (humans alongside agents)
  const [users, setUsers] = useState<MentionUser[]>([])
  useEffect(() => {
    let cancelled = false
    fetch(`${apiBase}/users`)
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(data => { if (!cancelled) setUsers(data.users || []) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [apiBase])

  // Slug → kind map for Slack-style @-mention coloring in rendered messages.
  // Lower-cased on both sides; user names also indexed by their email-local part
  // so `@user.handle` matches their workspace id when the email-local
  // and handle differ.
  const mentionableKinds = useMemo<Record<string, 'agent' | 'user'>>(() => {
    const map: Record<string, 'agent' | 'user'> = {}
    for (const a of agents) {
      map[a.id.toLowerCase()] = 'agent'
      map[a.name.toLowerCase()] = 'agent'
    }
    for (const u of users) {
      map[u.id.toLowerCase()] = 'user'
      map[u.name.toLowerCase()] = 'user'
      if (u.email) {
        const local = u.email.split('@')[0].toLowerCase()
        if (local) map[local] = 'user'
      }
    }
    return map
  }, [agents, users])
  const [messages, setMessages] = useState<ChatMessage[]>(initialMessages)
  const [typing, setTyping] = useState<string | null>(null)
  const [thinking, setThinking] = useState('')
  const [threadId, setThreadId] = useState<string | null>(null)
  const [threadReplies, setThreadReplies] = useState<Record<string, ChatMessage[]>>(initialThreads)
  const [threadTyping, setThreadTyping] = useState(false)
  const [threadThinking, setThreadThinking] = useState('')
  const [isAtBottom, setIsAtBottom] = useState(true)
  const [hasNewMessage, setHasNewMessage] = useState(false)
  const [threadWidth, setThreadWidth] = useState(() => {
    if (typeof window === 'undefined') return 320
    const saved = localStorage.getItem('ws:threadPanelWidth')
    return saved ? Math.max(280, Math.min(600, Number(saved))) : 320
  })
  const scrollRef = useRef<HTMLDivElement>(null)
  const sessionRef = useRef(`${channel}-${Date.now()}`)
  const demoLoaded = useRef(false)

  // Persist a message to the DB (fire-and-forget)
  function persistMessage(msg: ChatMessage) {
    try {
      fetch(`${apiBase}/channels/${channel}/messages`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          content: msg.text,
          sender_type: msg.role === 'bot' ? 'agent' : 'user',
          sender_id: msg.role === 'bot' ? (msg.botId || 'system') : 'user',
          sender_name: msg.role === 'bot' ? (msg.botName || 'Bot') : userName,
          sender_avatar: msg.role === 'bot' ? (msg.botAvatar || '') : '',
          sender_color: msg.role === 'bot' ? (msg.botColor || '') : '',
          tools_used: msg.toolsUsed || [],
          // This component streams the turn itself, so the server must not
          // also answer the mention — otherwise the agent replies twice and
          // both replies are stored.
          dispatch: false,
        }),
      }).catch(() => {}) // Silently ignore persistence errors
    } catch {}
  }

  // On mount: try to load messages from DB, fall back to demo data from config
  useEffect(() => {
    if (messages.length > 0 || demoLoaded.current) return
    demoLoaded.current = true
    ;(async () => {
      try {
        // Try DB first
        const dbRes = await fetch(`${apiBase}/channels/${channel}/messages`)
        if (dbRes.ok) {
          const data = await dbRes.json()
          if (data.messages && data.messages.length > 0) {
            // Map DB messages to ChatMessage format
            const dbMessages: ChatMessage[] = data.messages.map((m: any) => ({
              id: m.id || String(Date.now()),
              role: m.sender_type === 'agent' ? 'bot' : 'user',
              text: m.content,
              timestamp: m.created_at ? new Date(m.created_at).getTime() : Date.now(),
              time: m.created_at ? new Date(m.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '',
              ...(m.sender_type === 'agent' ? {
                botId: m.sender_id,
                botName: m.sender_name,
                botAvatar: m.sender_avatar || '',
                botColor: m.sender_color || '',
                toolsUsed: m.tools_used || [],
              } : {}),
            }))
            setMessages(dbMessages)
            return
          }
        }
      } catch {}

      // Fall back to demo data from config
      try {
        const res = await fetch(`${apiBase}/config`)
        if (!res.ok) return
        const config = await res.json()
        if (!config.demo?.messages?.length) return
        setMessages(config.demo.messages)
        if (config.demo.threads) setThreadReplies(config.demo.threads)
      } catch {}
    })()
  }, [apiBase, messages.length])

  // Supabase Realtime: listen for new messages from other users
  useEffect(() => {
    const sb = getSupabaseClient()
    if (!sb) return
    const realtime = sb
      .channel(`ws-${channel}-realtime`)
      .on('postgres_changes', {
        event: 'INSERT',
        schema: 'public',
        table: 'workspace_messages',
      }, (payload: any) => {
        const m = payload.new
        if (!m || !m.content) return
        // Skip if we already have this message (sent by us)
        setMessages(prev => {
          if (prev.some(msg => msg.id === m.id)) return prev
          const newMsg: ChatMessage = {
            id: m.id,
            role: m.sender_type === 'agent' ? 'bot' : 'user',
            text: m.content,
            timestamp: m.created_at ? new Date(m.created_at).getTime() : Date.now(),
            time: m.created_at ? new Date(m.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '',
            ...(m.sender_type === 'agent' ? {
              botId: m.sender_id,
              botName: m.sender_name,
              botAvatar: m.sender_avatar || '',
              botColor: m.sender_color || '',
              toolsUsed: m.tools_used || [],
            } : {}),
          }
          return [...prev, newMsg]
        })
      })
      .subscribe()

    return () => { sb.removeChannel(realtime) }
  }, [])

  // Smart scroll — only auto-scroll when user is at bottom
  useEffect(() => {
    if (isAtBottom) {
      scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
    } else {
      setHasNewMessage(true)
    }
  }, [messages, typing])

  // Track scroll position
  function handleScroll() {
    const el = scrollRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 50
    setIsAtBottom(atBottom)
    if (atBottom) setHasNewMessage(false)
  }

  function jumpToBottom() {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
    setHasNewMessage(false)
    setIsAtBottom(true)
  }

  // Keyboard shortcuts: Escape to close thread
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === 'Escape' && threadId) setThreadId(null)
    }
    document.addEventListener('keydown', handleKey)
    return () => document.removeEventListener('keydown', handleKey)
  }, [threadId])

  type DetectedMention =
    | { kind: 'agent'; agent: AgentConfig; cleanText: string }
    | { kind: 'user'; user: MentionUser }
    | null

  function detectMention(text: string): DetectedMention {
    // Agent mention takes priority — drives the chat reply path.
    for (const a of agents) {
      const patterns = [`@${a.name.toLowerCase()}`, `@${a.id}`]
      for (const p of patterns) {
        if (text.toLowerCase().includes(p)) {
          return { kind: 'agent', agent: a, cleanText: text.replace(new RegExp(p, 'gi'), '').trim() }
        }
      }
    }
    // Human-user mention — message stays posted, no agent invoked.
    for (const u of users) {
      const patterns = [`@${u.name.toLowerCase()}`, `@${u.id.toLowerCase()}`]
      for (const p of patterns) {
        if (text.toLowerCase().includes(p)) {
          return { kind: 'user', user: u }
        }
      }
    }
    return null
  }

  // Reactions — toggle emoji (add if not mine, remove if mine)
  function handleReact(messageId: string, emoji: string) {
    setMessages(prev => prev.map(m => {
      if (m.id !== messageId) return m
      const reactions = [...(m.reactions || [])]
      const idx = reactions.findIndex(r => r.emoji === emoji)
      if (idx >= 0) {
        if (reactions[idx].mine) {
          // Toggle off — remove or decrement
          if (reactions[idx].count <= 1) reactions.splice(idx, 1)
          else reactions[idx] = { ...reactions[idx], count: reactions[idx].count - 1, mine: false }
        } else {
          reactions[idx] = { ...reactions[idx], count: reactions[idx].count + 1, mine: true }
        }
      } else {
        reactions.push({ emoji, count: 1, mine: true })
      }
      return { ...m, reactions }
    }))
  }

  function handleEdit(messageId: string, newText: string) {
    setMessages(prev => prev.map(m => m.id === messageId ? { ...m, text: newText, edited: true } : m))
  }

  function handleDelete(messageId: string) {
    setMessages(prev => prev.map(m => m.id === messageId ? { ...m, deleted: true } : m))
  }

  function now() { return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) }

  async function send(text: string, images?: string[], attachments?: any[]) {
    const mention = detectMention(text)
    const ts = Date.now()
    const userMsg: ChatMessage = {
      id: ts.toString(), role: 'user', text, timestamp: ts,
      time: now(), images, attachments,
    }
    setMessages(prev => [...prev, userMsg])
    persistMessage(userMsg)

    // Pure human-to-human (or no @-tag): the message is now posted; nothing
    // else to do. No system "tag an agent" tip — that nag belongs to onboarding,
    // not every casual #general chat. Future: notification fanout to tagged
    // human users so they see "you were tagged" indicators.
    if (!mention || mention.kind === 'user') return

    const { agent } = mention
    setTyping(agent.name)
    setThinking('')

    try {
      await chatStream(apiBase, agent, mention.cleanText || text, `${sessionRef.current}-${agent.id}`, {
        onToolCall: (name) => setThinking(`${agent.name}: accessing ${name.replace(/_/g, ' ').replace(/^get /, '')}…`),
        onResponse: (text, toolsUsed, atts) => {
          const rts = Date.now()
          const botMsg: ChatMessage = {
            id: (rts + 1).toString(), role: 'bot', timestamp: rts + 1,
            botId: agent.id, botName: agent.name, botAvatar: agent.avatar, botColor: agent.color,
            text, time: now(), toolsUsed, attachments: atts,
          }
          setMessages(prev => [...prev, botMsg])
          persistMessage(botMsg)
        },
        onError: (msg) => setMessages(prev => [...prev, {
          id: (Date.now() + 1).toString(), role: 'bot', timestamp: Date.now() + 1,
          botName: agent.name, botAvatar: agent.avatar, botColor: agent.color, text: msg, time: now(),
        }]),
      }, { images, attachments })
    } catch {
      setMessages(prev => [...prev, {
        id: (Date.now() + 1).toString(), role: 'bot', timestamp: Date.now() + 1,
        botName: 'System', botAvatar: '⚡', botColor: '#6B7280',
        text: 'Failed to reach the server.', time: now(),
      }])
    } finally {
      setTyping(null); setThinking('')
    }
  }

  async function handleAudio(blob: Blob, duration: number) {
    const lastMsg = [...messages].reverse().find(m => m.role === 'bot' && m.botId)
    const agent = lastMsg?.botId ? agents.find(a => a.id === lastMsg.botId) : agents[0]
    if (!agent) return

    const ts = Date.now()
    const durationStr = `${Math.floor(duration / 60)}:${(duration % 60).toString().padStart(2, '0')}`
    const userMsgId = ts.toString()
    setMessages(prev => [...prev, {
      id: userMsgId, role: 'user', timestamp: ts, text: `🎤 Voice message (${durationStr})`, time: now(),
    }])
    setTyping(agent.name)
    setThinking(`${agent.name}: transcribing voice…`)

    try {
      await chatStream(apiBase, agent, '', `${sessionRef.current}-${agent.id}`, {
        onTranscription: (text) => {
          setMessages(prev => prev.map(m => m.id === userMsgId ? { ...m, text: `🎤 "${text}"` } : m))
          setThinking('')
        },
        onToolCall: (name) => setThinking(`${agent.name}: accessing ${name.replace(/_/g, ' ').replace(/^get /, '')}…`),
        onResponse: (text, toolsUsed, atts) => {
          const rts = Date.now()
          const botMsg: ChatMessage = {
            id: (rts + 1).toString(), role: 'bot', timestamp: rts + 1,
            botId: agent.id, botName: agent.name, botAvatar: agent.avatar, botColor: agent.color,
            text, time: now(), toolsUsed, attachments: atts,
          }
          setMessages(prev => [...prev, botMsg])
          persistMessage(botMsg)
        },
        onError: (msg) => setMessages(prev => prev.map(m => m.id === userMsgId ? { ...m, text: `🎤 ${msg}` } : m)),
      }, { audioBlob: blob })
    } catch {
      setMessages(prev => prev.map(m => m.id === userMsgId ? { ...m, text: '🎤 Voice message failed' } : m))
    } finally {
      setTyping(null); setThinking('')
    }
  }

  const typingAgent = typing ? agents.find(a => a.name === typing) : null
  const threadParent = threadId ? messages.find(m => m.id === threadId) : null

  // Collect unique thread participant avatars
  function getThreadAvatars(parentId: string): string[] {
    const replies = threadReplies[parentId] || []
    const avatars: string[] = []
    const seen = new Set<string>()
    for (const r of replies) {
      const av = r.role === 'bot' ? (r.botAvatar || '🤖') : '👤'
      const key = r.role === 'bot' ? (r.botId || r.botName || av) : 'user'
      if (!seen.has(key)) { seen.add(key); avatars.push(av) }
    }
    return avatars
  }

  async function handleThreadReply(text: string, alsoSendToChannel?: boolean) {
    if (!threadId || !threadParent) return

    // Who answers a thread reply. The parent's bot when there is one — but a
    // thread opened on somebody's *question* has no bot, and this used to
    // return silently: the composer cleared, nothing was sent, and no error
    // appeared anywhere. Fall back to whoever the text addresses, then to the
    // agent the parent addressed, then to the only agent if there is one.
    const mentioned = (s: string) => {
      const m = /@([\w-]+)/.exec(s || '')
      if (!m) return null
      const slug = m[1].toLowerCase()
      return agents.find(a => a.id.toLowerCase() === slug || (a.name || '').toLowerCase() === slug) || null
    }
    const agent =
      (threadParent.botId ? agents.find(a => a.id === threadParent.botId) : null) ||
      mentioned(text) ||
      mentioned(threadParent.text || '') ||
      (agents.length === 1 ? agents[0] : null)

    if (!agent) {
      const ts = Date.now()
      const asked: ChatMessage = {
        id: `${threadId}-r-${ts}`, role: 'user', text, timestamp: ts, time: now(),
      }
      const hint: ChatMessage = {
        id: `${threadId}-r-${ts}-err`, role: 'bot', timestamp: ts, time: now(),
        text:
          'Nobody is addressed in this thread — mention an agent by name, for example @' +
          (agents[0]?.id || 'agent') + ', and I will route it.',
      }
      setThreadReplies(prev => ({ ...prev, [threadId]: [...(prev[threadId] || []), asked, hint] }))
      return
    }

    const ts = Date.now()
    const userMsg: ChatMessage = {
      id: `${threadId}-r-${ts}`, role: 'user', text, timestamp: ts, time: now(),
    }
    setThreadReplies(prev => ({ ...prev, [threadId]: [...(prev[threadId] || []), userMsg] }))
    setThreadTyping(true)
    setThreadThinking('')

    if (alsoSendToChannel) {
      setMessages(prev => [...prev, { ...userMsg, id: `${userMsg.id}-ch` }])
    }

    try {
      const res = await fetch(`${apiBase}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          app_id: agent.id, message: text,
          session_id: `${sessionRef.current}-${agent.id}-thread-${threadId}`,
          thread_id: threadId,
        }),
      })
      if (res.ok) {
        const data = await res.json()
        const rts = Date.now()
        const botReply: ChatMessage = {
          id: `${threadId}-r-${rts + 1}`, role: 'bot', text: data.response, timestamp: rts + 1,
          botId: agent.id, botName: agent.name, botAvatar: agent.avatar, botColor: agent.color,
          time: now(), toolsUsed: data.tools_used || [],
        }
        setThreadReplies(prev => ({ ...prev, [threadId]: [...(prev[threadId] || []), botReply] }))
        // Update reply count + thread avatars on parent
        const avatars = getThreadAvatars(threadId)
        setMessages(prev => prev.map(m =>
          m.id === threadId ? { ...m, replyCount: ((m.replyCount || 0) + 1), threadAvatars: avatars } : m
        ))
        if (alsoSendToChannel) {
          setMessages(prev => [...prev, { ...botReply, id: `${botReply.id}-ch` }])
        }
      }
    } catch {}
    setThreadTyping(false)
    setThreadThinking('')
  }

  // Thread reaction/edit/delete handlers (for replies)
  function handleThreadReact(messageId: string, emoji: string) {
    if (!threadId) return
    setThreadReplies(prev => {
      const replies = [...(prev[threadId] || [])]
      return { ...prev, [threadId]: replies.map(m => {
        if (m.id !== messageId) return m
        const reactions = [...(m.reactions || [])]
        const idx = reactions.findIndex(r => r.emoji === emoji)
        if (idx >= 0) {
          if (reactions[idx].mine) {
            if (reactions[idx].count <= 1) reactions.splice(idx, 1)
            else reactions[idx] = { ...reactions[idx], count: reactions[idx].count - 1, mine: false }
          } else {
            reactions[idx] = { ...reactions[idx], count: reactions[idx].count + 1, mine: true }
          }
        } else {
          reactions.push({ emoji, count: 1, mine: true })
        }
        return { ...m, reactions }
      })}
    })
  }

  function handleThreadEdit(messageId: string, newText: string) {
    if (!threadId) return
    setThreadReplies(prev => ({
      ...prev, [threadId]: (prev[threadId] || []).map(m =>
        m.id === messageId ? { ...m, text: newText, edited: true } : m
      )
    }))
  }

  function handleThreadDelete(messageId: string) {
    if (!threadId) return
    setThreadReplies(prev => ({
      ...prev, [threadId]: (prev[threadId] || []).map(m =>
        m.id === messageId ? { ...m, deleted: true } : m
      )
    }))
  }

  // Render messages with date dividers
  function renderMessages() {
    const elements: React.ReactNode[] = []
    let lastDateKey = ''

    const visible = messages.filter(m => !m.deleted)
    for (let i = 0; i < messages.length; i++) {
      const msg = messages[i]

      // Date divider — use timestamp or parse ID as fallback
      const ts = msg.timestamp || (Number(msg.id) > 1e12 ? Number(msg.id) : 0)
      if (ts) {
        const dateKey = getDateKey(ts)
        if (dateKey !== lastDateKey) {
          lastDateKey = dateKey
          elements.push(<DateDivider key={`date-${dateKey}`} timestamp={ts} />)
        }
      }

      if (msg.deleted) {
        elements.push(<MessageBubble key={msg.id} message={msg} showHeader={false} userName={userName} mentionableKinds={mentionableKinds} />)
        continue
      }

      const isBot = msg.role === 'bot'
      const prevVisible = messages.slice(0, i).filter(m => !m.deleted).at(-1)
      const prevDiff = !prevVisible || prevVisible.role !== msg.role || (isBot && prevVisible.botId !== msg.botId)

      // Inject thread avatars from current threadReplies state
      const msgWithAvatars = msg.replyCount && msg.replyCount > 0 && !msg.threadAvatars?.length
        ? { ...msg, threadAvatars: getThreadAvatars(msg.id) }
        : msg

      elements.push(
        <MessageBubble key={msg.id} message={msgWithAvatars} showHeader={prevDiff} userName={userName}
          mentionableKinds={mentionableKinds}
          onOpenThread={(id) => setThreadId(id)}
          onReact={handleReact} onEdit={handleEdit} onDelete={handleDelete} />
      )
    }
    return elements
  }

  return (
    <div className="flex h-screen">
    <div className="flex flex-col flex-1 min-w-0">
      {/* Header */}
      <div className="h-[49px] border-b border-gray-200 bg-white flex items-center px-4 max-lg:pl-16 shrink-0">
        <div className="flex items-center gap-1.5 min-w-0">
          <Hash className="h-[18px] w-[18px] text-gray-500 shrink-0" />
          <span className="font-bold text-gray-900 text-[15px] truncate">{channel}</span>
        </div>
      </div>

      {/* Messages */}
      <div className="relative flex-1">
        <div ref={scrollRef} onScroll={handleScroll}
          // space-y is gone: spacing now belongs to the message itself, which knows
          // whether it opens a new author's turn. The first-child overrides stop the
          // opening message from drawing a rule against the channel header.
          className="absolute inset-0 overflow-y-auto bg-white px-4 lg:px-5 py-2
                     [&>*:first-child]:!mt-0 [&>*:first-child]:!pt-1 [&>*:first-child]:!border-t-0">
          {messages.length === 0 && (
            <div className="text-center py-16 text-gray-400">
              <Hash className="h-12 w-12 mx-auto mb-3 text-gray-200" />
              <p className="text-sm font-medium text-gray-500">Welcome to #{channel}</p>
              <p className="text-xs mt-1">Tag an AI team member to start a conversation.</p>
              <div className="mt-4 flex flex-wrap justify-center gap-2">
                {agents.map(a => (
                  <span key={a.id} className="text-xs px-2.5 py-1 rounded-full border"
                    style={{ borderColor: a.color + '40', color: a.color }}>
                    {a.avatar} @{a.name}
                  </span>
                ))}
              </div>
            </div>
          )}

          {renderMessages()}

          {typing && typingAgent && (
            <TypingIndicator name={typingAgent.name} avatar={typingAgent.avatar} color={typingAgent.color} thinkingText={thinking} />
          )}
        </div>

        {/* Jump to bottom pill */}
        {hasNewMessage && !isAtBottom && (
          <button onClick={jumpToBottom}
            className="absolute bottom-3 left-1/2 -translate-x-1/2 flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 text-white text-xs font-medium rounded-full shadow-lg hover:bg-blue-700 transition-colors z-10">
            <ArrowDown className="h-3 w-3" />
            New messages
          </button>
        )}
      </div>

      {/* Input */}
      <div className="px-4 lg:px-5 pb-3 bg-white shrink-0">
        <MessageComposer
          placeholder={`Message #${channel} — tag @anyone…`}
          agents={agents.map(a => ({ id: a.id, name: a.name, avatar: a.avatar, color: a.color }))}
          users={users}
          onSend={send}
          onSendAudio={handleAudio}
          draftKey={channel}
        />
      </div>
    </div>

    {/* Thread panel with resize handle */}
    {threadId && threadParent && (<>
      <ResizeHandle direction="horizontal"
        onResize={(delta) => setThreadWidth(w => Math.max(280, Math.min(600, w - delta)))}
        onResizeEnd={() => localStorage.setItem('ws:threadPanelWidth', String(threadWidth))} />
      <ThreadPanel
        parentMessage={threadParent}
        replies={threadReplies[threadId] || []}
        onClose={() => setThreadId(null)}
        onSendReply={handleThreadReply}
        onReact={handleThreadReact}
        onEdit={handleThreadEdit}
        onDelete={handleThreadDelete}
        typing={threadTyping}
        thinkingText={threadThinking}
        botAvatar={threadParent.botAvatar}
        botColor={threadParent.botColor}
        botName={threadParent.botName}
        userName={userName}
        channelName={`#${channel}`}
        width={threadWidth}
      />
    </>)}
    </div>
  )
}
