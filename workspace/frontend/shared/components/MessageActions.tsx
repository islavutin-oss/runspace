'use client'

import { useState, useRef, useEffect } from 'react'
import { MessageSquare, Smile, Bookmark, MoreHorizontal, Pencil, Trash2, Copy, Check } from 'lucide-react'

const QUICK_EMOJIS = ['👍', '❤️', '😂', '🎉', '🔥', '👀', '✅', '💯']

interface MessageActionsProps {
  onReply?: () => void
  onReact?: (emoji: string) => void
  onBookmark?: () => void
  onEdit?: () => void
  onDelete?: () => void
  onCopy?: () => void
  isOwnMessage?: boolean
}

export default function MessageActions({ onReply, onReact, onBookmark, onEdit, onDelete, onCopy, isOwnMessage }: MessageActionsProps) {
  const [showEmoji, setShowEmoji] = useState(false)
  const [showMore, setShowMore] = useState(false)
  const [copied, setCopied] = useState(false)
  const emojiRef = useRef<HTMLDivElement>(null)
  const moreRef = useRef<HTMLDivElement>(null)

  // Close popups on outside click
  useEffect(() => {
    if (!showEmoji && !showMore) return
    function handleClick(e: MouseEvent) {
      if (showEmoji && emojiRef.current && !emojiRef.current.contains(e.target as Node)) setShowEmoji(false)
      if (showMore && moreRef.current && !moreRef.current.contains(e.target as Node)) setShowMore(false)
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [showEmoji, showMore])

  function handleCopy() {
    onCopy?.()
    setCopied(true)
    setShowMore(false)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <div className="absolute -top-3 right-2 opacity-0 group-hover:opacity-100 transition-opacity z-10">
      <div className="bg-white border border-gray-200 rounded-lg shadow-sm flex items-center">
        <button onClick={onReply} className="p-1.5 hover:bg-gray-100 rounded-l-lg text-gray-400 hover:text-gray-600" title="Reply in thread">
          <MessageSquare className="h-3.5 w-3.5" />
        </button>

        {/* Emoji button + picker */}
        <div className="relative" ref={emojiRef}>
          <button onClick={() => { setShowEmoji(!showEmoji); setShowMore(false) }}
            className="p-1.5 hover:bg-gray-100 text-gray-400 hover:text-gray-600" title="Add reaction">
            <Smile className="h-3.5 w-3.5" />
          </button>
          {showEmoji && (
            <div className="absolute top-full right-0 mt-1 bg-white border border-gray-200 rounded-lg shadow-lg p-1.5 flex gap-0.5 z-20">
              {QUICK_EMOJIS.map(emoji => (
                <button key={emoji} onClick={() => { onReact?.(emoji); setShowEmoji(false) }}
                  className="w-8 h-8 flex items-center justify-center text-base hover:bg-gray-100 rounded transition-colors">
                  {emoji}
                </button>
              ))}
            </div>
          )}
        </div>

        <button onClick={onBookmark} className="p-1.5 hover:bg-gray-100 text-gray-400 hover:text-gray-600" title="Bookmark">
          <Bookmark className="h-3.5 w-3.5" />
        </button>

        {/* More menu */}
        <div className="relative" ref={moreRef}>
          <button onClick={() => { setShowMore(!showMore); setShowEmoji(false) }}
            className="p-1.5 hover:bg-gray-100 rounded-r-lg text-gray-400 hover:text-gray-600" title="More actions">
            <MoreHorizontal className="h-3.5 w-3.5" />
          </button>
          {showMore && (
            <div className="absolute top-full right-0 mt-1 w-40 bg-white border border-gray-200 rounded-lg shadow-lg overflow-hidden z-20">
              <button onClick={handleCopy}
                className="w-full flex items-center gap-2 px-3 py-2 text-xs text-gray-700 hover:bg-gray-50 text-left">
                {copied ? <Check className="h-3.5 w-3.5 text-green-500" /> : <Copy className="h-3.5 w-3.5" />}
                {copied ? 'Copied!' : 'Copy text'}
              </button>
              {isOwnMessage && onEdit && (
                <button onClick={() => { onEdit(); setShowMore(false) }}
                  className="w-full flex items-center gap-2 px-3 py-2 text-xs text-gray-700 hover:bg-gray-50 text-left">
                  <Pencil className="h-3.5 w-3.5" />
                  Edit message
                </button>
              )}
              {isOwnMessage && onDelete && (
                <button onClick={() => { onDelete(); setShowMore(false) }}
                  className="w-full flex items-center gap-2 px-3 py-2 text-xs text-red-600 hover:bg-red-50 text-left">
                  <Trash2 className="h-3.5 w-3.5" />
                  Delete message
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
