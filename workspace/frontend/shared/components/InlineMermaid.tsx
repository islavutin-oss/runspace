'use client'

import { useEffect, useRef, useState, memo } from 'react'

let mermaidPromise: Promise<any> | null = null
const svgCache = new Map<string, string>()

function loadMermaid() {
  if (!mermaidPromise) {
    mermaidPromise = import('mermaid').then(m => {
      m.default.initialize({ startOnLoad: false, theme: 'neutral', securityLevel: 'strict' })
      return m.default
    }).catch(() => null)
  }
  return mermaidPromise
}

function InlineMermaid({ code }: { code: string }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [svg, setSvg] = useState(() => svgCache.get(code) || '')
  const [error, setError] = useState('')

  useEffect(() => {
    if (svg) return // already cached
    let cancelled = false
    loadMermaid().then(async (mermaid) => {
      if (cancelled || !mermaid) {
        if (!mermaid && !cancelled) setError('mermaid not installed')
        return
      }
      try {
        const id = `m${Math.random().toString(36).slice(2, 9)}`
        const result = await mermaid.render(id, code)
        if (!cancelled) {
          svgCache.set(code, result.svg)
          setSvg(result.svg)
        }
      } catch (e) {
        if (!cancelled) setError(String(e).slice(0, 120))
      }
    })
    return () => { cancelled = true }
  }, [code, svg])

  if (error) {
    return <pre className="text-xs text-gray-500 bg-gray-50 p-2 rounded overflow-x-auto">{code}</pre>
  }

  if (!svg) {
    return (
      <div className="my-3 p-3 bg-white rounded-lg border border-gray-200 shadow-sm not-prose">
        <div className="text-xs text-gray-400">Rendering chart...</div>
      </div>
    )
  }

  return (
    <div className="my-3 p-3 bg-white rounded-lg border border-gray-200 shadow-sm not-prose">
      <div dangerouslySetInnerHTML={{ __html: svg }} />
    </div>
  )
}

export default memo(InlineMermaid)
