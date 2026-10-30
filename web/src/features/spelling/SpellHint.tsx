import { useEffect, useState } from 'react'
import { fixSpelling, loadFixes, type Fixes } from './spelling'

/** Under a title or post caption: the spelling it would fix ("dont → don't"), and Fix to make them. */
export function SpellHint({ text, onFix, disabled }: { text: string; onFix: (fixed: string) => void; disabled?: boolean }) {
  const [fixes, setFixes] = useState<Fixes>({})
  useEffect(() => { let on = true; void loadFixes().then(f => { if (on) setFixes(f) }); return () => { on = false } }, [])
  const [fixed, changes] = fixSpelling(text, fixes)
  if (disabled || !changes.length) return null
  const shown = changes.slice(0, 4).map(([a, b]) => `${a} → ${b}`).join(', ') + (changes.length > 4 ? `, and ${changes.length - 4} more` : '')
  return (
    <span className="spell row wrap" data-testid="spell-hint">
      <span className="hint">Spelling: {shown}</span>
      <button type="button" className="btn sm" onClick={() => onFix(fixed)}>Fix spelling</button>
    </span>
  )
}
