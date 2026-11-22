import { cloneElement, isValidElement, useId, type ReactElement, type ReactNode } from 'react'

/** A short explanation that comes up when the pointer rests on a setting, or it has the keyboard's focus (5.7.4).
 *  Wraps the control itself; the text is also read out with it (aria-describedby). No text: the control as it is. */
export function Tip({ text, children, wide }: { text?: ReactNode; children: ReactElement; wide?: boolean }) {
  const id = useId()
  if (!text || !isValidElement(children)) return children
  const child = cloneElement(children as ReactElement<{ 'aria-describedby'?: string }>, { 'aria-describedby': id })
  return (
    <span className={'tip' + (wide ? ' tip-wide' : '')}>
      {child}
      <span role="tooltip" id={id} className="tip-bubble">{text}</span>
    </span>
  )
}

/** A small "?" next to a setting's name, for a setting whose control can't carry the explanation itself. */
export function Help({ text, label }: { text: ReactNode; label: string }) {
  return (
    <Tip text={text} wide>
      <button type="button" className="help-dot" aria-label={`About ${label}`}>?</button>
    </Tip>
  )
}
