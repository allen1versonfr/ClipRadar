import { useCallback, useEffect, useRef, useState } from 'react'

export interface Poll<T> {
  data: T | undefined
  error: string
  /** fetch now (after an action), without waiting for the next tick */
  refresh: () => Promise<void>
}

/**
 * Ask the server every `ms` while the window is visible, and right away when it becomes visible
 * again: the original page's pollers, as one hook. The server owns the state; the screen is rebuilt
 * from each answer, so a reload or a reconnect never duplicates anything.
 * Answers that arrive out of order are dropped (an older request can't overwrite a newer one).
 */
export function usePoll<T>(load: () => Promise<T>, ms: number, enabled = true): Poll<T> {
  const [data, setData] = useState<T>()
  const [error, setError] = useState('')
  const seq = useRef(0)
  const loadRef = useRef(load)
  loadRef.current = load

  const refresh = useCallback(async () => {
    const mine = ++seq.current
    try {
      const d = await loadRef.current()
      if (mine === seq.current) {
        setData(d)
        setError('')
      }
    } catch (e) {
      if (mine === seq.current) setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  useEffect(() => {
    if (!enabled) return
    void refresh()
    const tick = setInterval(() => {
      if (!document.hidden) void refresh()
    }, ms)
    const onVisible = () => {
      if (!document.hidden) void refresh()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      clearInterval(tick)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [ms, enabled, refresh])

  return { data, error, refresh }
}
