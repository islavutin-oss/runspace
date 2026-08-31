'use client'

import { useMemo } from 'react'

import { describeSchedule } from '../../../shared/utils/describeSchedule'

interface Props {
  value: string
  onChange: (value: string) => void
  /** Accepted for parity with the other widgets; the card header renders them. */
  label?: string
  description?: string
}

const PRESETS: { label: string; cron: string }[] = [
  { label: 'Every hour', cron: '0 * * * *' },
  { label: 'Weekday mornings', cron: '0 8 * * 1-5' },
  { label: 'Every morning', cron: '0 8 * * *' },
  { label: 'Friday afternoon', cron: '0 17 * * 5' },
  { label: 'Monday morning', cron: '0 9 * * 1' },
  { label: 'First of the month', cron: '0 9 1 * *' },
]

/**
 * A cron expression, with the parts spelled out underneath.
 *
 * `schedule` was named in the settings type union but never implemented, so a
 * section declaring it rendered "Unknown widget type". Cron is also the kind
 * of field people get wrong silently — `0 8 * * 1-5` and `0 8 1-5 * *` differ
 * by a lot — so this says in words what the expression means.
 */
// `label` and `description` come in from the section but are rendered by
// SettingsPage's card header, so repeating them here duplicated every heading.
export default function ScheduleWidget({ value, onChange }: Props) {
  const summary = useMemo(() => describeSchedule(value), [value])

  return (
    <div className="p-4 bg-gray-50 rounded-lg">
      <input
        value={value || ''}
        onChange={(e) => onChange(e.target.value)}
        placeholder="0 8 * * 1-5"
        spellCheck={false}
        className="w-full text-sm font-mono border rounded-lg px-3 py-2 focus:outline-none focus:ring-1 focus:ring-gray-400"
      />
      <p
        className={`text-xs mt-1.5 ${
          !value?.trim() ? 'text-gray-400' : summary.ok ? 'text-gray-600' : 'text-amber-700'
        }`}
      >
        {!value?.trim() ? 'Not scheduled. Pick one below, or type a cron expression.' : summary.text}
      </p>
      <div className="flex flex-wrap gap-1.5 mt-2.5">
        {PRESETS.map((p) => (
          <button
            key={p.cron}
            type="button"
            onClick={() => onChange(p.cron)}
            className={`text-[11px] px-2 py-1 rounded-full border transition-colors ${
              value === p.cron
                ? 'bg-gray-900 text-white border-gray-900'
                : 'bg-white text-gray-600 border-gray-200 hover:border-gray-400'
            }`}
          >
            {p.label}
          </button>
        ))}
      </div>
    </div>
  )
}
