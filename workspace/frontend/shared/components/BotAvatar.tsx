'use client'

interface BotAvatarProps {
  avatar: string
  color: string
  size?: 'sm' | 'md' | 'lg'
  status?: 'online' | 'offline' | 'busy'
}

const sizes = {
  sm: 'w-8 h-8 text-sm',
  md: 'w-10 h-10 text-lg',
  lg: 'w-14 h-14 text-2xl',
}

export default function BotAvatar({ avatar, color, size = 'md', status }: BotAvatarProps) {
  return (
    <div className="relative inline-flex">
      <div className={`${sizes[size]} rounded-xl flex items-center justify-center`}
        style={{ backgroundColor: color + '18' }}>
        {avatar}
      </div>
      {status && (
        <span className={`absolute -bottom-0.5 -right-0.5 w-3 h-3 rounded-full border-2 border-white ${
          status === 'online' ? 'bg-green-400' : status === 'busy' ? 'bg-amber-400' : 'bg-gray-300'
        }`} />
      )}
    </div>
  )
}
