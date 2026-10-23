import { useEffect, useState } from 'react'

/** The time in seconds, updated every `ms` while `on` (for "Checking the file · 0:12"). */
export function useNow(ms: number, on: boolean): number {
  const [now, setNow] = useState(() => Date.now() / 1000)
  useEffect(() => {
    if (!on) return
    const t = setInterval(() => setNow(Date.now() / 1000), ms)
    return () => clearInterval(t)
  }, [ms, on])
  return now
}
