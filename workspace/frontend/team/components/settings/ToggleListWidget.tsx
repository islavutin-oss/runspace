'use client'

interface ToggleItem {
  key: string
  label: string
  description?: string
}

interface ToggleListWidgetProps {
  items: ToggleItem[]
  values: Record<string, boolean>
  onChange: (values: Record<string, boolean>) => void
}

export default function ToggleListWidget({ items, values, onChange }: ToggleListWidgetProps) {
  function toggle(key: string) {
    onChange({ ...values, [key]: !values[key] })
  }

  return (
    <div className="space-y-2">
      {items.map(item => (
        <div key={item.key} className="flex items-center justify-between p-3 bg-gray-50 rounded-lg">
          <div>
            <p className="text-sm font-medium text-gray-700">{item.label}</p>
            {item.description && <p className="text-xs text-gray-500">{item.description}</p>}
          </div>
          <button onClick={() => toggle(item.key)}
            className={`w-10 h-6 rounded-full transition-colors relative ${values[item.key] ? 'bg-green-600' : 'bg-gray-300'}`}>
            <span className={`absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform ${values[item.key] ? 'left-[18px]' : 'left-0.5'}`} />
          </button>
        </div>
      ))}
    </div>
  )
}
