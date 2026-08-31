'use client'

import { useState } from 'react'
import { Plus, Trash2, Pencil, Check, X } from 'lucide-react'
import type { Thread } from './threadStore'

interface ThreadListSidebarLabels {
  newChat?: string
  loading?: string
  emptyTitle?: string         // shown when no threads exist
  emptyHint?: string          // small text under emptyTitle
  buckets?: { today?: string; yesterday?: string; week?: string; older?: string }
  rename?: string
  delete?: string
  deleteConfirm?: string
  untitled?: string
}

interface ThreadListSidebarProps {
  threads: Thread[]
  activeId: string | null
  loading?: boolean
  onSelect(id: string): void
  onCreate(): void
  onRename(id: string, title: string): void
  onDelete(id: string): void
  /** Optional: show the workspace name + brand color in the sidebar header. */
  workspaceName?: string
  /** Override every visible string (for localisation). */
  labels?: ThreadListSidebarLabels
}

const DEFAULT_LABELS: Required<Omit<ThreadListSidebarLabels, 'buckets'>> & { buckets: Required<NonNullable<ThreadListSidebarLabels['buckets']>> } = {
  newChat: 'New chat',
  loading: 'Loading…',
  emptyTitle: 'No conversations yet.',
  emptyHint: 'Click New chat to start one.',
  buckets: { today: 'Today', yesterday: 'Yesterday', week: 'Previous 7 days', older: 'Older' },
  rename: 'Rename',
  delete: 'Delete',
  deleteConfirm: 'Delete this conversation?',
  untitled: 'Untitled',
}

function bucketize(threads: Thread[], names: Required<NonNullable<ThreadListSidebarLabels['buckets']>>): { label: string; items: Thread[] }[] {
  const startOfToday = new Date(); startOfToday.setHours(0, 0, 0, 0)
  const startOfYesterday = new Date(startOfToday); startOfYesterday.setDate(startOfYesterday.getDate() - 1)
  const startOfWeek = new Date(startOfToday); startOfWeek.setDate(startOfWeek.getDate() - 7)

  const today: Thread[] = []
  const yesterday: Thread[] = []
  const week: Thread[] = []
  const older: Thread[] = []
  for (const t of threads) {
    const ts = t.updatedAt
    if (ts >= startOfToday.getTime()) today.push(t)
    else if (ts >= startOfYesterday.getTime()) yesterday.push(t)
    else if (ts >= startOfWeek.getTime()) week.push(t)
    else older.push(t)
  }
  return [
    { label: names.today, items: today },
    { label: names.yesterday, items: yesterday },
    { label: names.week, items: week },
    { label: names.older, items: older },
  ].filter(b => b.items.length > 0)
}

export default function ThreadListSidebar({
  threads, activeId, loading = false, onSelect, onCreate, onRename, onDelete, workspaceName, labels,
}: ThreadListSidebarProps) {
  const [editingId, setEditingId] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const L = {
    ...DEFAULT_LABELS,
    ...labels,
    buckets: { ...DEFAULT_LABELS.buckets, ...(labels?.buckets || {}) },
  }
  const buckets = bucketize(threads, L.buckets)

  function startRename(t: Thread) {
    setEditingId(t.id)
    setDraft(t.title || L.untitled)
  }

  function saveRename(id: string) {
    const next = draft.trim() || L.untitled
    onRename(id, next)
    setEditingId(null)
  }

  return (
    <div className="flex flex-col h-full bg-gray-50 border-r border-gray-200 w-[260px] shrink-0">
      <div className="px-3 pt-3 pb-2 shrink-0">
        {workspaceName && (
          <div className="text-[11px] font-semibold uppercase tracking-wider text-gray-400 mb-2 px-1">
            {workspaceName}
          </div>
        )}
        <button onClick={onCreate}
          className="w-full flex items-center gap-2 px-3 py-2 rounded-lg border border-gray-300 bg-white text-sm font-medium text-gray-700 hover:bg-gray-100 hover:border-gray-400 transition-colors">
          <Plus className="h-4 w-4" />
          {L.newChat}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-2 pb-3">
        {loading && threads.length === 0 && (
          <div className="px-3 py-2 text-xs text-gray-400">{L.loading}</div>
        )}
        {!loading && threads.length === 0 && (
          <div className="px-3 py-6 text-center text-xs text-gray-400">
            {L.emptyTitle}
            <br />
            {L.emptyHint}
          </div>
        )}

        {buckets.map(bucket => (
          <div key={bucket.label} className="mt-3">
            <div className="text-[10px] font-semibold uppercase tracking-wider text-gray-400 px-3 mb-1">
              {bucket.label}
            </div>
            <ul className="space-y-0.5">
              {bucket.items.map(t => {
                const active = t.id === activeId
                const editing = editingId === t.id
                return (
                  <li key={t.id}>
                    <div className={`group flex items-center gap-1 rounded-lg px-3 py-2 text-[13px] transition-colors ${active ? 'bg-gray-200 text-gray-900 font-medium' : 'text-gray-700 hover:bg-gray-100'}`}>
                      {editing ? (
                        <input
                          value={draft}
                          onChange={e => setDraft(e.target.value)}
                          onKeyDown={e => {
                            if (e.key === 'Enter') saveRename(t.id)
                            if (e.key === 'Escape') setEditingId(null)
                          }}
                          autoFocus
                          className="flex-1 bg-white border border-gray-300 rounded px-1.5 py-0.5 text-[12px] outline-none focus:border-gray-500"
                        />
                      ) : (
                        <button
                          onClick={() => onSelect(t.id)}
                          className="flex-1 truncate text-left"
                          title={t.title || L.untitled}
                        >
                          {t.title || L.untitled}
                        </button>
                      )}
                      {editing ? (
                        <>
                          <button onClick={() => saveRename(t.id)} className="p-1 text-gray-400 hover:text-green-600">
                            <Check className="h-3.5 w-3.5" />
                          </button>
                          <button onClick={() => setEditingId(null)} className="p-1 text-gray-400 hover:text-gray-700">
                            <X className="h-3.5 w-3.5" />
                          </button>
                        </>
                      ) : (
                        <div className="opacity-0 group-hover:opacity-100 flex items-center transition-opacity">
                          <button onClick={() => startRename(t)} className="p-1 text-gray-400 hover:text-gray-700" title={L.rename}>
                            <Pencil className="h-3 w-3" />
                          </button>
                          <button onClick={() => { if (confirm(L.deleteConfirm)) onDelete(t.id) }}
                            className="p-1 text-gray-400 hover:text-red-600" title={L.delete}>
                            <Trash2 className="h-3 w-3" />
                          </button>
                        </div>
                      )}
                    </div>
                  </li>
                )
              })}
            </ul>
          </div>
        ))}
      </div>
    </div>
  )
}
