'use client'

// KnowledgeBrowser — generic group + cards + search + drawer.
//

import { useState, useEffect, type ReactNode } from 'react'

export interface KBGroup {
  id: string
  label: string
  count: number
}

export interface KBItem {
  /** Stable key — must be unique across all groups. */
  key: string
  /** Group this item belongs to (matches a KBGroup.id). */
  group: string
  /** Primary heading shown on the card and in the drawer. */
  title: string
  /** Optional badges shown next to the title (e.g. "en", "ada-tools"). */
  badges?: { label: string; tone?: 'gray' | 'indigo' | 'emerald' | 'amber' | 'rose' }[]
  /** Short preview text — line-clamped on the card. */
  preview?: string
  /** Right-side meta on the card (e.g. byte count, fact age). */
  meta?: string
  /** Long-form body shown in the drawer. */
  body?: string | ReactNode
  /** Original record — passed back to renderCardBody / renderDrawer. */
  raw?: unknown
}

interface Props {
  groups: KBGroup[]
  items: KBItem[]
  searchPlaceholder?: string
  emptyState?: ReactNode
  /** Override card body. Receives the full item; default renders preview + meta. */
  renderCardBody?: (item: KBItem) => ReactNode
  /** Override drawer contents. Receives the item and a close handler. */
  renderDrawer?: (item: KBItem, close: () => void) => ReactNode
  /** Right-aligned action area in the header (e.g. "+ New fact" button). */
  headerAction?: ReactNode
  title?: ReactNode
  subtitle?: ReactNode
  /** Accent color used for hover/border. Defaults to indigo. */
  accentColor?: string
}

const TONE_CLASSES: Record<NonNullable<NonNullable<KBItem['badges']>[number]['tone']>, string> = {
  gray:    'bg-gray-100 text-gray-600',
  indigo:  'bg-indigo-50 text-indigo-700',
  emerald: 'bg-emerald-50 text-emerald-700',
  amber:   'bg-amber-50 text-amber-700',
  rose:    'bg-rose-50 text-rose-700',
}

export default function KnowledgeBrowser({
  groups,
  items,
  searchPlaceholder = 'Search…',
  emptyState,
  renderCardBody,
  renderDrawer,
  headerAction,
  title,
  subtitle,
  accentColor = '#4f46e5',
}: Props) {
  const [activeGroup, setActiveGroup] = useState<string>(groups[0]?.id || '')
  const [search, setSearch] = useState('')
  const [openItem, setOpenItem] = useState<KBItem | null>(null)

  useEffect(() => {
    if (!activeGroup && groups[0]) setActiveGroup(groups[0].id)
  }, [groups, activeGroup])

  // Esc closes the drawer.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape' && openItem) setOpenItem(null)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [openItem])

  const allId = groups.find(g => g.id === 'all')?.id || groups[0]?.id || ''
  const filtered = items.filter(it => {
    if (activeGroup && activeGroup !== allId && it.group !== activeGroup) return false
    if (search) {
      const q = search.toLowerCase()
      const hay = `${it.title} ${it.preview || ''} ${it.badges?.map(b => b.label).join(' ') || ''}`.toLowerCase()
      if (!hay.includes(q)) return false
    }
    return true
  })

  return (
    <div className="max-w-6xl">
      {(title || subtitle || headerAction) && (
        <div className="flex items-start justify-between mb-5">
          <div>
            {title && <h3 className="text-lg font-semibold text-gray-900">{title}</h3>}
            {subtitle && <p className="text-sm text-gray-500 mt-1">{subtitle}</p>}
          </div>
          {headerAction}
        </div>
      )}

      <div className="flex gap-6">
        <aside className="w-44 shrink-0">
          <ul className="space-y-1">
            {groups.map(g => (
              <li key={g.id}>
                <button onClick={() => setActiveGroup(g.id)}
                        className={`w-full text-left px-3 py-1.5 rounded-md text-sm flex justify-between items-center transition ${
                          activeGroup === g.id ? 'bg-gray-100 text-gray-900 font-medium' : 'text-gray-600 hover:bg-gray-50'
                        }`}>
                  <span className="truncate">{g.label}</span>
                  <span className="text-xs text-gray-400 ml-2">{g.count}</span>
                </button>
              </li>
            ))}
          </ul>
        </aside>

        <div className="flex-1 min-w-0">
          <input type="text"
                 value={search}
                 onChange={e => setSearch(e.target.value)}
                 placeholder={searchPlaceholder}
                 className="w-full mb-4 px-3 py-2 text-sm border border-gray-200 rounded-md focus:outline-none focus:ring-1"
                 style={{ borderColor: search ? accentColor : undefined }} />
          {filtered.length === 0 ? (
            emptyState || (
              <p className="text-sm text-gray-400 py-8 text-center">
                Nothing matches.{search && (
                  <> <button onClick={() => setSearch('')} className="underline" style={{ color: accentColor }}>Clear search</button></>
                )}
              </p>
            )
          ) : (
            <div className="space-y-2">
              {filtered.map(it => (
                <button key={it.key}
                        onClick={() => setOpenItem(it)}
                        className="block w-full text-left bg-white border border-gray-200 rounded-lg p-4 transition hover:shadow-sm"
                        style={{ ['--hover-border' as string]: accentColor }}
                        onMouseEnter={(e) => { e.currentTarget.style.borderColor = accentColor }}
                        onMouseLeave={(e) => { e.currentTarget.style.borderColor = '' }}>
                  {renderCardBody ? renderCardBody(it) : (
                    <>
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2 mb-1 flex-wrap">
                            <span className="text-sm font-semibold text-gray-900">{it.title}</span>
                            {(it.badges || []).map((b, i) => (
                              <span key={i} className={`text-xs px-1.5 py-0.5 rounded ${TONE_CLASSES[b.tone || 'gray']}`}>{b.label}</span>
                            ))}
                          </div>
                          {it.preview && <p className="text-sm text-gray-700 line-clamp-2">{it.preview}</p>}
                        </div>
                        {it.meta && <span className="text-xs text-gray-400 whitespace-nowrap">{it.meta}</span>}
                      </div>
                    </>
                  )}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {openItem && (
        <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4"
             onClick={() => setOpenItem(null)}>
          <div className="bg-white rounded-2xl shadow-xl max-w-3xl w-full max-h-[80vh] flex flex-col"
               onClick={e => e.stopPropagation()}>
            {renderDrawer ? renderDrawer(openItem, () => setOpenItem(null)) : (
              <>
                <div className="px-6 py-4 border-b border-gray-200 flex items-center gap-3">
                  <h3 className="font-semibold text-gray-900">{openItem.title}</h3>
                  {(openItem.badges || []).map((b, i) => (
                    <span key={i} className={`text-xs px-1.5 py-0.5 rounded ${TONE_CLASSES[b.tone || 'gray']}`}>{b.label}</span>
                  ))}
                  <button onClick={() => setOpenItem(null)} className="ml-auto text-gray-400 hover:text-gray-600 text-xl leading-none">×</button>
                </div>
                <div className="p-6 overflow-y-auto">
                  {typeof openItem.body === 'string' ? (
                    <pre className="text-sm whitespace-pre-wrap">{openItem.body || <span className="italic text-gray-400">(empty)</span>}</pre>
                  ) : (openItem.body || <span className="italic text-gray-400 text-sm">(empty)</span>)}
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
