import assert from 'node:assert/strict'
import { groupAgents, titleCase } from './agentGroups.ts'

let pass = 0
const t = (name, fn) => { try { fn(); pass++; console.log('  ok  ' + name) } catch (e) { console.log('  FAIL ' + name + ' -> ' + e.message); process.exitCode = 1 } }

// The workspace that exposed this: four desks, only one in a known group.
const ALMANAC = [
  { id: 'advisor', name: 'Vera', group: 'customer' },
  { id: 'bench', name: 'Kelvin', group: 'engineering' },
  { id: 'providers', name: 'Mira', group: 'marketing' },
  { id: 'editor', name: 'Ines', group: 'marketing' },
]

t('no agent is dropped, whatever its group', () => {
  const shown = groupAgents(ALMANAC).flatMap(g => g.agents.map(a => a.name))
  assert.deepEqual(shown.sort(), ['Ines', 'Kelvin', 'Mira', 'Vera'])
})

t('one section per declared group', () => {
  assert.deepEqual(groupAgents(ALMANAC).map(g => g.label),
    ['Customer-Facing', 'Engineering', 'Marketing'])
})

t('known groups keep their labels and lead', () => {
  const g = groupAgents([
    { name: 'A', group: 'marketing' },
    { name: 'B', group: 'backoffice' },
    { name: 'C', group: 'customer' },
  ])
  assert.deepEqual(g.map(x => x.label), ['AI Team', 'Customer-Facing', 'Marketing'])
})

t('a label override wins over the default', () => {
  const g = groupAgents([{ name: 'A', group: 'backoffice' }], { backoffice: 'The Desk' })
  assert.equal(g[0].label, 'The Desk')
})

t('an agent with no group is still shown', () => {
  const g = groupAgents([{ name: 'A' }])
  assert.equal(g.length, 1)
  assert.equal(g[0].agents[0].name, 'A')
})

t('hidden groups are omitted', () => {
  const g = groupAgents(ALMANAC, {}, ['marketing'])
  assert.deepEqual(g.map(x => x.label), ['Customer-Facing', 'Engineering'])
})

t('agents keep their declared order within a group', () => {
  const g = groupAgents(ALMANAC).find(x => x.label === 'Marketing')
  assert.deepEqual(g.agents.map(a => a.name), ['Mira', 'Ines'])
})

t('no agents means no sections', () => {
  assert.deepEqual(groupAgents([]), [])
})

t('titleCase reads group names', () => {
  assert.equal(titleCase('engineering'), 'Engineering')
  assert.equal(titleCase('back_office'), 'Back Office')
  assert.equal(titleCase('customer-facing'), 'Customer Facing')
})

console.log(`  ${pass} passed`)
