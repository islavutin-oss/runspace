'use client'

interface FieldDef {
  key: string
  label: string
  description?: string
  secret?: boolean
  placeholder?: string
}

interface KeyValueWidgetProps {
  fields: FieldDef[]
  values: Record<string, string>
  onChange: (values: Record<string, string>) => void
}

export default function KeyValueWidget({ fields, values, onChange }: KeyValueWidgetProps) {
  function update(key: string, val: string) {
    onChange({ ...values, [key]: val })
  }

  return (
    <div className="space-y-3">
      {fields.map(f => (
        <div key={f.key}>
          <label className="block text-sm font-medium text-gray-700 mb-1">{f.label}</label>
          {f.description && <p className="text-xs text-gray-500 mb-1">{f.description}</p>}
          <input
            type={f.secret ? 'password' : 'text'}
            value={values[f.key] || ''}
            onChange={e => update(f.key, e.target.value)}
            placeholder={f.placeholder}
            className="w-full text-sm border rounded-lg px-3 py-2 focus:outline-none focus:ring-1 focus:ring-gray-400"
          />
        </div>
      ))}
    </div>
  )
}
