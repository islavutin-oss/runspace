'use client'

import { useState, useRef, useEffect, KeyboardEvent } from 'react'
import MarkdownContent from './MarkdownContent'
import ToolBadge from './ToolBadge'
import MessageActions from './MessageActions'

export interface FileAttachment {
  name: string
  size: number     // bytes
  type: string     // MIME type
  url: string      // data URL or https URL
}

export interface ChatMessage {
  id: string
  role: 'user' | 'bot'
  botId?: string
  botName?: string
  botAvatar?: string
  botColor?: string
  text: string
  time: string
  timestamp?: number           // Date.now() — used for date dividers
  toolsUsed?: string[]
  reactions?: { emoji: string; count: number; mine?: boolean }[]
  threadId?: string
  replyCount?: number
  threadAvatars?: string[]     // unique participant avatars in thread
  edited?: boolean
  deleted?: boolean
  images?: string[]
  attachments?: FileAttachment[]
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function isSafeImageSrc(src: string): boolean {
  if (src.startsWith('data:image/')) return true
  try {
    const url = new URL(src)
    return url.protocol === 'https:'
  } catch {
    return false
  }
}

interface MessageBubbleProps {
  message: ChatMessage
  showHeader: boolean
  userName?: string
  userAvatar?: string
  // Slug → kind map for Slack-style @-mention coloring.
  // Map of handle → 'agent' | 'user' so the renderer can chip-style mentions.
  mentionableKinds?: Record<string, 'agent' | 'user'>
  onOpenThread?: (messageId: string) => void
  onReact?: (messageId: string, emoji: string) => void
  onEdit?: (messageId: string, newText: string) => void
  onDelete?: (messageId: string) => void
}

export default function MessageBubble({ message: msg, showHeader, userName = 'You', userAvatar, mentionableKinds, onOpenThread, onReact, onEdit, onDelete }: MessageBubbleProps) {
  const userInitials = userAvatar || userName.split(' ').map(w => w[0]).join('').slice(0, 2) || 'U'
  const isBot = msg.role === 'bot'
  const isOwnMessage = msg.role === 'user'
  const [editing, setEditing] = useState(false)
  const [editText, setEditText] = useState(msg.text)
  const editRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (editing && editRef.current) {
      editRef.current.focus()
      editRef.current.setSelectionRange(editText.length, editText.length)
      editRef.current.style.height = 'auto'
      editRef.current.style.height = editRef.current.scrollHeight + 'px'
    }
  }, [editing])

  function handleEditKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); saveEdit() }
    if (e.key === 'Escape') { setEditing(false); setEditText(msg.text) }
  }

  function saveEdit() {
    const trimmed = editText.trim()
    if (trimmed && trimmed !== msg.text) onEdit?.(msg.id, trimmed)
    setEditing(false)
  }

  function handleCopy() { navigator.clipboard?.writeText(msg.text) }

  if (msg.deleted) {
    return (
      <div className="flex gap-2 -mx-2 px-2 py-1">
        <div className="w-9 shrink-0" />
        <span className="text-xs text-gray-400 italic">This message was deleted</span>
      </div>
    )
  }

  return (
    <div
      // A new author's turn opens with a hairline rule and real breathing room.
      // Agent replies here run long — tables, KPI strips, charts — and at the
      className={`group relative flex gap-2 hover:bg-gray-50/70 -mx-2 px-2 rounded pb-px ${
        showHeader ? 'mt-4 pt-4 border-t border-gray-200/70' : 'pt-px'
      }`}
    >
      {/* Avatar column */}
      <div className="w-9 shrink-0">
        {showHeader && (
          isBot ? (
            <div className="w-9 h-9 rounded-lg flex items-center justify-center text-sm"
              style={{ backgroundColor: (msg.botColor || '#6B7280') + '12' }}>
              {msg.botAvatar || '🤖'}
            </div>
          ) : (
            <div className="w-9 h-9 rounded-lg bg-gray-800 flex items-center justify-center text-xs text-white font-bold">
              {userInitials}
            </div>
          )
        )}
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        {showHeader && (
          <div className="flex items-baseline gap-2 mb-0.5">
            <span className="text-[13px] font-bold" style={isBot && msg.botColor ? { color: msg.botColor } : { color: '#111' }}>
              {isBot ? msg.botName : userName}
            </span>
            <span className="text-[11px] text-gray-400">{msg.time}</span>
          </div>
        )}

        {msg.toolsUsed && msg.toolsUsed.length > 0 && <ToolBadge tools={msg.toolsUsed} />}

        {editing ? (
          <div className="border border-amber-400 rounded-lg bg-amber-50/50 shadow-sm -mx-1">
            {/* Formatting toolbar */}
            <div className="flex items-center gap-0.5 px-2 pt-1.5 pb-1 border-b border-amber-200/50">
              <button onClick={() => { const el = editRef.current; if (el) { const s = el.selectionStart; const e = el.selectionEnd; const sel = editText.slice(s, e); setEditText(editText.slice(0, s) + '**' + sel + '**' + editText.slice(e)) } }}
                className="p-1 rounded hover:bg-amber-100 text-gray-400 hover:text-gray-600 text-xs font-bold" title="Bold">B</button>
              <button onClick={() => { const el = editRef.current; if (el) { const s = el.selectionStart; const e = el.selectionEnd; const sel = editText.slice(s, e); setEditText(editText.slice(0, s) + '*' + sel + '*' + editText.slice(e)) } }}
                className="p-1 rounded hover:bg-amber-100 text-gray-400 hover:text-gray-600 text-xs italic" title="Italic">I</button>
              <div className="flex-1" />
              <span className="text-[10px] text-amber-600/60 mr-1">editing</span>
            </div>
            <textarea ref={editRef} value={editText}
              onChange={e => { setEditText(e.target.value); e.target.style.height = 'auto'; e.target.style.height = Math.max(44, e.target.scrollHeight) + 'px' }}
              onKeyDown={handleEditKeyDown}
              style={{ height: 'auto' }}
              className="w-full text-[13px] text-gray-800 bg-transparent outline-none resize-none px-3 py-2 leading-[1.46]" />
            <div className="flex items-center gap-2 px-3 py-1.5 border-t border-amber-200/50">
              <span className="text-[11px] text-gray-400">esc to cancel · enter to save · shift+enter for new line</span>
              <div className="flex-1" />
              <button onClick={() => { setEditing(false); setEditText(msg.text) }}
                className="text-[12px] px-3 py-1 text-gray-600 hover:text-gray-800 border border-gray-300 rounded hover:bg-gray-100 font-medium">Cancel</button>
              <button onClick={saveEdit}
                className="text-[12px] px-3 py-1 bg-green-700 text-white rounded hover:bg-green-800 font-medium">Save Changes</button>
            </div>
          </div>
        ) : (
          <div className="text-[13px] text-gray-800 leading-[1.46]">
            <MarkdownContent text={msg.text} mentionableKinds={mentionableKinds} />
            {msg.edited && <span className="text-[10px] text-gray-400 ml-1">(edited)</span>}
          </div>
        )}

        {/* Images */}
        {msg.images && msg.images.length > 0 && (
          <div className={`flex flex-wrap gap-1.5 mt-1 ${msg.images.length === 1 ? '' : 'max-w-md'}`}>
            {msg.images.filter(isSafeImageSrc).map((src, i) => (
              <a key={i} href={src} target="_blank" rel="noopener noreferrer"
                className="block rounded-lg overflow-hidden border border-gray-200 hover:border-gray-300 transition-colors">
                <img src={src} alt={`Attachment ${i + 1}`}
                  className={`object-cover ${msg.images!.length === 1 ? 'max-w-sm max-h-72' : 'w-32 h-32'}`}
                  loading="lazy" />
              </a>
            ))}
          </div>
        )}

        {/* File attachments */}
        {msg.attachments && msg.attachments.length > 0 && (
          <div className="flex flex-col gap-1 mt-1">
            {msg.attachments.map((file, i) => (
              <a key={i} href={file.url} download={file.name}
                className="flex items-center gap-2 px-3 py-2 rounded-lg border border-gray-200 hover:bg-gray-50 transition-colors max-w-xs">
                <span className="text-lg shrink-0">
                  {file.type.includes('pdf') ? '📄' : file.type.includes('spreadsheet') || file.type.includes('csv') ? '📊' : file.type.includes('word') || file.type.includes('document') ? '📝' : file.type.includes('presentation') || file.name.endsWith('.pptx') ? '📊' : '📎'}
                </span>
                <div className="min-w-0">
                  <p className="text-xs font-medium text-gray-800 truncate">{file.name}</p>
                  <p className="text-[10px] text-gray-400">{formatFileSize(file.size)}</p>
                </div>
              </a>
            ))}
          </div>
        )}

        {/* Reactions — with toggle + own-reaction highlight */}
        {msg.reactions && msg.reactions.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-1">
            {msg.reactions.map((r, i) => (
              <button key={i} onClick={() => onReact?.(msg.id, r.emoji)}
                className={`flex items-center gap-0.5 px-1.5 py-0.5 text-xs rounded-full border transition-colors ${
                  r.mine
                    ? 'bg-blue-50 border-blue-300 hover:bg-blue-100'
                    : 'bg-gray-100 border-gray-200 hover:bg-gray-200'
                }`}>
                <span>{r.emoji}</span>
                <span className={r.mine ? 'text-blue-600 font-medium' : 'text-gray-500'}>{r.count}</span>
              </button>
            ))}
          </div>
        )}

        {/* Thread indicator — with avatar stack */}
        {msg.replyCount && msg.replyCount > 0 && (
          <button onClick={() => onOpenThread?.(msg.id)}
            className="flex items-center gap-1.5 mt-1.5 text-xs text-blue-600 hover:text-blue-700 hover:bg-blue-50 rounded px-1.5 py-1 -ml-1.5 transition-colors">
            {/* Avatar stack */}
            {msg.threadAvatars && msg.threadAvatars.length > 0 && (
              <span className="flex -space-x-1">
                {msg.threadAvatars.slice(0, 3).map((av, i) => (
                  <span key={i} className="w-5 h-5 rounded-md bg-gray-100 flex items-center justify-center text-[10px] border border-white">
                    {av}
                  </span>
                ))}
              </span>
            )}
            <span className="font-medium">{msg.replyCount} {msg.replyCount === 1 ? 'reply' : 'replies'}</span>
          </button>
        )}
      </div>

      {/* Hover timestamp for non-header messages */}
      {!showHeader && (
        <span className="text-[10px] text-gray-400 opacity-0 group-hover:opacity-100 transition-opacity pt-0.5 shrink-0">
          {msg.time}
        </span>
      )}

      {/* Hover actions */}
      {!editing && (
        <MessageActions
          onReply={() => onOpenThread?.(msg.id)}
          onReact={(emoji) => onReact?.(msg.id, emoji)}
          onEdit={isOwnMessage ? () => { setEditText(msg.text); setEditing(true) } : undefined}
          onDelete={isOwnMessage ? () => onDelete?.(msg.id) : undefined}
          onCopy={handleCopy}
          isOwnMessage={isOwnMessage}
        />
      )}
    </div>
  )
}
