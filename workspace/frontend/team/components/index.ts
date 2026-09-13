export { default as Sidebar, type SidebarConfig, type ChannelConfig, type AgentConfig } from './Sidebar'
export { default as ResizeHandle } from './ResizeHandle'
export { default as ModeSwitcher, type WorkspaceMode } from './ModeSwitcher'
export { default as DashboardPanel, type DashboardSpec, type DashboardMessage } from './DashboardPanel'
export { default as Kanban, type KanbanStage } from './Kanban'
export { default as ThreadPanel } from './ThreadPanel'
export { default as KnowledgeBrowser, type KBGroup, type KBItem } from './KnowledgeBrowser'

// The message surface and the inline blocks. These shipped in the package but
// were never exported from it, so a consumer had to deep-import paths that are
// not part of the public shape. The block components are the client half of the
// fence protocol the server already emits (chart, datatable, kpi, insight,
// form, file) — a workspace that cannot render them has half a protocol.


export {
  type WidgetIntent, type WidgetIntentResult, WidgetIntentDispatcher,
  WidgetIntentProvider, useWidgetIntent, useIsWidgetIntentWired,
export {
  type ChartType, type ChartConfig, type ParseResult, parseChartConfig,
