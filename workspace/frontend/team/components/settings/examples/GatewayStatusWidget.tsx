'use client'

import { useState, useEffect, useCallback } from 'react'
import { Wifi, WifiOff, Loader2, KeyRound, Copy, Check, Link2Off, RefreshCw } from 'lucide-react'

type Status = 'connected' | 'waiting_scan' | 'pairing_code' | 'initializing' | 'unreachable'

const STATUS_COLORS: Record<Status, string> = {
  connected: 'text-green-700 bg-green-50 border-green-200',
  waiting_scan: 'text-amber-700 bg-amber-50 border-amber-200',
  pairing_code: 'text-purple-700 bg-purple-50 border-purple-200',
  initializing: 'text-blue-700 bg-blue-50 border-blue-200',
  unreachable: 'text-red-700 bg-red-50 border-red-200',
}
const STATUS_LABELS: Record<Status, string> = {
  connected: 'Connected', waiting_scan: 'Waiting for QR scan',
  pairing_code: 'Enter pairing code', initializing: 'Starting up…', unreachable: 'Offline',
}

interface GatewayStatusWidgetProps {
  endpoint: string  // e.g. /api/gateway/qr
}

export default function GatewayStatusWidget({ endpoint }: GatewayStatusWidgetProps) {
  const [status, setStatus] = useState<Status>('initializing')
  const [qr, setQr] = useState<string | null>(null)
  const [pairingCode, setPairingCode] = useState<string | null>(null)
  const [phone, setPhone] = useState<string | null>(null)
  const [pairingPhone, setPairingPhone] = useState<string | null>(null)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  const [copied, setCopied] = useState(false)
  const [disconnecting, setDisconnecting] = useState(false)
  const [reconnecting, setReconnecting] = useState(false)

  const poll = useCallback(async () => {
    try {
      const res = await fetch(endpoint)
      if (res.ok) {
        const d = await res.json()
        setStatus((d.status as Status) || 'unreachable')
        setQr(d.qr ?? null)
        setPairingCode(d.pairing_code ?? null)
        setPhone(d.connected_phone ?? null)
        setPairingPhone(d.pairing_phone ?? null)
      } else setStatus('unreachable')
    } catch { setStatus('unreachable') }
    setLastUpdated(new Date())
  }, [endpoint])

  useEffect(() => { poll(); const i = setInterval(poll, 5000); return () => clearInterval(i) }, [poll])

  const canDisconnect = status === 'connected' || status === 'waiting_scan' || status === 'pairing_code'

  return (
    <div className="space-y-4">
      <div className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-sm font-medium ${STATUS_COLORS[status]}`}>
        {status === 'connected' && <Wifi className="h-4 w-4" />}
        {status === 'unreachable' && <WifiOff className="h-4 w-4" />}
        {status === 'pairing_code' && <KeyRound className="h-4 w-4" />}
        {(status === 'initializing' || status === 'waiting_scan') && <Loader2 className="h-4 w-4 animate-spin" />}
        {STATUS_LABELS[status]}
        {lastUpdated && <span className="ml-auto text-xs font-normal opacity-60">{lastUpdated.toLocaleTimeString()}</span>}
      </div>

      {status === 'connected' && (
        <div className="text-center py-4">
          <Wifi className="h-10 w-10 text-green-500 mx-auto mb-2" />
          <p className="font-medium text-gray-700">Gateway connected</p>
          {phone && <p className="text-sm font-mono text-green-700 mt-1 bg-green-50 inline-block px-3 py-1 rounded-full border border-green-200">{phone}</p>}
        </div>
      )}

      {status === 'waiting_scan' && qr && (
        <div className="flex flex-col items-center gap-3">
          <div className="border-2 border-gray-200 rounded-xl p-3 bg-white">
            <img src={qr} alt="QR" width={220} height={220} />
          </div>
          <p className="text-sm text-center text-gray-500">Scan with WhatsApp → Linked Devices → Link a Device</p>
        </div>
      )}

      {status === 'pairing_code' && pairingCode && (
        <div className="flex flex-col items-center gap-3 py-2">
          <div className="flex items-center gap-3">
            <div className="font-mono text-3xl font-bold tracking-widest text-gray-900 bg-gray-100 px-6 py-4 rounded-xl border-2 border-gray-200 select-all">{pairingCode}</div>
            <button onClick={() => { navigator.clipboard.writeText(pairingCode); setCopied(true); setTimeout(() => setCopied(false), 2000) }}
              className="p-2 rounded-lg text-gray-400 hover:text-gray-700 hover:bg-gray-100">
              {copied ? <Check className="h-5 w-5 text-green-500" /> : <Copy className="h-5 w-5" />}
            </button>
          </div>
          {pairingPhone && <p className="text-sm text-gray-700">Open WhatsApp on <span className="font-mono text-purple-700">{pairingPhone}</span></p>}
        </div>
      )}

      {status === 'initializing' && (
        <div className="text-center py-4 text-gray-500">
          <Loader2 className="h-10 w-10 text-blue-400 mx-auto mb-2 animate-spin" />
          <p className="font-medium text-gray-700">Connecting…</p>
        </div>
      )}

      {status === 'unreachable' && (
        <div className="text-center py-4 text-gray-500">
          <WifiOff className="h-10 w-10 text-red-400 mx-auto mb-2" />
          <p className="font-medium text-gray-700">Gateway offline</p>
          <button onClick={poll} className="mt-3 px-3 py-1 text-sm rounded-lg border border-gray-200 hover:bg-gray-50">
            <RefreshCw className="h-3 w-3 inline mr-1" /> Retry
          </button>
        </div>
      )}

      {canDisconnect && (
        <div className="pt-2 border-t flex items-center justify-between gap-2">
          {status !== 'connected' ? (
            <button disabled={reconnecting} onClick={async () => { setReconnecting(true); await fetch(endpoint.replace('/qr', '/reconnect'), { method: 'POST' }); setReconnecting(false); poll() }}
              className="flex items-center gap-1 px-3 py-1.5 text-sm rounded-lg border border-gray-200 hover:bg-gray-50">
              <RefreshCw className={`h-3.5 w-3.5 ${reconnecting ? 'animate-spin' : ''}`} /> {reconnecting ? 'Restarting…' : 'Reconnect'}
            </button>
          ) : <div />}
          <button disabled={disconnecting} onClick={async () => { if (!confirm('Disconnect? You will need to scan QR again.')) return; setDisconnecting(true); await fetch(endpoint.replace('/qr', '/disconnect'), { method: 'POST' }); setDisconnecting(false); poll() }}
            className="flex items-center gap-1 px-3 py-1.5 text-sm rounded-lg text-red-600 hover:bg-red-50">
            <Link2Off className="h-3.5 w-3.5" /> {disconnecting ? 'Disconnecting…' : 'Disconnect'}
          </button>
        </div>
      )}
    </div>
  )
}
