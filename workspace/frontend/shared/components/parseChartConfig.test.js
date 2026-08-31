/**
 * Tests for parseChartConfig — runs with plain Node, no dependencies.
 * Usage: node parseChartConfig.test.js
 */

function parseChartConfig(json) {
  try {
    let text = json.trim()
    if (!text.startsWith('{')) {
      const match = text.match(/\{[\s\S]*\}/)
      if (match) text = match[0]
    }
    const parsed = JSON.parse(text)

    if (!parsed.data && parsed.labels && parsed.series) {
      const labels = parsed.labels
      const series = parsed.series
      const yKey = series[0]?.name?.toLowerCase().replace(/\s+/g, '_') || 'value'
      parsed.data = labels.map((label, i) => {
        const row = { label }
        for (const s of series) {
          const key = (s.name || `series_${i}`).toLowerCase().replace(/\s+/g, '_')
          row[key] = s.values[i] ?? 0
        }
        return row
      })
      if (!parsed.xKey) parsed.xKey = 'label'
      if (!parsed.yKey) parsed.yKey = yKey
      if (series.length > 1) parsed.y2Key = parsed.y2Key || (series[1].name || 'series_1').toLowerCase().replace(/\s+/g, '_')
    }

    if (!parsed.data && parsed.labels && parsed.datasets) {
      const labels = parsed.labels
      const datasets = parsed.datasets
      const yKey = datasets[0]?.label?.toLowerCase().replace(/\s+/g, '_') || 'value'
      parsed.data = labels.map((label, i) => {
        const row = { label }
        for (const ds of datasets) {
          const key = (ds.label || `dataset_${i}`).toLowerCase().replace(/\s+/g, '_')
          row[key] = ds.data[i] ?? 0
        }
        return row
      })
      if (!parsed.xKey) parsed.xKey = 'label'
      if (!parsed.yKey) parsed.yKey = yKey
      if (datasets.length > 1) parsed.y2Key = parsed.y2Key || (datasets[1].label || 'dataset_1').toLowerCase().replace(/\s+/g, '_')
    }

    if (!parsed.data || !Array.isArray(parsed.data)) return { config: null, error: `missing data array (keys: ${Object.keys(parsed).join(', ')})` }
    if (parsed.data.length === 0) return { config: null, error: 'empty data array' }

    // Normalize key aliases
    if (!parsed.xKey) parsed.xKey = parsed.nameKey || parsed.labelKey || parsed.categoryKey || parsed.category
    if (!parsed.yKey) parsed.yKey = parsed.valueKey || parsed.dataKey || parsed.amountKey || parsed.metricKey

    // Auto-detect from data shape
    if (!parsed.xKey || !parsed.yKey) {
      const first = parsed.data[0]
      if (!first || typeof first !== 'object') return { config: null, error: 'invalid data[0]' }
      const keys = Object.keys(first)
      if (keys.length === 0) return { config: null, error: 'empty data[0]' }
      const stringKey = keys.find(k => typeof first[k] === 'string') || keys[0]
      const numberKey = keys.find(k => k !== stringKey && typeof first[k] === 'number') || keys.find(k => k !== stringKey) || keys[1]
      if (!parsed.xKey) parsed.xKey = stringKey
      if (!parsed.yKey) parsed.yKey = numberKey
    }

    if (!parsed.xKey || !parsed.yKey) return { config: null, error: 'could not determine xKey/yKey' }

    return { config: parsed, error: '' }
  } catch (e) {
    return { config: null, error: String(e).slice(0, 100) }
  }
}

// ── Test runner ──
let pass = 0, fail = 0
function test(name, fn) {
  try { fn(); pass++; console.log(`  OK  ${name}`) }
  catch (e) { fail++; console.log(`  FAIL ${name}: ${e.message}`) }
}
function assert(cond, msg) { if (!cond) throw new Error(msg || 'assertion failed') }
function eq(a, b, msg) { assert(JSON.stringify(a) === JSON.stringify(b), msg || `expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`) }

// ── Tests ──
console.log('parseChartConfig tests:\n')

test('standard bar chart with xKey/yKey', () => {
  const { config } = parseChartConfig(JSON.stringify({
    type: 'bar', xKey: 'date', yKey: 'revenue',
    data: [{ date: '04-17', revenue: 6200 }, { date: '04-18', revenue: 5800 }]
  }))
  assert(config !== null)
  eq(config.xKey, 'date')
  eq(config.yKey, 'revenue')
})

test('pie chart auto-detects xKey/yKey from data', () => {
  const { config } = parseChartConfig(JSON.stringify({
    type: 'pie', data: [{ name: 'Apr 16', value: 1363 }, { name: 'Apr 17', value: 671 }]
  }))
  assert(config !== null)
  eq(config.xKey, 'name')
  eq(config.yKey, 'value')
})

test('pie chart with nameKey/valueKey aliases', () => {
  const { config } = parseChartConfig(JSON.stringify({
    type: 'pie', nameKey: 'date', valueKey: 'revenue',
    data: [{ date: '04-17', revenue: 671 }, { date: '04-18', revenue: 1270 }]
  }))
  assert(config !== null)
  eq(config.xKey, 'date')
  eq(config.yKey, 'revenue')
})

test('chart with labelKey/dataKey aliases', () => {
  const { config } = parseChartConfig(JSON.stringify({
    type: 'bar', labelKey: 'product', dataKey: 'sales',
    data: [{ product: 'Wine', sales: 500 }]
  }))
  assert(config !== null)
  eq(config.xKey, 'product')
  eq(config.yKey, 'sales')
})

test('chart with categoryKey/amountKey aliases', () => {
  const { config } = parseChartConfig(JSON.stringify({
    type: 'pie', categoryKey: 'item', amountKey: 'total',
    data: [{ item: 'Food', total: 800 }]
  }))
  assert(config !== null)
  eq(config.xKey, 'item')
  eq(config.yKey, 'total')
})

test('pie chart with explicit xKey/yKey', () => {
  const { config } = parseChartConfig(JSON.stringify({
    type: 'pie', xKey: 'date', yKey: 'revenue',
    data: [{ date: 'Mon', revenue: 500 }]
  }))
  assert(config !== null)
  eq(config.xKey, 'date')
})

test('Chart.js labels+series normalization', () => {
  const { config } = parseChartConfig(JSON.stringify({
    type: 'bar', labels: ['Mon', 'Tue', 'Wed'],
    series: [{ name: 'Revenue', values: [100, 200, 300] }]
  }))
  assert(config !== null)
  eq(config.yKey, 'revenue')
  eq(config.data.length, 3)
})

test('Chart.js labels+datasets normalization', () => {
  const { config } = parseChartConfig(JSON.stringify({
    type: 'line', labels: ['Jan', 'Feb'],
    datasets: [{ label: 'Sales', data: [10, 20] }]
  }))
  assert(config !== null)
  eq(config.yKey, 'sales')
})

test('series with undefined name does not crash', () => {
  const { config } = parseChartConfig(JSON.stringify({
    type: 'bar', labels: ['A', 'B'], series: [{ values: [10, 20] }]
  }))
  assert(config !== null)
})

test('datasets with undefined label does not crash', () => {
  const { config } = parseChartConfig(JSON.stringify({
    type: 'bar', labels: ['X', 'Y'], datasets: [{ data: [5, 15] }]
  }))
  assert(config !== null)
})

test('extracts JSON from surrounding text', () => {
  const { config } = parseChartConfig('Here is the chart: {"type":"bar","xKey":"x","yKey":"y","data":[{"x":"a","y":1}]} enjoy!')
  assert(config !== null)
  eq(config.type, 'bar')
})

test('invalid JSON returns error', () => {
  const { config, error } = parseChartConfig('not json at all')
  assert(config === null)
  assert(error.length > 0)
})

test('missing data array returns error', () => {
  const { config, error } = parseChartConfig(JSON.stringify({ type: 'bar', xKey: 'x', yKey: 'y' }))
  assert(config === null)
  assert(error.includes('missing data'))
})

test('empty data array returns error', () => {
  const { config, error } = parseChartConfig(JSON.stringify({ type: 'bar', data: [] }))
  assert(config === null)
  eq(error, 'empty data array')
})

test('two series sets y2Key', () => {
  const { config } = parseChartConfig(JSON.stringify({
    type: 'bar', labels: ['A', 'B'],
    series: [{ name: 'Revenue', values: [100, 200] }, { name: 'Orders', values: [10, 20] }]
  }))
  assert(config !== null)
  eq(config.y2Key, 'orders')
})

test('heatmap with explicit keys passes through', () => {
  const { config } = parseChartConfig(JSON.stringify({
    type: 'heatmap', xKey: 'hour', yKey: 'day', valueKey: 'orders',
    data: [{ hour: '12', day: 'Mon', orders: 5 }]
  }))
  assert(config !== null)
  eq(config.type, 'heatmap')
})

test('auto-detect when all fields are numbers', () => {
  const { config } = parseChartConfig(JSON.stringify({
    type: 'line', data: [{ x: 1, y: 10 }, { x: 2, y: 20 }]
  }))
  assert(config !== null)
  eq(config.xKey, 'x')
  eq(config.yKey, 'y')
})

test('real LLM pie chart — date+revenue without keys', () => {
  const { config } = parseChartConfig(JSON.stringify({
    type: 'pie', title: 'April Revenue',
    data: [{ date: '2026-04-16', revenue: 1363 }, { date: '2026-04-17', revenue: 671 }]
  }))
  assert(config !== null)
  eq(config.xKey, 'date')
  eq(config.yKey, 'revenue')
})

test('data[0] is null does not crash', () => {
  const { config } = parseChartConfig(JSON.stringify({ type: 'bar', data: [null, { x: 'a', y: 1 }] }))
  assert(config === null) // null is not a valid object
})

test('chart JSON embedded after explanation text', () => {
  const { config } = parseChartConfig('Sure: {"type":"pie","data":[{"name":"Food","value":841},{"name":"Wine","value":395}]}')
  assert(config !== null)
  eq(config.type, 'pie')
  eq(config.data.length, 2)
})

test('real LLM: top products pie with xKey/yKey (should just work)', () => {
  const { config } = parseChartConfig(JSON.stringify({
    type: 'pie', title: 'Revenue Share — Top 8',
    data: [{ name: 'Beef tenderloin', revenue: 1470 }, { name: 'Octopus', revenue: 1102 }],
    xKey: 'name', yKey: 'revenue', yFormat: 'currency'
  }))
  assert(config !== null)
  eq(config.xKey, 'name')
  eq(config.yKey, 'revenue')
})

test('real LLM: pie with nameKey+valueKey (alias resolution)', () => {
  const { config } = parseChartConfig(JSON.stringify({
    type: 'pie', title: 'Daily Revenue Share',
    data: [{ date: '04-17', revenue: 671 }, { date: '04-18', revenue: 1270 }],
    nameKey: 'date', valueKey: 'revenue'
  }))
  assert(config !== null)
  eq(config.xKey, 'date')
  eq(config.yKey, 'revenue')
})

// ── Summary ──
console.log(`\n${pass + fail} tests: ${pass} passed, ${fail} failed`)
process.exit(fail > 0 ? 1 : 0)
