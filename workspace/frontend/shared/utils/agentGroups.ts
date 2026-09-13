/** Grouping agents into sidebar sections.
 *
 * Extracted so it can be tested. It used to be an inline filter for two group
 * names, `backoffice` and `customer`, so a workspace that grouped its agents
 * any other way had those agents dropped from the sidebar — present in the
 * config and reachable by URL, but invisible. Three of four desks vanished
 * that way.
 */

export type Grouped<T> = { label: string; agents: T[] }

/** Groups that keep a curated label and lead the sidebar. */
const KNOWN = ['backoffice', 'customer']

const DEFAULT_LABELS: Record<string, string> = {
  backoffice: 'AI Team',
  customer: 'Customer-Facing',
}

export const titleCase = (g: string): string =>
  g.replace(/[-_]/g, ' ').replace(/\b\w/g, c => c.toUpperCase())

/**
 * @param agents  every agent the workspace declares
 * @param labels  per-group overrides, keyed by group name
 * @param hidden  group names to omit entirely
 */
export function groupAgents<T extends { group?: string }>(
  agents: T[],
  labels: Record<string, string> = {},
  hidden: string[] = [],
): Grouped<T>[] {
  const skip = new Set(hidden)
  const byGroup = new Map<string, T[]>()
  for (const a of agents) {
    // An agent with no group still belongs somewhere; dropping it is the bug
    // this function exists to prevent.
    const g = a.group || 'agents'
    if (skip.has(g)) continue
    if (!byGroup.has(g)) byGroup.set(g, [])
    byGroup.get(g)!.push(a)
  }
  const order = [
    ...KNOWN.filter(g => byGroup.has(g)),
    ...[...byGroup.keys()].filter(g => !KNOWN.includes(g)),
  ]
  return order.map(g => ({
    label: labels[g] || DEFAULT_LABELS[g] || titleCase(g),
    agents: byGroup.get(g)!,
  }))
}
