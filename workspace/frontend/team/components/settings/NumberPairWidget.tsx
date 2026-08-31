'use client'

interface FieldDef {
  key: string
  label: string
  min?: number
  max?: number
  description?: string
}

interface NumberPairWidgetProps {
  fields: FieldDef[]
  values: Record<string, number>
  onChange: (values: Record<string, number>) => void
}

export default function NumberPairWidget({ fields, values, onChange }: NumberPairWidgetProps) {
  function update(key: string, delta: number) {
    const field = fields.find(f => f.key === key)
    const current = values[key] || 0
    const next = current + delta
    if (field?.min !== undefined && next < field.min) return
    if (field?.max !== undefined && next > field.max) return
    onChange({ ...values, [key]: next })
  }

  return (
    <div className="grid grid-cols-2 gap-4">
      {fields.map(f => (
        <div key={f.key} className="p-4 bg-gray-50 rounded-lg">
          <label className="block text-sm font-medium text-gray-700 mb-1">{f.label}</label>
          {f.description && <p className="text-xs text-gray-500 mb-3">{f.description}</p>}
          <div className="flex items-center gap-3">
            <button onClick={() => update(f.key, -1)}
              className="w-8 h-8 rounded-full bg-white border border-gray-300 flex items-center justify-center text-gray-600 hover:bg-gray-100">−</button>
            <span className="text-lg font-semibold text-gray-900 w-8 text-center">{values[f.key] || 0}</span>
            <button onClick={() => update(f.key, 1)}
              className="w-8 h-8 rounded-full bg-white border border-gray-300 flex items-center justify-center text-gray-600 hover:bg-gray-100">+</button>
          </div>
        </div>
      ))}
    </div>
  )
}
