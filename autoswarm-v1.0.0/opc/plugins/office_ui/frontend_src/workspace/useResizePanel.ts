import { useCallback, useEffect, useRef, useState } from 'react'

interface UseResizePanelOptions {
  initialWidth: number
  minWidth: number
  maxWidth: number
  onCollapse?: () => void
}

interface UseResizePanelReturn {
  width: number
  isResizing: boolean
  handleMouseDown: (e: React.MouseEvent) => void
}

export function useResizePanel({
  initialWidth,
  minWidth,
  maxWidth,
  onCollapse,
}: UseResizePanelOptions): UseResizePanelReturn {
  const [width, setWidth] = useState(initialWidth)
  const [isResizing, setIsResizing] = useState(false)
  const startXRef = useRef(0)
  const startWidthRef = useRef(0)
  const widthRef = useRef(initialWidth)

  // Keep ref in sync with state
  widthRef.current = width

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    startXRef.current = e.clientX
    startWidthRef.current = widthRef.current
    setIsResizing(true)
  }, [])

  useEffect(() => {
    if (!isResizing) return

    const handleMouseMove = (e: MouseEvent) => {
      // Panel is on the right, so dragging left increases width
      const delta = startXRef.current - e.clientX
      const next = startWidthRef.current + delta

      if (next < minWidth - 50) {
        onCollapse?.()
        setIsResizing(false)
        return
      }

      setWidth(Math.min(maxWidth, Math.max(minWidth, next)))
    }

    const handleMouseUp = () => {
      setIsResizing(false)
    }

    document.body.style.userSelect = 'none'
    document.body.style.cursor = 'col-resize'
    document.addEventListener('mousemove', handleMouseMove)
    document.addEventListener('mouseup', handleMouseUp)

    return () => {
      document.body.style.userSelect = ''
      document.body.style.cursor = ''
      document.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseup', handleMouseUp)
    }
  }, [isResizing, minWidth, maxWidth, onCollapse])

  return { width, isResizing, handleMouseDown }
}
