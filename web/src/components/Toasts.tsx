import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from 'react'

type Kind = '' | 'err'
/** A button on a toast: Undo, or one of a few answers (the original page's toast actions). */
export interface ToastAction { label: string; fn: () => void }
interface Toast { id: number; msg: string; kind: Kind; actions: ToastAction[] }

const ToastContext = createContext<(msg: string, kind?: Kind, actions?: ToastAction[]) => void>(() => {})

/** Short messages in the corner, at most three at a time (like the original page). */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const next = useRef(0)
  const close = useCallback((id: number) => setToasts(t => t.filter(x => x.id !== id)), [])
  const show = useCallback((msg: string, kind: Kind = '', actions: ToastAction[] = []) => {
    const id = ++next.current
    setToasts(t => [...t, { id, msg, kind, actions }].slice(-3))
    setTimeout(() => close(id), actions.length ? 8000 : kind === 'err' ? 7000 : 4500)     // time to reach a button
  }, [close])
  return (
    <ToastContext.Provider value={show}>
      {children}
      <div className="toasts" data-testid="toasts" role="status" aria-live="polite">
        {toasts.map(t => (
          <div key={t.id} className={`toast ${t.kind}`}>
            <span>{t.msg}</span>
            {t.actions.map(a => (
              <button key={a.label} className="link" data-testid="toast-action" onClick={() => { close(t.id); a.fn() }}>{a.label}</button>
            ))}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export const useToast = () => useContext(ToastContext)
