'use client'

/**
 * Widget intent dispatcher — single channel for widgets (clickable
 * rows, action buttons, chart-point drill-throughs) to inject a
 * synthetic user message back into the chat.
 *
 * Design choice: every interactive widget posts the click as a normal
 * user turn (`{text: "show Tuesday's orders"}`). It does NOT bypass
 * the agent loop with a typed tool intent — the agent's deliberation
 * gates (e.g. Ada's «never auto-mark paid» rule) still get to fire.
 *
 * The host (AgentChat) wires `dispatch` to its `sendMessage`. Widgets
 * consume it via `useWidgetIntent()`. When no provider is in scope
 * (e.g. dashboard rendering an isolated table), the hook returns a
 * no-op-resolving dispatcher and the click silently does nothing —
 * widgets never crash.
 *
 * Promise contract: `dispatch` returns a Promise that resolves when
 * the assistant's reply has fully landed (HTTP 200 + onResponse
 * fired), and rejects on transport / agent error. Widgets use this
 * to flip from "pending" to "applied" / "failed" states realistically
 * — visually mirroring server truth, no optimistic lying.
 */
import { createContext, useContext, type ReactNode } from 'react'

export interface WidgetIntent {
  /** The synthetic user message text — sent as if the user typed it. */
  text: string
  /** Diagnostic only. Not used for routing — kept for telemetry/logs. */
  source?: 'row' | 'action' | 'point'
  /** Diagnostic only. e.g. `{invoiceId: "i07"}` for log correlation. */
  meta?: Record<string, unknown>
}

/** Outcome after the assistant's reply lands. The widget uses this to
 *  flip its visual state — e.g. row status updates, button vanishes. */
export interface WidgetIntentResult {
  ok: boolean
  /** Free-form reply text the agent emitted. Useful for showing inline
   *  confirmation / error to the user from inside the widget. */
  reply?: string
  /** Error message when ok=false (transport error or agent rejection). */
  error?: string
}

export type WidgetIntentDispatcher =
  (intent: WidgetIntent) => Promise<WidgetIntentResult>

const WidgetIntentContext = createContext<WidgetIntentDispatcher | null>(null)

export function WidgetIntentProvider(
  { dispatch, children }: { dispatch: WidgetIntentDispatcher; children: ReactNode },
) {
  return (
    <WidgetIntentContext.Provider value={dispatch}>
      {children}
    </WidgetIntentContext.Provider>
  )
}

/** Returns a dispatcher; falls back to a no-op-resolving dispatcher
 *  when no provider is in scope so widgets render in non-chat
 *  surfaces (dashboards) without blowing up. Callers can still
 *  detect "no host" via `useIsWidgetIntentWired()`. */
export function useWidgetIntent(): WidgetIntentDispatcher {
  return useContext(WidgetIntentContext)
    ?? (async () => ({ ok: false, error: 'no chat host wired' }))
}

/** True when a real provider is in scope. */
export function useIsWidgetIntentWired(): boolean {
  return useContext(WidgetIntentContext) !== null
}
