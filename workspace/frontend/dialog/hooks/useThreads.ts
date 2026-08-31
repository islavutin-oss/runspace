'use client'

import { useCallback, useEffect, useState } from 'react'
import { getDefaultThreadStore, type Thread, type ThreadStore } from '../components/threadStore'

export interface UseThreadsOptions {
  userId: string
  agentId?: string         // when set, list/create are scoped to this agent
  store?: ThreadStore      // default: LocalStorageThreadStore
  /** id of a thread that should be selected on first load (e.g. from URL). */
  initialActiveId?: string
}

export interface UseThreadsResult {
  threads: Thread[]
  activeThread: Thread | null
  loading: boolean
  selectThread(id: string | null): void
  createThread(title?: string): Promise<Thread>
  renameThread(id: string, title: string): Promise<void>
  deleteThread(id: string): Promise<void>
  /** Mark the active thread as freshly used; auto-titles from the first message. */
  touchActive(opts?: { incrementMessages?: number; titleIfEmpty?: string }): Promise<void>
  /** Force-refresh from the store (useful after external writes). */
  refresh(): Promise<void>
}

/**
 * Manages a list of dialog threads for a (user, agent) pair.
 *
 * The store is pluggable: defaults to browser localStorage, but consumers can
 * pass a `SupabaseThreadStore` (or any ThreadStore) for multi-device sync.
 */
export function useThreads(opts: UseThreadsOptions): UseThreadsResult {
  const store = opts.store ?? getDefaultThreadStore()
  const { userId, agentId, initialActiveId } = opts

  const [threads, setThreads] = useState<Thread[]>([])
  const [activeId, setActiveId] = useState<string | null>(initialActiveId ?? null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    if (!userId) { setThreads([]); setLoading(false); return }
    setLoading(true)
    const list = await store.list(userId, agentId)
    setThreads(list)
    setLoading(false)
  }, [store, userId, agentId])

  // Initial load + reload whenever scope changes.
  useEffect(() => { void refresh() }, [refresh])

  const selectThread = useCallback((id: string | null) => setActiveId(id), [])

  const createThread = useCallback(async (title = '') => {
    if (!agentId) throw new Error('useThreads.createThread requires agentId in options')
    const t = await store.create(userId, agentId, title)
    setThreads(prev => [t, ...prev])
    setActiveId(t.id)
    return t
  }, [store, userId, agentId])

  const renameThread = useCallback(async (id: string, title: string) => {
    const updated = await store.rename(id, title)
    if (!updated) return
    setThreads(prev => prev.map(t => t.id === id ? updated : t))
  }, [store])

  const deleteThread = useCallback(async (id: string) => {
    const ok = await store.remove(id)
    if (!ok) return
    setThreads(prev => prev.filter(t => t.id !== id))
    setActiveId(prev => prev === id ? null : prev)
  }, [store])

  const touchActive = useCallback(async (opts: { incrementMessages?: number; titleIfEmpty?: string } = {}) => {
    if (!activeId) return
    const updated = await store.touch(activeId, opts)
    if (!updated) return
    setThreads(prev => {
      const without = prev.filter(t => t.id !== activeId)
      return [updated, ...without]   // bump to top
    })
  }, [store, activeId])

  const activeThread = activeId ? threads.find(t => t.id === activeId) ?? null : null

  return { threads, activeThread, loading, selectThread, createThread, renameThread, deleteThread, touchActive, refresh }
}
