import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from 'react'

type Kind = '' | 'err'
interface Toast { id: number; msg: string; kind: Kind }

const ToastContext = createContext<(msg: string, kind?: Kind) => void>(() => {})

/** Short messages in the corner, at most three at a time (like the original page). */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const next = useRef(0)
  const show = useCallback((msg: string, kind: Kind = '') => {
    const id = ++next.current
    setToasts(t => [...t, { id, msg, kind }].slice(-3))
    setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), kind === 'err' ? 7000 : 4500)
  }, [])
  return (
    <ToastContext.Provider value={show}>
      {children}
      <div className="toasts" data-testid="toasts" role="status" aria-live="polite">
        {toasts.map(t => <div key={t.id} className={`toast ${t.kind}`}>{t.msg}</div>)}
      </div>
    </ToastContext.Provider>
  )
}

export const useToast = () => useContext(ToastContext)
