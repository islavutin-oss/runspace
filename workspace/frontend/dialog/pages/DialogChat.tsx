'use client'

/**
 * ChatGPT/Claude-style dialog page — fully self-contained.
 *
 * Layout: thread sidebar | conversation pane.
 *
 * Intentionally does NOT import `AgentChat`, `MessageBubble`, or
 * `MessageComposer` — those are the Slack-style channel components.
 * Sharing them via a `layout` prop caused regressions where DM-only
 * visual choices leaked into multi-user channels (2026-04-29 incident:
 * every human's posts stacked right-aligned in #general). Keeping
 * channels and dialogs in separate files is the deliberate design.
 *
 * What this file owns:
 *   - thread sidebar (mobile drawer pattern via ThreadListSidebar)
 *   - top bar (agent picker, hamburger on mobile)
 *   - empty state (2×2 suggestion card grid)
 *   - DialogConversation (streaming + per-thread message store +
 *     DialogMessageRow + DialogComposer)
 *
 * Streaming, history, attachments are shared with AgentChat through
 * `chatStream()` (in hooks/useChatStream) — only the rendering and
 * composer are dialog-specific.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowDown, Menu } from 'lucide-react'
import { useUser } from '../../team/pages/WorkspaceLayout'
import type { AgentConfig } from '../../team/pages/WorkspaceLayout'
import ThreadListSidebar from '../components/ThreadListSidebar'
import AgentPicker from '../components/AgentPicker'
import DialogMessageRow from '../components/DialogMessageRow'
import DialogComposer from '../components/DialogComposer'
import TypingIndicator from '../../shared/components/TypingIndicator'
import type { ChatMessage, FileAttachment } from '../../shared/components/MessageBubble'
import { useThreads } from '../hooks/useThreads'
import { useLocalState } from '../../shared/hooks/useLocalState'
import { chatStream } from '../../shared/hooks/useChatStream'
import type { ThreadStore } from '../components/threadStore'

export interface DialogChatLabels {
  sidebar?: {
    newChat?: string; loading?: string
    emptyTitle?: string; emptyHint?: string
    buckets?: { today?: string; yesterday?: string; week?: string; older?: string }
    rename?: string; delete?: string; deleteConfirm?: string; untitled?: string
  }
  empty?: {
    headlinePrefix?: string
    suggestions?: string[]
  }
  noAgents?: string
  untitledThread?: string
  composerPlaceholderPrefix?: string
}

interface DialogChatProps {
  agents: AgentConfig[]
  apiBase?: string
  userId?: string
  userName?: string
  store?: ThreadStore
  workspaceName?: string
  defaultAgentId?: string
  labels?: DialogChatLabels
}

const DEFAULT_LABELS = {
  empty: {
    headlinePrefix: 'Start a conversation with ',
    suggestions: [
      'What can you help me with?',
      'Summarize what you do.',
      'Walk me through an example task.',
    ],
  },
  noAgents: 'Configure at least one agent in workspace.yml to start chatting.',
  untitledThread: 'Untitled',
  composerPlaceholderPrefix: 'Message ',
}

const PENDING_PROMPT_KEY = 'ws:dialog:pending-prompt'


export default function DialogChat({
  agents, apiBase = '/api/workspace', userId: userIdProp, userName: userNameProp,
  store, workspaceName, defaultAgentId, labels,
}: DialogChatProps) {
  const currentUser = useUser()
  const userId = userIdProp || currentUser?.email || 'anonymous'
  const userName = userNameProp || currentUser?.name || 'You'

  const L = {
    ...DEFAULT_LABELS,
    ...labels,
    empty: { ...DEFAULT_LABELS.empty, ...(labels?.empty || {}) },
  }

  const [activeAgentId, setActiveAgentId] = useState<string>(
    defaultAgentId || agents[0]?.id || ''
  )
  useEffect(() => {
    if (!agents.find(a => a.id === activeAgentId) && agents[0]) {
      setActiveAgentId(agents[0].id)
    }
  }, [agents, activeAgentId])

  const { threads, activeThread, loading, selectThread, createThread, renameThread, deleteThread, touchActive } =
    useThreads({ userId, agentId: activeAgentId, store })

  const activeAgent = useMemo(
    () => agents.find(a => a.id === activeAgentId) || agents[0] || null,
    [agents, activeAgentId],
  )

  // Track suggestion-pick prompts per-thread so DialogConversation can
  // auto-send on first mount (re-mounts on thread change via key).
  const [pendingByThread, setPendingByThread] = useState<Record<string, string>>({})
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)

  async function handleNewChat() {
    if (!activeAgent) return
    await createThread('')
    setMobileSidebarOpen(false)
  }

  async function handleSuggestionPick(prompt: string) {
    if (!activeAgent) return
    const t = await createThread(prompt)
    setPendingByThread(prev => ({ ...prev, [t.id]: prompt }))
  }

  function handleAgentChange(id: string) {
    setActiveAgentId(id)
    selectThread(null)
  }

  // External hand-off (e.g., dashboard "Ask Iris" buttons writing to
  // sessionStorage). Consume once active agent is resolved.
  useEffect(() => {
    if (!activeAgent) return
    if (typeof window === 'undefined') return
    let pending: string | null = null
    try { pending = window.sessionStorage.getItem(PENDING_PROMPT_KEY) } catch { return }
    if (!pending) return
    try { window.sessionStorage.removeItem(PENDING_PROMPT_KEY) } catch { /* ignore */ }
    void handleSuggestionPick(pending)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeAgent?.id])

  return (
    <div className="flex h-full bg-white relative">
      {/* Sidebar — drawer on mobile, static on md+ */}
      <div
        className={`${mobileSidebarOpen ? 'translate-x-0' : '-translate-x-full'} md:translate-x-0
          fixed md:static inset-y-0 left-0 z-40 h-full transition-transform duration-200 ease-out`}
      >
        <ThreadListSidebar
          threads={threads}
          activeId={activeThread?.id || null}
          loading={loading}
          onSelect={(id) => { selectThread(id); setMobileSidebarOpen(false) }}
          onCreate={handleNewChat}
          onRename={renameThread}
          onDelete={deleteThread}
          workspaceName={workspaceName}
          labels={L.sidebar}
        />
      </div>
      {mobileSidebarOpen && (
        <div className="fixed inset-0 bg-black/30 z-30 md:hidden"
             onClick={() => setMobileSidebarOpen(false)} aria-hidden="true" />
      )}

      <div className="flex-1 flex flex-col min-w-0">
        {/* Top bar — minimal, no border */}
        <div className="h-12 bg-white flex items-center px-3 shrink-0 gap-2">
          <button onClick={() => setMobileSidebarOpen(true)}
            className="md:hidden p-1.5 rounded hover:bg-gray-100 text-gray-600 shrink-0"
            aria-label="Open menu">
            <Menu className="h-5 w-5" />
          </button>
          {activeAgent && agents.length > 1 ? (
            <AgentPicker agents={agents} activeId={activeAgentId} onChange={handleAgentChange} />
          ) : activeAgent ? (
            <div className="flex items-center gap-2 px-2 py-1">
              <span className="text-base">{activeAgent.avatar}</span>
              <span className="text-sm font-semibold text-gray-900">{activeAgent.name}</span>
            </div>
          ) : (
            <div className="text-sm text-gray-400 px-2">No agents available</div>
          )}
        </div>

        {/* Body */}
        {!activeAgent ? (
          <div className="flex-1 flex items-center justify-center text-sm text-gray-400">
            {L.noAgents}
          </div>
        ) : !activeThread ? (
          <EmptyState
            agent={activeAgent}
            headlinePrefix={L.empty.headlinePrefix}
            // The agent's own openers when it has them, the workspace list
            // otherwise. Per-agent matters here for the same reason it does in
            // a 1-1: the advisor's pricing questions are the wrong opening for
            // the catalogue desk, and this surface lets you switch agent.
            suggestions={
              (activeAgent as { suggestions?: string[] })?.suggestions?.length
                ? (activeAgent as { suggestions?: string[] }).suggestions!
                : L.empty.suggestions
            }
            onStart={(prompt) => { void handleSuggestionPick(prompt) }}
          />
        ) : (
          <DialogConversation
            key={activeThread.id}
            agent={activeAgent}
            apiBase={apiBase}
            userId={userId}
            userName={userName}
            threadId={activeThread.id}
            autoSendPrompt={pendingByThread[activeThread.id]}
            composerPlaceholder={`${L.composerPlaceholderPrefix}${activeAgent.name}`}
            onUserMessage={(text) => { void touchActive({ incrementMessages: 1, titleIfEmpty: text }) }}
          />
        )}
      </div>
    </div>
  )
}


// ── Empty state (2×2 suggestion cards) ─────────────────────────────────────

function EmptyState({ agent, onStart, headlinePrefix, suggestions }: {
  agent: AgentConfig
  onStart(prompt: string): void
  headlinePrefix: string
  suggestions: string[]
}) {
  return (
    <div className="flex-1 flex flex-col items-center justify-center px-6 max-w-3xl mx-auto w-full">
      <div className="w-14 h-14 rounded-2xl flex items-center justify-center text-3xl mb-4"
        style={{ backgroundColor: agent.color + '12' }}>
        {agent.avatar}
      </div>
      <h2 className="text-2xl font-semibold text-gray-900 mb-1 text-center">
        {headlinePrefix}{agent.name}
      </h2>
      {agent.role && <p className="text-[15px] text-gray-500 mb-8 text-center">{agent.role}</p>}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 w-full">
        {suggestions.map(s => (
          <button key={s} onClick={() => onStart(s)}
            className="text-left px-4 py-4 rounded-2xl border border-gray-200 hover:border-gray-300 hover:bg-gray-50 hover:shadow-sm transition-all text-[14px] text-gray-700 leading-snug">
            {s}
          </button>
        ))}
      </div>
    </div>
  )
}


// ── Conversation pane (one active thread) ──────────────────────────────────
//
// Owns: messages state, streaming, scroll, composer. Re-mounts on thread
// change via the parent's `key={activeThread.id}` so state cleanly resets.

interface DialogConversationProps {
  agent: AgentConfig
  apiBase: string
  userId: string
  userName: string
  threadId: string
  autoSendPrompt?: string
  composerPlaceholder: string
  onUserMessage(text: string): void
}

function DialogConversation({
  agent, apiBase, userId, userName, threadId,
  autoSendPrompt, composerPlaceholder, onUserMessage,
}: DialogConversationProps) {
  const storageScope = `dialog-${threadId}`
  const messagesKey = `ws:dm:${agent.id}:${storageScope}:messages`
  const [messages, setMessages] = useLocalState<ChatMessage[]>(messagesKey, [])
  const [typing, setTyping] = useState(false)
  const [thinking, setThinking] = useState('')
  const sessionId = threadId  // 1:1 — thread id IS the chat session id

  const scrollRef = useRef<HTMLDivElement>(null)
  const [isAtBottom, setIsAtBottom] = useState(true)
  const [hasNewMessage, setHasNewMessage] = useState(false)

  function now() { return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) }

  function scrollToBottom() {
    const el = scrollRef.current
    if (!el) return
    el.scrollTop = el.scrollHeight
    setIsAtBottom(true); setHasNewMessage(false)
  }

  function handleScroll() {
    const el = scrollRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60
    setIsAtBottom(atBottom)
    if (atBottom) setHasNewMessage(false)
  }

  useEffect(() => {
    if (isAtBottom) scrollToBottom()
    else if (messages.length) setHasNewMessage(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages.length])

  async function sendMessage(text: string, images?: string[], attachments?: FileAttachment[]) {
    const ts = Date.now()
    setMessages(prev => [...prev, {
      id: ts.toString(), role: 'user', text, timestamp: ts, time: now(), images, attachments,
    }])
    onUserMessage(text)
    setTyping(true); setThinking('')
    try {
      await chatStream(apiBase, agent, text, sessionId, {
        onToolCall: (name) => setThinking(`Accessing ${name.replace(/_/g, ' ').replace(/^get /, '')}…`),
        onResponse: (txt, toolsUsed, atts) => {
          const rts = Date.now()
          setMessages(prev => [...prev, {
            id: (rts + 1).toString(), role: 'bot', text: txt, timestamp: rts + 1,
            botId: agent.id, botName: agent.name, botAvatar: agent.avatar, botColor: agent.color,
            time: now(), toolsUsed, attachments: atts,
          }])
        },
        onError: (msg) => setMessages(prev => [...prev, {
          id: (Date.now() + 1).toString(), role: 'bot', text: msg, timestamp: Date.now() + 1,
          botId: agent.id, botName: agent.name, botAvatar: agent.avatar, botColor: agent.color, time: now(),
        }]),
      }, { images, attachments, senderName: userName !== 'You' ? userName : undefined })
    } catch {
      setMessages(prev => [...prev, {
        id: (Date.now() + 1).toString(), role: 'bot', text: 'Could not reach the server.', timestamp: Date.now() + 1,
        botId: agent.id, botName: agent.name, botAvatar: agent.avatar, botColor: agent.color, time: now(),
      }])
    } finally { setTyping(false); setThinking('') }
  }

  // Auto-send a one-shot prompt on mount when set (suggestion click /
  // sessionStorage hand-off). Only fires when there's no prior history.
  const autoSentRef = useRef(false)
  useEffect(() => {
    if (autoSentRef.current) return
    if (!autoSendPrompt) return
    if (messages.length > 0) return
    autoSentRef.current = true
    void sendMessage(autoSendPrompt)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoSendPrompt])

  // Build message rows with grouped headers (consecutive same-sender
  // turns hide the avatar/header — same heuristic as channels).
  const rows: { msg: ChatMessage; showHeader: boolean }[] = []
  for (let i = 0; i < messages.length; i++) {
    const m = messages[i]
    const prev = messages[i - 1]
    const showHeader = !prev || prev.role !== m.role
    rows.push({ msg: m, showHeader })
  }

  return (
    <>
      {/* Messages — centered reading column */}
      <div className="relative flex-1">
        <div ref={scrollRef} onScroll={handleScroll}
          className="absolute inset-0 overflow-y-auto bg-white">
          <div className="max-w-3xl mx-auto px-4 sm:px-6 pb-4 min-h-full flex flex-col justify-center">
            {rows.map(({ msg, showHeader }) => (
              <DialogMessageRow key={msg.id} message={msg} showHeader={showHeader} />
            ))}
            {typing && (
              <TypingIndicator name={agent.name} avatar={agent.avatar}
                color={agent.color} thinkingText={thinking} />
            )}
          </div>
        </div>
        {hasNewMessage && !isAtBottom && (
          <button onClick={scrollToBottom}
            className="absolute bottom-3 left-1/2 -translate-x-1/2 flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 text-white text-xs font-medium rounded-full shadow-lg hover:bg-blue-700 transition-colors z-10">
            <ArrowDown className="h-3 w-3" />
            New messages
          </button>
        )}
      </div>

      {/* Composer — pill, in the same reading column */}
      <div className="bg-white shrink-0 pb-3 pt-2">
        <div className="max-w-3xl mx-auto px-4 sm:px-6">
          <DialogComposer
            placeholder={composerPlaceholder}
            onSend={sendMessage}
            disabled={typing}
            draftKey={`dialog-${agent.id}-${threadId}`}
          />
        </div>
      </div>
    </>
  )
}
