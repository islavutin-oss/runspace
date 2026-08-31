'use client'

/**
 * Message row used inside DialogChat (ChatGPT/Claude-style 1-on-1).
 *
 * - User message → right-aligned bubble (`bg-gray-100 rounded-2xl`,
 *   max-w-75%, no avatar)
 * - Assistant message → full-width row with avatar on first turn only
 *
 * **Not** shared with Slack-style channels. The channel feed uses
 * `MessageBubble` with its own (Slack-row) rendering. Keeping these
 * surfaces in separate files prevents one mode's edits from leaking
 * into the other — the bug class we hit on 2026-04-29.
 */

import MarkdownContent from '../../shared/components/MarkdownContent'
import ToolBadge from '../../shared/components/ToolBadge'
import type { ChatMessage } from '../../shared/components/MessageBubble'

interface DialogMessageRowProps {
  message: ChatMessage
  showHeader: boolean
  mentionableKinds?: Record<string, 'agent' | 'user'>
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

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export default function DialogMessageRow({ message: msg, showHeader, mentionableKinds }: DialogMessageRowProps) {
  if (msg.deleted) {
    return (
      <div className="py-1 text-xs text-gray-400 italic text-center">
        This message was deleted
      </div>
    )
  }

  const isBot = msg.role === 'bot'

  // ── User: right-aligned bubble ─────────────────────────────────────────
  if (!isBot) {
    return (
      <div className={`flex justify-end ${showHeader ? 'pt-3' : 'pt-1'}`}>
        <div className="max-w-[75%] min-w-0">
          <div className="bg-gray-100 text-gray-900 rounded-2xl px-4 py-2.5">
            <div className="text-[15px] text-gray-900 leading-[1.6] whitespace-pre-wrap">
              <MarkdownContent text={msg.text} mentionableKinds={mentionableKinds} />
              {msg.edited && <span className="text-[10px] text-gray-400 ml-1">(edited)</span>}
            </div>
            {msg.images && msg.images.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mt-2">
                {msg.images.filter(isSafeImageSrc).map((src, i) => (
                  <a key={i} href={src} target="_blank" rel="noopener noreferrer"
                    className="block rounded-lg overflow-hidden border border-gray-200">
                    <img src={src} alt={`Attachment ${i + 1}`}
                      className={`object-cover ${msg.images!.length === 1 ? 'max-w-sm max-h-72' : 'w-32 h-32'}`}
                      loading="lazy" />
                  </a>
                ))}
              </div>
            )}
          </div>
          {showHeader && (
            <div className="text-[11px] text-gray-400 text-right mt-1 px-2">{msg.time}</div>
          )}
        </div>
      </div>
    )
  }

  // ── Assistant: full-width with avatar on first row ─────────────────────
  return (
    <div className={`flex gap-3 ${showHeader ? 'pt-4' : 'pt-1'}`}>
      <div className="w-9 shrink-0">
        {showHeader && (
          <div className="w-9 h-9 rounded-lg flex items-center justify-center text-sm"
            style={{ backgroundColor: (msg.botColor || '#6B7280') + '12' }}>
            {msg.botAvatar || '🤖'}
          </div>
        )}
      </div>

      <div className="flex-1 min-w-0">
        {showHeader && (
          <div className="flex items-baseline gap-2 mb-1">
            <span className="text-[14px] font-semibold"
              style={msg.botColor ? { color: msg.botColor } : { color: '#111' }}>
              {msg.botName}
            </span>
            <span className="text-[11px] text-gray-400">{msg.time}</span>
          </div>
        )}

        {msg.toolsUsed && msg.toolsUsed.length > 0 && <ToolBadge tools={msg.toolsUsed} />}

        <div className="text-[15px] text-gray-800 leading-[1.6]">
          <MarkdownContent text={msg.text} mentionableKinds={mentionableKinds} />
          {msg.edited && <span className="text-[10px] text-gray-400 ml-1">(edited)</span>}
        </div>

        {msg.images && msg.images.length > 0 && (
          <div className={`flex flex-wrap gap-1.5 mt-2 ${msg.images.length === 1 ? '' : 'max-w-md'}`}>
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

        {msg.attachments && msg.attachments.length > 0 && (
          <div className="flex flex-col gap-1 mt-2">
            {msg.attachments.map((file, i) => (
              <a key={i} href={file.url} download={file.name}
                className="flex items-center gap-2 px-3 py-2 rounded-lg border border-gray-200 hover:bg-gray-50 transition-colors max-w-xs">
                <span className="text-lg shrink-0">
                  {file.type.includes('pdf') ? '📄' : file.type.includes('csv') ? '📊' : '📎'}
                </span>
                <div className="min-w-0">
                  <p className="text-xs font-medium text-gray-800 truncate">{file.name}</p>
                  <p className="text-[10px] text-gray-400">{formatFileSize(file.size)}</p>
                </div>
              </a>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
