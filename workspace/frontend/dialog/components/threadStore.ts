'use client'

/**
 * Thread storage abstraction for the ChatGPT-style DialogChat.
 *
 * A "thread" is a top-level conversation (≠ Slack reply thread). Each thread's
 * id doubles as the chat session_id used by /api/workspace/chat/stream — so
 * message history naturally partitions per thread without backend coordination.
 *
 * Two implementations ship out of the box:
 *   - LocalStorageThreadStore  — default, no backend, single device, single user.
 *   - SupabaseThreadStore      — opt-in, multi-device, requires SUPABASE_URL +
 *     SUPABASE_ANON_KEY in the consumer app and the chat_threads table from
 *     workspace/migrations/001_chat_threads.sql.
 *
 * Consumers pick one and pass it to <DialogChat store={...} /> (or to
 * useThreads(...) directly). The interface is intentionally tiny.
 */

export interface Thread {
  id: string
  userId: string
  agentId: string
  title: string
  createdAt: number   // epoch ms
  updatedAt: number   // epoch ms
  messageCount: number
}

export interface ThreadStore {
  list(userId: string, agentId?: string): Promise<Thread[]>
  create(userId: string, agentId: string, title?: string): Promise<Thread>
  rename(id: string, title: string): Promise<Thread | null>
  remove(id: string): Promise<boolean>
  /** Bump updatedAt + message count, optionally set the title if it's still empty. */
  touch(id: string, opts?: { incrementMessages?: number; titleIfEmpty?: string }): Promise<Thread | null>
}

// ---------------------------------------------------------------------------
// LocalStorageThreadStore — default
// ---------------------------------------------------------------------------

const DEFAULT_LS_KEY = 'ws:dialog:threads'

function newId(): string {
  // Browser-safe uuid-ish; not cryptographically strong, fine for an id.
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID()
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

export class LocalStorageThreadStore implements ThreadStore {
  constructor(private readonly storageKey: string = DEFAULT_LS_KEY) {}

  private readAll(): Record<string, Thread> {
    if (typeof window === 'undefined') return {}
    try {
      const raw = window.localStorage.getItem(this.storageKey)
      if (!raw) return {}
      const parsed = JSON.parse(raw)
      return typeof parsed === 'object' && parsed ? parsed as Record<string, Thread> : {}
    } catch {
      return {}
    }
  }

  private writeAll(data: Record<string, Thread>) {
    if (typeof window === 'undefined') return
    window.localStorage.setItem(this.storageKey, JSON.stringify(data))
  }

  async list(userId: string, agentId?: string): Promise<Thread[]> {
    const all = Object.values(this.readAll())
    return all
      .filter(t => t.userId === userId && (!agentId || t.agentId === agentId))
      .sort((a, b) => b.updatedAt - a.updatedAt)
  }

  async create(userId: string, agentId: string, title = ''): Promise<Thread> {
    const now = Date.now()
    const t: Thread = { id: newId(), userId, agentId, title, createdAt: now, updatedAt: now, messageCount: 0 }
    const data = this.readAll()
    data[t.id] = t
    this.writeAll(data)
    return t
  }

  async rename(id: string, title: string): Promise<Thread | null> {
    const data = this.readAll()
    const t = data[id]
    if (!t) return null
    t.title = title
    data[id] = t
    this.writeAll(data)
    return t
  }

  async remove(id: string): Promise<boolean> {
    const data = this.readAll()
    if (!data[id]) return false
    delete data[id]
    this.writeAll(data)
    return true
  }

  async touch(id: string, opts: { incrementMessages?: number; titleIfEmpty?: string } = {}): Promise<Thread | null> {
    const data = this.readAll()
    const t = data[id]
    if (!t) return null
    t.updatedAt = Date.now()
    if (opts.incrementMessages) t.messageCount += opts.incrementMessages
    if (opts.titleIfEmpty && !(t.title || '').trim()) t.title = opts.titleIfEmpty.slice(0, 80)
    data[id] = t
    this.writeAll(data)
    return t
  }
}

// ---------------------------------------------------------------------------
// SupabaseThreadStore — opt-in, browser-side
// ---------------------------------------------------------------------------

/**
 * Browser-side Supabase store. Consumers must install @supabase/supabase-js
 * and pass a configured client. Schema:
 *   chat_threads(id text pk, tenant_id text, user_id text not null,
 *                agent_id text not null, title text, created_at timestamptz,
 *                last_message_at timestamptz, message_count int)
 * (See workspace/migrations/001_chat_threads.sql.)
 *
 * RLS should restrict rows to the authenticated user.
 */
export interface SupabaseLikeClient {
  from(table: string): {
    select(cols?: string): any
    insert(row: any): any
    update(row: any): any
    delete(): any
    eq(col: string, val: any): any
    order(col: string, opts?: { ascending?: boolean }): any
  }
}

interface SupabaseThreadStoreOptions {
  client: SupabaseLikeClient
  tenantId?: string
  table?: string
}

export class SupabaseThreadStore implements ThreadStore {
  private readonly table: string
  constructor(private readonly opts: SupabaseThreadStoreOptions) {
    this.table = opts.table || 'chat_threads'
  }

  private rowToThread(row: any): Thread {
    return {
      id: row.id,
      userId: row.user_id,
      agentId: row.agent_id,
      title: row.title || '',
      createdAt: row.created_at ? new Date(row.created_at).getTime() : Date.now(),
      updatedAt: row.last_message_at ? new Date(row.last_message_at).getTime() : Date.now(),
      messageCount: row.message_count ?? 0,
    }
  }

  async list(userId: string, agentId?: string): Promise<Thread[]> {
    let q: any = this.opts.client.from(this.table).select('*').eq('user_id', userId)
    if (this.opts.tenantId) q = q.eq('tenant_id', this.opts.tenantId)
    if (agentId) q = q.eq('agent_id', agentId)
    const res = await q.order('last_message_at', { ascending: false })
    const rows: any[] = res?.data || []
    return rows.map(r => this.rowToThread(r))
  }

  async create(userId: string, agentId: string, title = ''): Promise<Thread> {
    const id = newId()
    const now = new Date().toISOString()
    const row = {
      id, user_id: userId, agent_id: agentId,
      tenant_id: this.opts.tenantId, title,
      created_at: now, last_message_at: now, message_count: 0,
    }
    await this.opts.client.from(this.table).insert(row)
    return this.rowToThread(row)
  }

  async rename(id: string, title: string): Promise<Thread | null> {
    const res: any = await this.opts.client.from(this.table).update({ title }).eq('id', id)
    const rows: any[] = res?.data || []
    return rows[0] ? this.rowToThread(rows[0]) : null
  }

  async remove(id: string): Promise<boolean> {
    const res: any = await this.opts.client.from(this.table).delete().eq('id', id)
    return Array.isArray(res?.data) && res.data.length > 0
  }

  async touch(id: string, opts: { incrementMessages?: number; titleIfEmpty?: string } = {}): Promise<Thread | null> {
    // Fetch first so we can compute message_count and decide on title.
    const sel: any = await this.opts.client.from(this.table).select('*').eq('id', id)
    const current = (sel?.data || [])[0]
    if (!current) return null
    const update: Record<string, any> = { last_message_at: new Date().toISOString() }
    if (opts.incrementMessages) update.message_count = (current.message_count ?? 0) + opts.incrementMessages
    if (opts.titleIfEmpty && !(current.title || '').trim()) update.title = opts.titleIfEmpty.slice(0, 80)
    const res: any = await this.opts.client.from(this.table).update(update).eq('id', id)
    const rows: any[] = res?.data || []
    return rows[0] ? this.rowToThread(rows[0]) : this.rowToThread({ ...current, ...update })
  }
}

// ---------------------------------------------------------------------------
// Default singleton (lazy)
// ---------------------------------------------------------------------------

let _defaultStore: ThreadStore | null = null

/** Convenience accessor for consumers that just want "browser localStorage, please." */
export function getDefaultThreadStore(): ThreadStore {
  if (!_defaultStore) _defaultStore = new LocalStorageThreadStore()
  return _defaultStore
}
