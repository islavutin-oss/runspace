// A cron expression people get wrong silently: `0 8 * * 1-5` and `0 8 1-5 * *`
// differ by a lot and look almost identical. The widget spells one out; these
// pin that it says the right thing.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { describeSchedule as describeCron } from './describeSchedule.ts'

test('weekday mornings read as weekdays, not days of the month', () => {
  const r = describeCron('0 8 * * 1-5')
  assert.equal(r.ok, true)
  assert.match(r.text, /08:00/)
  assert.match(r.text, /Monday–Friday/)
  assert.doesNotMatch(r.text, /day 1-5 of the month/)
})

test('a day-of-month schedule is not read as a weekday', () => {
  const r = describeCron('0 9 1 * *')
  assert.equal(r.ok, true)
  assert.match(r.text, /day 1 of the month/)
  assert.doesNotMatch(r.text, /Monday/)
})

test('the time is zero-padded so 0 8 does not read as 8:0', () => {
  assert.match(describeCron('5 8 * * *').text, /08:05/)
})

test('the wrong number of fields is rejected rather than guessed at', () => {
  for (const bad of ['', '0 8 * *', '0 8 * * 1-5 7']) {
    assert.equal(describeCron(bad).ok, false, `accepted ${JSON.stringify(bad)}`)
  }
})

test('characters cron does not allow are rejected', () => {
  assert.equal(describeCron('0 8 * * MON').ok, false)
})

test('every-minute is described, not left blank', () => {
  assert.match(describeCron('* * * * *').text, /every minute/)
})
