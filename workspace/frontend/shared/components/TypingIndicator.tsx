'use client'

import { Wrench } from 'lucide-react'

interface TypingIndicatorProps {
  name: string
  avatar?: string
  color?: string
  thinkingText?: string  // e.g. "Accessing revenue summary…"
}

export default function TypingIndicator({ name, avatar, color, thinkingText }: TypingIndicatorProps) {
  return (
    <div className="flex gap-2 -mx-2 px-2 pt-2">
      <div className="w-9 shrink-0">
        {avatar && (
          <div className="w-9 h-9 rounded-lg flex items-center justify-center text-sm"
            style={{ backgroundColor: (color || '#6B7280') + '12' }}>
            {avatar}
          </div>
        )}
      </div>
      <div>
        <span className="text-[13px] font-bold text-gray-900">{name}</span>
        <div className="flex items-center gap-1.5 mt-0.5">
          {thinkingText ? (
            <>
              <Wrench className="h-3 w-3 text-gray-400 animate-spin" />
              <span className="text-xs text-gray-500">{thinkingText}</span>
            </>
          ) : (
            <>
              <span className="w-1.5 h-1.5 rounded-full animate-bounce" style={{ backgroundColor: color || '#9CA3AF' }} />
              <span className="w-1.5 h-1.5 rounded-full animate-bounce" style={{ backgroundColor: color || '#9CA3AF', animationDelay: '150ms', opacity: 0.6 }} />
              <span className="w-1.5 h-1.5 rounded-full animate-bounce" style={{ backgroundColor: color || '#9CA3AF', animationDelay: '300ms', opacity: 0.3 }} />
            </>
          )}
        </div>
      </div>
    </div>
  )
}
