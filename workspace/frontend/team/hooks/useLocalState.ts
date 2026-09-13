'use client'

import { useState, useEffect, useRef, useCallback } from 'react'

/**
 * Strip binary data (images, attachments with data URLs) before persisting.
 * Only text-based fields survive localStorage — images are session-only.
 */
function stripBinaryData(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(stripBinaryData)
  }
  if (value && typeof value === 'object') {
    const obj = value as Record<string, unknown>
    const cleaned: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(obj)) {
      // Drop images array and attachments with data URLs
      if (k === 'images') continue
      if (k === 'attachments') continue
      // Recurse into nested objects/arrays (e.g. thread replies map)
      cleaned[k] = stripBinaryData(v)
    }
    return cleaned
  }
  return value
}

/**
 * useState backed by localStorage. Persists across page refreshes.
 * - Debounced writes (300ms) to avoid thrashing
 * - Strips images/attachments (binary data) before saving — text only
 * - Merges initialValue on first load if no stored data exists
 * - JSON serialization (works with objects, arrays, primitives)
 */
export function useLocalState<T>(key: string, initialValue: T): [T, (value: T | ((prev: T) => T)) => void, () => void] {
  const [state, setState] = useState<T>(() => {
    if (typeof window === 'undefined') return initialValue
    try {
      const stored = localStorage.getItem(key)
      if (stored) {
        const parsed = JSON.parse(stored)
        // For arrays: merge initial + stored (initial may have demo data)
        if (Array.isArray(initialValue) && Array.isArray(parsed)) {
          // If stored is non-empty, use it; otherwise use initial
          return parsed.length > 0 ? parsed as T : initialValue
        }
        // For objects: merge (stored wins)
        if (initialValue && typeof initialValue === 'object' && !Array.isArray(initialValue) && typeof parsed === 'object') {
          return { ...initialValue, ...parsed } as T
        }
        return parsed as T
      }
    } catch {}
    return initialValue
  })

  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Debounced save to localStorage
  useEffect(() => {
    if (saveTimer.current) clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(() => {
      try {
        localStorage.setItem(key, JSON.stringify(stripBinaryData(state)))
      } catch {
        // localStorage full or unavailable — silently ignore
      }
    }, 300)
    return () => { if (saveTimer.current) clearTimeout(saveTimer.current) }
  }, [key, state])

  // Clear function
  const clear = useCallback(() => {
    localStorage.removeItem(key)
    setState(initialValue)
  }, [key, initialValue])

  return [state, setState, clear]
}
