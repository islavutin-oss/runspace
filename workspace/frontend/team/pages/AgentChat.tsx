'use client'

import { useState, useRef, useEffect, useCallback } from 'react'
import { PanelRightOpen, PanelRightClose, Phone, Search, Bookmark, ArrowDown, RotateCcw } from 'lucide-react'
import { MessageBubble, MessageComposer, TypingIndicator, WidgetIntentProvider, type ChatMessage } from '../../shared/components'
import ThreadPanel from '../components/ThreadPanel'
import DateDivider, { getDateKey } from '../../shared/components/DateDivider'
import ResizeHandle from '../components/ResizeHandle'
import type { AgentConfig } from '../components/Sidebar'
import { useUser } from './WorkspaceLayout'
import { useLocalState } from '../../shared/hooks/useLocalState'
import { chatStream } from '../../shared/hooks/useChatStream'

interface AgentChatProps {
  agent: AgentConfig
  apiBase?: string
  userName?: string
  initialMessages?: ChatMessage[]
}

export default function AgentChat({ agent, apiBase = '/api/workspace', userName: userNameProp = 'You', initialMessages = [] }: AgentChatProps) {
  const currentUser = useUser()
  const userName = currentUser?.name || userNameProp
  const userEmail = currentUser?.email
  const storageKey = userEmail ? `ws:dm:${agent.id}:${userEmail}:messages` : `ws:dm:${agent.id}:messages`
  const [messages, setMessages] = useLocalState<ChatMessage[]>(storageKey, initialMessages)
  const [typing, setTyping] = useState(false)
  const [thinking, setThinking] = useState('')
  const [threadId, setThreadId] = useState<string | null>(null)
  const threadStorageKey = userEmail ? `ws:dm:${agent.id}:${userEmail}:threads` : `ws:dm:${agent.id}:threads`
  const [threadReplies, setThreadReplies] = useLocalState<ChatMessage[]>(threadStorageKey, [])
  const [threadTyping, setThreadTyping] = useState(false)
  const [threadThinking, setThreadThinking] = useState('')
  const [showProfile, setShowProfile] = useState(false)
  const [isAtBottom, setIsAtBottom] = useState(true)
  const [hasNewMessage, setHasNewMessage] = useState(false)
  const [threadWidth, setThreadWidth] = useState(() => {
    if (typeof window === 'undefined') return 320
    const saved = localStorage.getItem('ws:threadPanelWidth')
    return saved ? Math.max(280, Math.min(600, Number(saved))) : 320
  })
  const scrollRef = useRef<HTMLDivElement>(null)
  const sessionRef = useRef<string>('')
  if (!sessionRef.current && typeof window !== 'undefined') {
    // Deterministic session_id: (agent, user) — stable across devices/localStorage clears.
    // Conversation history persists even if browser storage is reset.
    if (userEmail) {
      sessionRef.current = `${agent.id}-${userEmail.replace(/[^a-zA-Z0-9]/g, '_')}`
    } else {
      // Fallback for unauthenticated: cache in localStorage to avoid new session per tab
      const sessionKey = `ws-session-${agent.id}`
      sessionRef.current = localStorage.getItem(sessionKey) || `${agent.id}-${Date.now()}`
      localStorage.setItem(sessionKey, sessionRef.current)
    }
  }

  // Load chat history from server on mount (DB-backed = source of truth).
  // Server history always wins over localStorage to avoid fragmented views.
  const fetchHistory = useCallback(() => {
    if (!sessionRef.current) return
    fetch(`${apiBase}/chat/history?app_id=${agent.id}&session_id=${encodeURIComponent(sessionRef.current)}`)
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (!data?.messages?.length) return
        setMessages(prev => {
          const ts = Date.now()
          const server = data.messages.map((m: { role: string; content: string; attachments?: { name: string; url: string; size: number; type: string }[] }, i: number) => ({
            id: `hist-${i}`,
            role: m.role === 'assistant' ? 'bot' as const : 'user' as const,
            botId: m.role === 'assistant' ? agent.id : undefined,
            botName: m.role === 'assistant' ? agent.name : undefined,
            botAvatar: m.role === 'assistant' ? agent.avatar : undefined,
            botColor: m.role === 'assistant' ? agent.color : undefined,
            text: m.content,
            time: new Date(ts - (data.messages.length - i) * 60000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
            attachments: m.attachments,
          }))
          // If local has more messages than server, something is in-flight — keep local.
          if (prev.length > server.length) return prev
          return server
        })
      })
      .catch(() => {})
  }, [agent.id, apiBase])

  // Initial load.
  useEffect(() => { fetchHistory() }, [fetchHistory])

  // Pending-reply poller: when the last message in local state is from
  // the user, the agent reply hasn't arrived (either it's still streaming
  useEffect(() => {
    const last = messages[messages.length - 1]
    if (!last || last.role !== 'user' || typing) return  // typing = active SSE; no need to poll
    const id = setInterval(fetchHistory, 3000)
    return () => clearInterval(id)
  }, [messages, typing, fetchHistory])

  // Smart scroll
  useEffect(() => {
    if (isAtBottom) {
      scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
    } else {
      setHasNewMessage(true)
    }
  }, [messages, typing])

  function handleScroll() {
    const el = scrollRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 50
    setIsAtBottom(atBottom)
    if (atBottom) setHasNewMessage(false)
  }

  function jumpToBottom() {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
    setHasNewMessage(false); setIsAtBottom(true)
  }

  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === 'Escape' && threadId) setThreadId(null)
    }
    document.addEventListener('keydown', handleKey)
    return () => document.removeEventListener('keydown', handleKey)
  }, [threadId])

  // Reaction toggle
  function handleReact(messageId: string, emoji: string) {
    setMessages(prev => prev.map(m => {
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
    }))
  }

  function handleEdit(messageId: string, newText: string) {
    setMessages(prev => prev.map(m => m.id === messageId ? { ...m, text: newText, edited: true } : m))
  }

  function handleDelete(messageId: string) {
    setMessages(prev => prev.map(m => m.id === messageId ? { ...m, deleted: true } : m))
  }

  function now() { return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) }

  /** Send a chat message and resolve once the assistant's reply has
   *  fully landed. Resolves to `{ok: true, reply}` on success or
   *  `{ok: false, error}` on transport / agent error. Used by the
   *  WidgetIntent dispatcher so widgets can flip from pending →
   *  applied / failed states realistically (not optimistically). */
  async function sendMessage(
    text: string, images?: string[], attachments?: any[],
  ): Promise<{ ok: boolean; reply?: string; error?: string }> {
    const ts = Date.now()
    setMessages(prev => [...prev, {
      id: ts.toString(), role: 'user', text, timestamp: ts, time: now(), images, attachments,
    }])
    setTyping(true); setThinking('')

    let resolved = false
    let outcome: { ok: boolean; reply?: string; error?: string } = { ok: false, error: 'no reply' }

    try {
      await chatStream(apiBase, agent, text, sessionRef.current, {
        onToolCall: (name) => setThinking(`Accessing ${name.replace(/_/g, ' ').replace(/^get /, '')}…`),
        onResponse: (replyText, toolsUsed, atts) => {
          const rts = Date.now()
          setMessages(prev => [...prev, {
            id: (rts + 1).toString(), role: 'bot', text: replyText, timestamp: rts + 1,
            botId: agent.id, botName: agent.name, botAvatar: agent.avatar, botColor: agent.color,
            time: now(), toolsUsed, attachments: atts,
          }])
          if (!resolved) { outcome = { ok: true, reply: replyText }; resolved = true }
        },
        onError: (msg) => {
          setMessages(prev => [...prev, {
            id: (Date.now() + 1).toString(), role: 'bot', text: msg, timestamp: Date.now() + 1,
            botId: agent.id, botName: agent.name, botAvatar: agent.avatar, botColor: agent.color, time: now(),
          }])
          if (!resolved) { outcome = { ok: false, error: msg }; resolved = true }
        },
      }, { images, attachments, senderName: userName !== 'You' ? userName : undefined })
    } catch (e: unknown) {
      const errMsg = 'Could not reach the server.'
      setMessages(prev => [...prev, {
        id: (Date.now() + 1).toString(), role: 'bot', text: errMsg, timestamp: Date.now() + 1,
        botId: agent.id, botName: agent.name, botAvatar: agent.avatar, botColor: agent.color, time: now(),
      }])
      if (!resolved) { outcome = { ok: false, error: errMsg }; resolved = true }
    } finally { setTyping(false); setThinking('') }

    return outcome
  }

  async function sendThreadReply(text: string) {
    if (!threadId) return
    const ts = Date.now()
    const userMsg: ChatMessage = {
      id: ts.toString(), role: 'user', text, timestamp: ts, time: now(), threadId,
    }
    setThreadReplies(prev => [...prev, userMsg])
    setThreadTyping(true)
    try {
      const res = await fetch(`${apiBase}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          app_id: agent.id, message: text,
          session_id: `${sessionRef.current}-thread-${threadId}`, thread_id: threadId,
        }),
      })
      if (res.ok) {
        const data = await res.json()
        const rts = Date.now()
        setThreadReplies(prev => [...prev, {
          id: (rts + 1).toString(), role: 'bot', text: data.response, timestamp: rts + 1,
          botId: agent.id, botName: agent.name, botAvatar: agent.avatar, botColor: agent.color,
          time: now(), toolsUsed: data.tools_used || [], threadId,
        }])
        setMessages(prev => prev.map(m => m.id === threadId
          ? { ...m, replyCount: (m.replyCount || 0) + 1, threadAvatars: [agent.avatar, '👤'] }
          : m
        ))
      }
    } catch {}
    setThreadTyping(false)
  }

  async function handleAudio(blob: Blob, duration: number) {
    const ts = Date.now()
    const durationStr = `${Math.floor(duration / 60)}:${(duration % 60).toString().padStart(2, '0')}`
    const userMsgId = ts.toString()
    setMessages(prev => [...prev, {
      id: userMsgId, role: 'user', timestamp: ts, text: `🎤 Voice message (${durationStr})`, time: now(),
    }])
    setTyping(true); setThinking('Transcribing voice…')

    try {
      await chatStream(apiBase, agent, '', sessionRef.current, {
        onTranscription: (text) => {
          setMessages(prev => prev.map(m => m.id === userMsgId ? { ...m, text: `🎤 "${text}"` } : m))
          setThinking('')
        },
        onToolCall: (name) => setThinking(`Accessing ${name.replace(/_/g, ' ').replace(/^get /, '')}…`),
        onResponse: (text, toolsUsed, atts) => {
          const rts = Date.now()
          setMessages(prev => [...prev, {
            id: (rts + 1).toString(), role: 'bot', text, timestamp: rts + 1,
            botId: agent.id, botName: agent.name, botAvatar: agent.avatar, botColor: agent.color,
            time: now(), toolsUsed, attachments: atts,
          }])
        },
        onError: (msg) => setMessages(prev => prev.map(m => m.id === userMsgId ? { ...m, text: `🎤 ${msg}` } : m)),
      }, { audioBlob: blob, senderName: userName !== 'You' ? userName : undefined })
    } catch {
      setMessages(prev => prev.map(m => m.id === userMsgId ? { ...m, text: '🎤 Voice message failed' } : m))
    } finally { setTyping(false); setThinking('') }
  }

  function openThread(messageId: string) {
    setThreadId(messageId)
    setThreadReplies([])
  }

  const parentMsg = threadId ? messages.find(m => m.id === threadId) : null

  // Render messages with date dividers
  function renderMessages() {
    const elements: React.ReactNode[] = []
    let lastDateKey = ''
    const filtered = messages.filter(m => !m.threadId)

    for (let i = 0; i < filtered.length; i++) {
      const msg = filtered[i]
      const ts = msg.timestamp || (Number(msg.id) > 1e12 ? Number(msg.id) : 0)
      if (ts) {
        const dateKey = getDateKey(ts)
        if (dateKey !== lastDateKey) { lastDateKey = dateKey; elements.push(<DateDivider key={`date-${dateKey}`} timestamp={ts} />) }
      }

      if (msg.deleted) {
        elements.push(<MessageBubble key={msg.id} message={msg} showHeader={false} userName={userName} />)
        continue
      }

      const prevVisible = filtered.slice(0, i).filter(m => !m.deleted).at(-1)
      const prevDiff = !prevVisible || prevVisible.role !== msg.role
      elements.push(
        <MessageBubble key={msg.id} message={msg} showHeader={prevDiff}
          userName={userName} onOpenThread={openThread}
          onReact={handleReact} onEdit={handleEdit} onDelete={handleDelete} />
      )
    }
    return elements
  }

  return (
    <WidgetIntentProvider dispatch={async (intent) => sendMessage(intent.text)}>
    <div className="flex h-screen">
      <div className="flex-1 flex flex-col min-w-0">
        {/* Header */}
        <div className="h-[49px] border-b border-gray-200 bg-white flex items-center justify-between px-4 max-lg:pl-16 shrink-0">
          <div className="flex items-center gap-2">
            <span className="text-lg">{agent.avatar}</span>
            <span className="font-bold text-gray-900 text-[15px]">{agent.name}</span>
            <span className="w-2 h-2 rounded-full bg-green-400" />
            <span className="text-xs text-gray-400 hidden sm:inline">{agent.role}</span>
          </div>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={async () => {
                if (!sessionRef.current) return
                if (!window.confirm(`Reset conversation with ${agent.name}? This clears the chat history for this session — the agent will forget what you've said and start fresh.`)) return
                try {
                  await fetch(
                    `${apiBase}/chat/history?app_id=${agent.id}&session_id=${encodeURIComponent(sessionRef.current)}`,
                    { method: 'DELETE' }
                  )
                } catch { /* best-effort — clear UI anyway */ }
                setMessages([])
              }}
              title="Reset conversation — agent forgets this thread"
              className="p-1.5 rounded hover:bg-gray-100 text-gray-400 hover:text-gray-700"
            >
              <RotateCcw className="h-4 w-4" />
            </button>
            <button className="p-1.5 rounded hover:bg-gray-100 text-gray-400 hover:text-gray-600"><Phone className="h-4 w-4" /></button>
            <button className="p-1.5 rounded hover:bg-gray-100 text-gray-400 hover:text-gray-600"><Search className="h-4 w-4" /></button>
            <button className="p-1.5 rounded hover:bg-gray-100 text-gray-400 hover:text-gray-600"><Bookmark className="h-4 w-4" /></button>
            <button onClick={() => setShowProfile(!showProfile)}
              className={`p-1.5 rounded transition-colors ${showProfile ? 'bg-blue-50 text-blue-600' : 'hover:bg-gray-100 text-gray-400'}`}>
              {showProfile ? <PanelRightClose className="h-4 w-4" /> : <PanelRightOpen className="h-4 w-4" />}
            </button>
          </div>
        </div>

        {/* Messages */}
        <div className="relative flex-1">
          <div ref={scrollRef} onScroll={handleScroll} className="absolute inset-0 overflow-y-auto bg-white">
            <div className="px-5 pb-4 space-y-0.5">
              {messages.length === 0 && !typing ? (
                <EmptyDM agent={agent} onStart={sendMessage} />
              ) : renderMessages()}
              {typing && <TypingIndicator name={agent.name} avatar={agent.avatar} color={agent.color} thinkingText={thinking} />}
            </div>
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
          <MessageComposer placeholder={`Message ${agent.name}`} onSend={sendMessage}
            onSendAudio={handleAudio} disabled={typing} draftKey={`dm-${agent.id}`} />
        </div>
      </div>

      {/* Thread panel with resize handle */}
      {threadId && parentMsg && (<>
        <ResizeHandle direction="horizontal"
          onResize={(delta) => setThreadWidth(w => Math.max(280, Math.min(600, w - delta)))}
          onResizeEnd={() => localStorage.setItem('ws:threadPanelWidth', String(threadWidth))} />
        <ThreadPanel
          parentMessage={parentMsg} replies={threadReplies}
          onClose={() => setThreadId(null)} onSendReply={sendThreadReply}
          typing={threadTyping} thinkingText={threadThinking}
          botAvatar={agent.avatar} botColor={agent.color} botName={agent.name} userName={userName}
          width={threadWidth}
        />
      </>)}

      {/* Profile panel */}
      {showProfile && !threadId && (
        <div className="w-72 border-l border-gray-200 bg-white h-screen overflow-y-auto shrink-0">
          <div className="h-12 border-b border-gray-200 flex items-center justify-between px-4">
            <span className="text-sm font-bold text-gray-900">Profile</span>
            <button onClick={() => setShowProfile(false)} className="p-1 rounded hover:bg-gray-100 text-gray-400">
              <PanelRightClose className="h-4 w-4" />
            </button>
          </div>
          <div className="p-4 text-center mb-4">
            <div className="w-20 h-20 rounded-xl mx-auto flex items-center justify-center text-4xl mb-3"
              style={{ backgroundColor: agent.color + '12' }}>{agent.avatar}</div>
            <h3 className="font-bold text-gray-900 text-lg">{agent.name}</h3>
            <p className="text-xs mt-0.5" style={{ color: agent.color }}>{agent.role}</p>
          </div>
          {agent.description && (
            <div className="px-4 pb-3 border-t border-gray-100 pt-3">
              <p className="text-[11px] font-semibold text-gray-400 uppercase tracking-wider mb-2">About</p>
              <p className="text-xs text-gray-600 leading-relaxed">{agent.description}</p>
            </div>
          )}
          {agent.capabilities && agent.capabilities.length > 0 && (
            <div className="px-4 pb-3 border-t border-gray-100 pt-3">
              <p className="text-[11px] font-semibold text-gray-400 uppercase tracking-wider mb-2">Capabilities</p>
              <div className="flex flex-wrap gap-1">
                {agent.capabilities.map(cap => (
                  <span key={cap} className="text-[11px] px-2 py-0.5 rounded-md font-medium"
                    style={{ backgroundColor: agent.color + '10', color: agent.color }}>{cap}</span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
    </WidgetIntentProvider>
  )
}

/** Shown while a 1-1 has no messages.
 *
 *  A direct message opened from the sidebar used to be a blank pane with a
 *  "Message <name>" box and nothing else — the visitor had to guess both what
 *  the agent knew and how to ask. The questions come from workspace.yml
 *  (`apps.<id>.suggestions`) so they are config, not copy baked into the UI,
 *  and they are per-agent: the advisor's pricing questions are the wrong
 *  opening for the catalogue desk.
 *
 *  Nothing is pre-written into the conversation. Clicking a suggestion sends it
 *  as the visitor's own first message and the agent answers for real, so the
 *  transcript is never a transcript of something that did not happen.
 */
function EmptyDM({ agent, onStart }: { agent: AgentConfig; onStart: (text: string) => void }) {
  const suggestions = agent.suggestions || []
  return (
    <div className="flex flex-col items-center justify-center h-full px-6 py-12 max-w-2xl mx-auto w-full">
      <div className="w-14 h-14 rounded-2xl flex items-center justify-center text-3xl mb-4"
        style={{ backgroundColor: (agent.color || '#6B7280') + '12' }}>
        {agent.avatar}
      </div>
      <h2 className="text-xl font-semibold text-gray-900 mb-1 text-center">{agent.name}</h2>
      {agent.role && (
        <p className="text-[14px] text-gray-500 mb-8 text-center leading-relaxed">{agent.role}</p>
      )}
      {suggestions.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 w-full">
          {suggestions.map(s => (
            <button key={s} onClick={() => onStart(s)}
              className="text-left px-4 py-3 rounded-xl border border-gray-200 hover:border-gray-300 hover:bg-gray-50 hover:shadow-sm transition-all text-[13px] text-gray-700 leading-snug">
              {s}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
