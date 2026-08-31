'use client'

interface TextWidgetProps {
  label: string
  value: string
  onChange: (value: string) => void
  description?: string
  placeholder?: string
  secret?: boolean
  mono?: boolean
}

export default function TextWidget({ label, value, onChange, description, placeholder, secret, mono }: TextWidgetProps) {
  return (
    <div className="p-4 bg-gray-50 rounded-lg">
      <label className="block text-sm font-medium text-gray-700 mb-1">{label}</label>
      {description && <p className="text-xs text-gray-500 mb-2">{description}</p>}
      <input
        type={secret ? 'password' : 'text'}
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        className={`w-full text-sm border rounded-lg px-3 py-2 focus:outline-none focus:ring-1 focus:ring-gray-400 ${mono ? 'font-mono' : ''}`}
      />
    </div>
  )
}
