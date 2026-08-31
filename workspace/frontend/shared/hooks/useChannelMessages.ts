'use client'

import { useState, useEffect, useCallback, useRef } from 'react'
import { createClient, type RealtimeChannel } from '@supabase/supabase-js'

export interface DbMessage {
  id: string
  tenant_id: string
  channel_id: string
  thread_id: string | null
  sender_type: 'user' | 'agent'
  sender_id: string
  sender_name: string
  sender_avatar: string
  sender_color: string
  content: string
  content_type: string
  edited: boolean
  deleted: boolean
  tools_used: string[]
  attachments: any[]
  reactions: any[]
  mentions: string[]
  metadata: any
  created_at: string
  updated_at: string
}

interface UseChannelMessagesOptions {
  apiBase?: string
  supabaseUrl?: string
  supabaseAnonKey?: string
}

export function useChannelMessages(channelSlug: string, opts: UseChannelMessagesOptions = {}) {
  const apiBase = opts.apiBase || '/api/workspace'
  const [messages, setMessages] = useState<DbMessage[]>([])
  const [loading, setLoading] = useState(true)
  const [sending, setSending] = useState(false)
  const channelRef = useRef<RealtimeChannel | null>(null)
  const channelIdRef = useRef<string>('')

  // Load initial messages
  const loadMessages = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/channels/${channelSlug}/messages?limit=100`)
      if (res.ok) {
        const data = await res.json()
        setMessages(data.messages || [])
        channelIdRef.current = data.channel_id || ''
      }
    } catch {}
    setLoading(false)
  }, [channelSlug, apiBase])

  // Subscribe to Supabase Realtime for live updates
  useEffect(() => {
    loadMessages()

    const url = opts.supabaseUrl || process.env.NEXT_PUBLIC_SUPABASE_URL || ''
    const key = opts.supabaseAnonKey || process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || ''
    if (!url || !key) return

    const supabase = createClient(url, key)

    // Small delay to ensure channel_id is loaded
    const timer = setTimeout(() => {
      if (!channelIdRef.current) return

      const sub = supabase
        .channel(`ws-messages-${channelSlug}`)
        .on(
          'postgres_changes',
          {
            event: 'INSERT',
            schema: 'public',
            table: 'workspace_messages',
            filter: `channel_id=eq.${channelIdRef.current}`,
          },
          (payload) => {
            const msg = payload.new as DbMessage
            setMessages(prev => {
              // Avoid duplicates
              if (prev.some(m => m.id === msg.id)) return prev
              return [...prev, msg]
            })
          }
        )
        .on(
          'postgres_changes',
          {
            event: 'UPDATE',
            schema: 'public',
            table: 'workspace_messages',
            filter: `channel_id=eq.${channelIdRef.current}`,
          },
          (payload) => {
            const updated = payload.new as DbMessage
            setMessages(prev => prev.map(m => m.id === updated.id ? updated : m))
          }
        )
        .subscribe()

      channelRef.current = sub
    }, 1000)

    return () => {
      clearTimeout(timer)
      channelRef.current?.unsubscribe()
    }
  }, [channelSlug])

  const sendMessage = useCallback(async (content: string, threadId?: string) => {
    setSending(true)
    try {
      const res = await fetch(`${apiBase}/channels/${channelSlug}/messages`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ content, thread_id: threadId }),
      })
      if (res.ok) {
        const data = await res.json()
        // Add message locally if Realtime hasn't delivered it yet
        if (data.message) {
          setMessages(prev => {
            if (prev.some(m => m.id === data.message.id)) return prev
            return [...prev, data.message]
          })
        }
        // Agent response comes via Realtime or is in data.agent_response
        if (data.agent_response) {
          setMessages(prev => {
            if (prev.some(m => m.id === data.agent_response.id)) return prev
            return [...prev, data.agent_response]
          })
        }
      }
    } catch {}
    setSending(false)
  }, [channelSlug, apiBase])

  const editMessage = useCallback(async (messageId: string, content: string) => {
    try {
      await fetch(`${apiBase}/messages/${messageId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ content }),
      })
    } catch {}
  }, [apiBase])

  const deleteMessage = useCallback(async (messageId: string) => {
    try {
      await fetch(`${apiBase}/messages/${messageId}`, {
        method: 'DELETE',
        credentials: 'include',
      })
    } catch {}
  }, [apiBase])

  const addReaction = useCallback(async (messageId: string, emoji: string, userId: string) => {
    try {
      await fetch(`${apiBase}/messages/${messageId}/reactions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ emoji, user_id: userId }),
      })
    } catch {}
  }, [apiBase])

  return {
    messages,
    loading,
    sending,
    sendMessage,
    editMessage,
    deleteMessage,
    addReaction,
    reload: loadMessages,
  }
}
