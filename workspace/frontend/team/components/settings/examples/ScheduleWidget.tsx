'use client'

import { Clock } from 'lucide-react'

interface DayHours { open: string | null; close: string | null; closed: boolean }

const DAY_NAMES = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
const DAY_LABELS: Record<string, string> = {
  monday: 'Monday', tuesday: 'Tuesday', wednesday: 'Wednesday',
  thursday: 'Thursday', friday: 'Friday', saturday: 'Saturday', sunday: 'Sunday',
}

interface ScheduleWidgetProps {
  value: Record<string, DayHours>
  onChange: (value: Record<string, DayHours>) => void
}

export default function ScheduleWidget({ value, onChange }: ScheduleWidgetProps) {
  function updateDay(day: string, field: string, val: any) {
    if (field === 'closed') {
      onChange({ ...value, [day]: val ? { open: null, close: null, closed: true } : { open: '17:00', close: '23:00', closed: false } })
    } else {
      onChange({ ...value, [day]: { ...value[day], [field]: val } })
    }
  }

  return (
    <div className="space-y-2">
      {DAY_NAMES.map(day => {
        const hours = value[day]
        if (!hours) return null
        const isClosed = hours.closed
        return (
          <div key={day} className={`flex items-center gap-4 p-3 rounded-lg transition-all ${isClosed ? 'bg-gray-50' : 'bg-green-50'}`}>
            <span className="w-28 font-medium text-gray-900 text-sm">{DAY_LABELS[day]}</span>
            <button onClick={() => updateDay(day, 'closed', !isClosed)}
              className={`px-3 py-1 rounded-full text-sm font-medium transition-all ${isClosed ? 'bg-gray-200 text-gray-600' : 'bg-green-600 text-white'}`}>
              {isClosed ? 'Closed' : 'Open'}
            </button>
            {!isClosed && (
              <div className="flex items-center gap-2 ml-auto">
                <input type="time" value={hours.open || ''} onChange={e => updateDay(day, 'open', e.target.value)}
                  className="px-2 py-1 border rounded text-sm" />
                <span className="text-gray-500">to</span>
                <input type="time" value={hours.close || ''} onChange={e => updateDay(day, 'close', e.target.value)}
                  className="px-2 py-1 border rounded text-sm" />
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
