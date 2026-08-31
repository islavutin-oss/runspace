'use client'

/**
 * Generic kanban board component — drag-and-drop, multi-column.
 *
 * Originally lifted from initech/apps/web/src/app/workspace/leads/page.tsx
 * (Lead Pipeline) and generalised so other workspaces can reuse the same
 * visuals + interaction pattern. Currently consumed by:
 *
 *   - initech  → leads pipeline (new → qualifying → … → closed)
 *   - globex    → supplier-comm cards (new → awaiting → stale → received → closed)
 *
 * Design principles:
 *   - Stages (columns) are data-driven; pass `stages: KanbanStage[]`.
 *   - Card content is fully owned by the host via `renderCard` — the shared
 *     component only handles columns, headers, drop zones, drag wiring.
 *   - Drag-and-drop uses native HTML5 (no external lib). On drop, the host's
 *     `onCardMove(cardId, newStageId)` is called with optimistic-update
 *     responsibility on the host.
 *   - Stage colour is one CSS string per stage; the component derives header,
 *     count badge and divider from it. No per-card styling here.
 *
 * Minimum card shape: `{ id: string; stage: string; ... }`.
 *
 * Example:
 *   <Kanban
 *     stages={STAGES}
 *     cards={cards}
 *     renderCard={(card) => <SupplierCard card={card} />}
 *     onCardMove={(id, stage) => updateCardStage(id, stage)}
 *     onCardClick={(card) => openDrawer(card)}
 *   />
 */
import { ReactNode, useCallback } from 'react'

export interface KanbanStage {
  /** Unique stable id used in card.stage and onCardMove. */
  id: string
  /** Visible column header. */
  label: string
  /** Hex/rgb colour string. Used for header text + divider + count badge. */
  color: string
}

export interface KanbanCard {
  id: string
  /** Must match a KanbanStage.id. Cards in unknown stages are dropped from view. */
  stage: string
  /** Hosts can attach any other fields and read them in renderCard. */
  [key: string]: unknown
}

export interface KanbanProps<T extends KanbanCard = KanbanCard> {
  stages: KanbanStage[]
  cards: T[]
  /** Render the card body. Drag handle / drop wiring is added by Kanban. */
  renderCard: (card: T) => ReactNode
  /** Called on drop — host should persist + optimistic-update card.stage. */
  onCardMove?: (cardId: string, newStageId: string) => void | Promise<void>
  /** Click anywhere on the card body (not a drag). */
  onCardClick?: (card: T) => void
  /** Override the empty-column placeholder. Default: localized "Drop here". */
  emptyPlaceholder?: ReactNode
  /** Min-height of the column body. Default: 200px. */
  columnMinHeight?: number
  /** Column width in px. Default: 288 (Tailwind w-72). */
  columnWidth?: number
}

const DRAG_KEY = 'kanban-card-id'

export default function Kanban<T extends KanbanCard = KanbanCard>({
  stages,
  cards,
  renderCard,
  onCardMove,
  onCardClick,
  emptyPlaceholder = (
    <div className="flex items-center justify-center h-20 text-xs text-gray-300 border-2 border-dashed border-gray-200 rounded-lg">
      Drop here
    </div>
  ),
  columnMinHeight = 200,
  columnWidth = 288,
}: KanbanProps<T>) {
  const grouped = stages.map((s) => ({
    ...s,
    cards: cards.filter((c) => c.stage === s.id),
  }))

  const handleDrop = useCallback(
    (e: React.DragEvent<HTMLDivElement>, stageId: string) => {
      e.currentTarget.classList.remove('bg-gray-100')
      const id = e.dataTransfer.getData(DRAG_KEY)
      if (id && onCardMove) onCardMove(id, stageId)
    },
    [onCardMove]
  )

  return (
    <div
      className="flex gap-3 overflow-x-auto pb-4 min-w-0"
      style={{ minHeight: 'calc(100vh - 200px)' }}
    >
      {grouped.map((stage) => (
        <div
          key={stage.id}
          className="shrink-0 flex flex-col"
          style={{ width: columnWidth }}
        >
          <div
            className="rounded-t-xl px-3 py-2.5 flex items-center justify-between"
            style={{
              backgroundColor: stage.color + '15',
              borderBottom: `2px solid ${stage.color}`,
            }}
          >
            <div className="flex items-center gap-2">
              <div
                className="w-2.5 h-2.5 rounded-full"
                style={{ backgroundColor: stage.color }}
              />
              <span
                className="text-sm font-bold"
                style={{ color: stage.color }}
              >
                {stage.label}
              </span>
            </div>
            <span
              className="text-xs font-bold px-2 py-0.5 rounded-full"
              style={{
                backgroundColor: stage.color + '20',
                color: stage.color,
              }}
            >
              {stage.cards.length}
            </span>
          </div>

          <div
            className="flex-1 bg-gray-50/80 rounded-b-xl p-2 space-y-2"
            style={{ minHeight: columnMinHeight }}
            onDragOver={(e) => {
              e.preventDefault()
              e.currentTarget.classList.add('bg-gray-100')
            }}
            onDragLeave={(e) =>
              e.currentTarget.classList.remove('bg-gray-100')
            }
            onDrop={(e) => handleDrop(e, stage.id)}
          >
            {stage.cards.length === 0 && emptyPlaceholder}
            {stage.cards.map((card) => (
              <div
                key={card.id}
                draggable
                onDragStart={(e) => {
                  e.dataTransfer.setData(DRAG_KEY, card.id)
                  e.currentTarget.classList.add('opacity-50')
                }}
                onDragEnd={(e) =>
                  e.currentTarget.classList.remove('opacity-50')
                }
                onClick={() => onCardClick?.(card)}
                className="bg-white rounded-lg border border-gray-200 p-3 hover:shadow-lg hover:border-gray-300 transition-all cursor-grab active:cursor-grabbing active:shadow-xl"
              >
                {renderCard(card)}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
