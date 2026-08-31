'use client'

import { useRef, useCallback, useEffect } from 'react'

interface ResizeHandleProps {
  direction: 'horizontal' | 'vertical'
  onResize: (delta: number) => void
  onResizeEnd?: () => void
  className?: string
}

export default function ResizeHandle({ direction, onResize, onResizeEnd, className = '' }: ResizeHandleProps) {
  const dragging = useRef(false)
  const lastPos = useRef(0)

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    dragging.current = true
    lastPos.current = direction === 'horizontal' ? e.clientX : e.clientY
    document.body.style.cursor = direction === 'horizontal' ? 'col-resize' : 'row-resize'
    document.body.style.userSelect = 'none'
  }, [direction])

  useEffect(() => {
    function handleMouseMove(e: MouseEvent) {
      if (!dragging.current) return
      const pos = direction === 'horizontal' ? e.clientX : e.clientY
      const delta = pos - lastPos.current
      lastPos.current = pos
      onResize(delta)
    }

    function handleMouseUp() {
      if (!dragging.current) return
      dragging.current = false
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      onResizeEnd?.()
    }

    document.addEventListener('mousemove', handleMouseMove)
    document.addEventListener('mouseup', handleMouseUp)
    return () => {
      document.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseup', handleMouseUp)
    }
  }, [direction, onResize, onResizeEnd])

  return (
    <div
      onMouseDown={handleMouseDown}
      className={`shrink-0 ${
        direction === 'horizontal'
          ? 'w-1 cursor-col-resize hover:bg-blue-400 active:bg-blue-500 group'
          : 'h-1 cursor-row-resize hover:bg-blue-400 active:bg-blue-500 group'
      } bg-transparent transition-colors ${className}`}
    >
      {/* Visual indicator on hover */}
      <div className={`${
        direction === 'horizontal'
          ? 'w-px h-full mx-auto bg-gray-200 group-hover:bg-blue-400 group-active:bg-blue-500'
          : 'h-px w-full my-auto bg-gray-200 group-hover:bg-blue-400 group-active:bg-blue-500'
      } transition-colors`} />
    </div>
  )
}
