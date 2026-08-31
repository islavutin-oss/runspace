/** Plain-English reading of a five-field cron expression.
 *
 * Kept out of the widget because it is pure logic worth testing directly, and
 * because cron is the kind of field people get wrong silently: `0 8 * * 1-5`
 * and `0 8 1-5 * *` look almost identical and mean very different things.
 */
const DOW = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

export function describeSchedule(expr: string): { ok: boolean; text: string } {
  const parts = (expr || '').trim().split(/\s+/).filter(Boolean)
  if (parts.length !== 5) {
    return { ok: false, text: 'Five fields expected: minute hour day-of-month month day-of-week.' }
  }
  const [min, hour, dom, mon, dow] = parts
  if (!/^[\d*/,-]+$/.test(min + hour + dom + mon + dow)) {
    return { ok: false, text: 'Only digits and the characters * , - / are allowed.' }
  }

  const time =
    /^\d+$/.test(min) && /^\d+$/.test(hour)
      ? `at ${hour.padStart(2, '0')}:${min.padStart(2, '0')}`
      : hour === '*'
        ? min === '*'
          ? 'every minute'
          : `at minute ${min} of every hour`
        : `at hour ${hour}, minute ${min}`

  let when = 'every day'
  if (dow !== '*') {
    const named = dow
      .split(',')
      .map((d) => {
        const range = d.split('-').map((n) => DOW[Number(n) % 7])
        return range.length === 2 ? `${range[0]}–${range[1]}` : range[0]
      })
      .filter(Boolean)
      .join(', ')
    when = named ? `on ${named}` : `on day-of-week ${dow}`
  } else if (dom !== '*') {
    when = `on day ${dom} of the month`
  }
  const month = mon === '*' ? '' : `, in month ${mon}`

  return { ok: true, text: `Runs ${time}, ${when}${month}.` }
}
