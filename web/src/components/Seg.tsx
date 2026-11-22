import { Tip } from './Tip'

/** A row of buttons where one is chosen (the original page's .seg), announced as a radio group.
 *  An option's third entry, when there is one, is what it does: shown when the pointer rests on it (5.7.4). */
export function Seg({ label, value, options, onChange }: { label: string; value: string; options: Array<[string, string] | [string, string, string]>; onChange: (v: string) => void }) {
  return (
    <div className="seg" role="radiogroup" aria-label={label}>
      {options.map(([v, l, hint]) => (
        <Tip key={v} text={hint}>
          <button role="radio" aria-checked={value === v} className={value === v ? 'on' : ''} onClick={() => onChange(v)}>{l}</button>
        </Tip>
      ))}
    </div>
  )
}
