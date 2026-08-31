'use client'

import { useState, useEffect } from 'react'
import { Settings, Save, Loader2, Check } from 'lucide-react'
import {
  TextWidget,
  NumberPairWidget,
  ToggleListWidget,
  KeyValueWidget,
  GatewayStatusWidget,
  ScheduleWidget,
} from '../components/settings'

interface SettingsSection {
  id: string
  label?: string
  type: string  // text, number_pair, toggle_list, key_value, schedule, gateway_status, custom
  field?: string
  fields?: any[]
  items?: any[]
  endpoint?: string
  description?: string
  component?: string
  [key: string]: any
}

interface SettingsPageProps {
  sections: SettingsSection[]
  settingsApi?: string  // GET/PUT endpoint for values
  customWidgets?: Record<string, React.ComponentType<any>>  // app-provided widgets
}

export default function SettingsPage({ sections, settingsApi = '/api/workspace/settings', customWidgets = {} }: SettingsPageProps) {
  const [values, setValues] = useState<Record<string, any>>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    (async () => {
      try {
        const res = await fetch(settingsApi)
        if (res.ok) setValues(await res.json())
      } catch {}
      setLoading(false)
    })()
  }, [settingsApi])

  async function save() {
    setSaving(true); setSaved(false)
    try {
      const res = await fetch(settingsApi, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(values),
      })
      if (res.ok) { setSaved(true); setTimeout(() => setSaved(false), 2000) }
    } catch {}
    setSaving(false)
  }

  function updateField(key: string, val: any) {
    setValues(prev => ({ ...prev, [key]: val }))
  }

  function updateFields(updates: Record<string, any>) {
    setValues(prev => ({ ...prev, ...updates }))
  }

  function renderSection(section: SettingsSection) {
    // Custom widget from app
    if (section.type === 'custom' && section.component && customWidgets[section.component]) {
      const Widget = customWidgets[section.component]
      return <Widget section={section} values={values} onChange={updateFields} />
    }

    // App-registered widget type
    if (customWidgets[section.type]) {
      const Widget = customWidgets[section.type]
      return <Widget section={section} values={values} value={section.field ? values[section.field] : undefined}
        onChange={(v: any) => section.field ? updateField(section.field, v) : updateFields(v)} />
    }

    // Built-in widgets
    switch (section.type) {
      case 'text':
        return <TextWidget label={section.label || section.id} value={values[section.field || section.id] || ''}
          onChange={v => updateField(section.field || section.id, v)}
          description={section.description} placeholder={section.placeholder} secret={section.secret} mono={section.mono} />

      case 'number_pair':
        return <NumberPairWidget fields={section.fields || []}
          values={Object.fromEntries((section.fields || []).map((f: any) => [f.key, values[f.key] || 0]))}
          onChange={updateFields} />

      case 'toggle_list':
        return <ToggleListWidget items={section.items || []}
          values={Object.fromEntries((section.items || []).map((i: any) => [i.key, values[i.key] || false]))}
          onChange={updateFields} />

      case 'key_value':
        return <KeyValueWidget fields={section.fields || []}
          values={Object.fromEntries((section.fields || []).map((f: any) => [f.key, values[f.key] || '']))}
          onChange={updateFields} />

      case 'gateway_status':
        return <GatewayStatusWidget section={section} />

      case 'schedule':
        return <ScheduleWidget label={section.label || section.id}
          value={values[section.field || section.id] || ''}
          onChange={v => updateField(section.field || section.id, v)}
          description={section.description} />

      default:
        return <p className="text-xs text-gray-400">Unknown widget type: {section.type}</p>
    }
  }

  return (
    <div className="p-6 max-w-3xl mx-auto space-y-5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-gray-900 flex items-center justify-center">
            <Settings className="h-5 w-5 text-white" />
          </div>
          <div>
            <h1 className="text-xl font-bold text-gray-900">Settings</h1>
            <p className="text-xs text-gray-500">Configure your workspace</p>
          </div>
        </div>
        <button onClick={save} disabled={saving || loading}
          className={`flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-lg text-white transition-colors ${
            saved ? 'bg-green-600' : 'bg-purple-600 hover:bg-purple-700'
          } disabled:opacity-50`}>
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : saved ? <Check className="h-4 w-4" /> : <Save className="h-4 w-4" />}
          {saved ? 'Saved!' : 'Save Changes'}
        </button>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-6 w-6 animate-spin text-gray-300" />
        </div>
      ) : (
        <div className="space-y-6">
          {sections.map(section => (
            <div key={section.id} className="bg-white rounded-xl border border-gray-100 overflow-hidden">
              {section.label && (
                <div className="px-5 py-3 border-b border-gray-100 bg-gradient-to-r from-gray-50 to-white">
                  <h2 className="text-sm font-semibold text-gray-700">{section.label}</h2>
                  {section.description && <p className="text-xs text-gray-500 mt-0.5">{section.description}</p>}
                </div>
              )}
              <div className="p-5">
                {renderSection(section)}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
