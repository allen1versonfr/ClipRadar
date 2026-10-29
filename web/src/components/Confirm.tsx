import { createContext, useCallback, useContext, useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'

export interface Ask {
  title: string
  body: string
  ok?: string
  /** a destructive action: the button is red and Cancel has the focus, so Enter doesn't do it */
  danger?: boolean
}

const ConfirmContext = createContext<(a: Ask) => Promise<boolean>>(() => Promise.resolve(false))

/** "Are you sure?" before an action that can't be undone (the original page's ask()). */
export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState<Ask & { resolve: (v: boolean) => void }>()
  const confirm = useCallback((a: Ask) => new Promise<boolean>(resolve => setOpen({ ...a, resolve })), [])
  const close = (v: boolean) => {
    open?.resolve(v)
    setOpen(undefined)
  }
  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {open && <Dialog ask={open} onClose={close} />}
    </ConfirmContext.Provider>
  )
}

function Dialog({ ask, onClose }: { ask: Ask; onClose: (v: boolean) => void }) {
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null
    box.current?.querySelector<HTMLButtonElement>(ask.danger ? '[data-cancel]' : '[data-ok]')?.focus()
    return () => before?.focus()
  }, [ask.danger])
  function onKey(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      e.preventDefault()
      onClose(false)
    } else if (e.key === 'Tab') {           // keep the focus in the dialog
      const b = [...(box.current?.querySelectorAll('button') ?? [])]
      const i = b.indexOf(document.activeElement as HTMLButtonElement)
      e.preventDefault()
      b[(i + (e.shiftKey ? b.length - 1 : 1)) % b.length]?.focus()
    }
  }
  return (
    <div className="modal-back" onMouseDown={e => { if (e.target === e.currentTarget) onClose(false) }}>
      <div className="modal" role="alertdialog" aria-modal="true" aria-labelledby="ask-title" aria-describedby="ask-body"
           ref={box} onKeyDown={onKey}>
        <h2 id="ask-title">{ask.title}</h2>
        <p id="ask-body">{ask.body}</p>
        <div className="row modal-actions">
          <button className="btn" data-cancel data-testid="confirm-no" onClick={() => onClose(false)}>Cancel</button>
          <button className={`btn ${ask.danger ? 'danger' : 'primary'}`} data-ok data-testid="confirm-yes" onClick={() => onClose(true)}>{ask.ok ?? 'OK'}</button>
        </div>
      </div>
    </div>
  )
}

export const useConfirm = () => useContext(ConfirmContext)
