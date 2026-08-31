'use client'

import { useState, useRef, useEffect } from 'react'
import { X } from 'lucide-react'
import MessageBubble, { type ChatMessage } from '../../shared/components/MessageBubble'
import MessageComposer from '../../shared/components/MessageComposer'
import MarkdownContent from '../../shared/components/MarkdownContent'

interface ThreadPanelProps {
  parentMessage: ChatMessage
  replies: ChatMessage[]
  onClose: () => void
  onSendReply: (text: string, alsoSendToChannel?: boolean) => void
  onReact?: (messageId: string, emoji: string) => void
  onEdit?: (messageId: string, newText: string) => void
  onDelete?: (messageId: string) => void
  typing?: boolean
  thinkingText?: string
  botAvatar?: string
  botColor?: string
  botName?: string
  userName?: string
  channelName?: string
  width?: number  // resizable width, default 320
}

export default function ThreadPanel({
  parentMessage, replies, onClose, onSendReply,
  onReact, onEdit, onDelete,
  typing, thinkingText, botAvatar, botColor, botName, userName = 'You',
  channelName, width,
}: ThreadPanelProps) {
  const [alsoSendToChannel, setAlsoSendToChannel] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [replies.length, typing])

  return (
    <div className="border-l border-gray-200 bg-white h-full flex flex-col shrink-0"
      style={{ width: width || 320 }}>
      {/* Header */}
      <div className="h-12 border-b border-gray-200 flex items-center justify-between px-4 shrink-0">
        <div>
          <span className="text-sm font-bold text-gray-900">Thread</span>
          {channelName && <span className="text-[10px] text-gray-400 ml-2">{channelName}</span>}
        </div>
        <button onClick={onClose} className="p-1 rounded hover:bg-gray-100 text-gray-400" aria-label="Close thread">
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Parent message */}
      <div className="px-4 py-3 border-b border-gray-100">
        <div className="flex items-start gap-2">
          <div className="w-8 h-8 rounded-lg flex items-center justify-center text-sm shrink-0"
            style={{ backgroundColor: (parentMessage.botColor || '#6B7280') + '12' }}>
            {parentMessage.role === 'bot' ? parentMessage.botAvatar || '🤖' : userName.split(' ').map(w => w[0]).join('').slice(0, 2) || 'U'}
          </div>
          <div className="min-w-0">
            <div className="flex items-baseline gap-2">
              <span className="text-xs font-bold text-gray-900">
                {parentMessage.role === 'bot' ? parentMessage.botName : userName}
              </span>
              <span className="text-[10px] text-gray-400">{parentMessage.time}</span>
            </div>
            <div className="text-xs text-gray-600 mt-0.5 line-clamp-3">
              <MarkdownContent text={parentMessage.text} className="prose-xs" />
            </div>
          </div>
        </div>
      </div>

      {/* Reply count divider */}
      {replies.length > 0 && (
        <div className="flex items-center gap-2 px-4 py-1.5 border-b border-gray-100">
          <span className="text-[11px] text-blue-600 font-medium">{replies.length} {replies.length === 1 ? 'reply' : 'replies'}</span>
          <div className="flex-1 h-px bg-gray-100" />
        </div>
      )}

      {/* Replies — fully interactive */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-2 py-2 space-y-0.5">
        {replies.length === 0 && (
          <p className="text-center text-xs text-gray-400 py-8">No replies yet</p>
        )}
        {replies.map((msg, i) => {
          const prevDiff = i === 0 || replies[i - 1]?.role !== msg.role ||
            (msg.role === 'bot' && replies[i - 1]?.botId !== msg.botId)
          return (
            <MessageBubble key={msg.id} message={msg} showHeader={prevDiff} userName={userName}
              onReact={onReact} onEdit={onEdit} onDelete={onDelete} />
          )
        })}

        {typing && (
          <div className="flex gap-2 px-2 pt-2">
            <div className="w-9 shrink-0">
              {botAvatar && (
                <div className="w-8 h-8 rounded-lg flex items-center justify-center text-sm"
                  style={{ backgroundColor: (botColor || '#6B7280') + '12' }}>
                  {botAvatar}
                </div>
              )}
            </div>
            <div>
              {botName && <span className="text-[12px] font-bold text-gray-900">{botName}</span>}
              <div className="flex items-center gap-1 mt-0.5">
                <span className="w-1.5 h-1.5 rounded-full bg-gray-400 animate-bounce" />
                <span className="w-1.5 h-1.5 rounded-full bg-gray-400 animate-bounce" style={{ animationDelay: '150ms' }} />
                <span className="w-1.5 h-1.5 rounded-full bg-gray-400 animate-bounce" style={{ animationDelay: '300ms' }} />
                {thinkingText && <span className="text-[10px] text-gray-500 ml-1">{thinkingText}</span>}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Reply input */}
      <div className="px-3 pb-3 shrink-0">
        {channelName && (
          <label className="flex items-center gap-1.5 px-1 pb-1.5 cursor-pointer">
            <input type="checkbox" checked={alsoSendToChannel} onChange={e => setAlsoSendToChannel(e.target.checked)}
              className="w-3 h-3 rounded border-gray-300 text-blue-600 focus:ring-blue-500" />
            <span className="text-[11px] text-gray-500">Also send to {channelName}</span>
          </label>
        )}
        <MessageComposer
          placeholder={`Reply${botName ? ` to ${botName}` : ''}…`}
          onSend={(text) => onSendReply(text, alsoSendToChannel)}
        />
      </div>
    </div>
  )
}
