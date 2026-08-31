import { type LucideIcon } from 'lucide-react'

interface KPICardProps {
  title: string
  value: string
  subtitle?: string
  icon: LucideIcon
  trend?: { value: number; label: string }
  color?: string
}

export default function KPICard({ title, value, subtitle, icon: Icon, trend, color }: KPICardProps) {
  return (
    <div className={`rounded-xl p-4 shadow-sm border border-gray-100 ${color ? '' : 'bg-white'}`}
      style={color ? { background: `linear-gradient(135deg, ${color}, ${color}dd)`, color: 'white' } : undefined}>
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <p className={`text-xs font-medium ${color ? 'text-white/70' : 'text-gray-500'}`}>{title}</p>
          <p className="text-2xl font-bold tracking-tight">{value}</p>
          {subtitle && <p className={`text-xs ${color ? 'text-white/60' : 'text-gray-400'}`}>{subtitle}</p>}
          {trend && (
            <p className={`text-xs font-medium ${
              trend.value >= 0 ? (color ? 'text-green-200' : 'text-green-600') : (color ? 'text-red-200' : 'text-red-600')
            }`}>{trend.value >= 0 ? '↑' : '↓'} {Math.abs(trend.value)}% {trend.label}</p>
          )}
        </div>
        <Icon className={`h-8 w-8 ${color ? 'text-white/30' : 'text-gray-200'}`} />
      </div>
    </div>
  )
}
