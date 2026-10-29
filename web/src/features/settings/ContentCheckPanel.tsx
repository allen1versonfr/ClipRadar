import { useState, type ChangeEvent } from 'react'
import { api } from '../../api/client'
import { Safety } from '../../api/schemas'
import { Seg } from '../../components/Seg'
import { useToast } from '../../components/Toasts'
import { usePoll } from '../../hooks/usePoll'

const SEV: Record<number, string> = { 3: 'Serious', 2: 'Medium', 1: 'Mild' }
const SENS: Array<[string, string]> = [['all', 'Everything'], ['skip_mild', 'Skip mild'], ['serious', 'Only serious']]
const SENS_HINT: Record<string, string> = {
  all: 'Flags everything it knows, swearing included.',
  skip_mild: 'Leaves out swearing and gambling talk.',
  serious: 'Only slurs, self-harm or harassment, personal info and your own words.',
}
const CENSOR: Array<[string, string]> = [['off', 'Just flag it'], ['mask', 'Hide in captions'], ['mute', 'Mute it'], ['bleep', 'Bleep it']]
const CENSOR_HINT: Record<string, string> = {
  off: 'Clips are flagged in Review and Exports. Nothing is changed.',
  mask: 'Flagged words show as f*** in captions and titles. The audio is left alone.',
  mute: 'Flagged words are hidden in captions and silenced in the audio.',
  bleep: 'Flagged words are hidden in captions and bleeped in the audio.',
}

/** Settings › Content check: the same settings, and the same server, as the current UI's. */
export function ContentCheckPanel() {
  const toast = useToast()
  const poll = usePoll(() => api(Safety, '/api/safety'), 60_000)
  // a choice shows at once, and the server's answer takes over once it's saved
  const [pending, setPending] = useState<Partial<Safety>>({})
  const [custom, setCustom] = useState<string>()
  const c = poll.data && { ...poll.data, ...pending }

  async function save(patch: Partial<Safety> | { custom: string }) {
    setPending(p => ({ ...p, ...(patch as Partial<Safety>) }))
    try {
      await api(Safety, '/api/safety', patch)
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), 'err')
    }
    await poll.refresh()
    setPending({})
  }
  const toggle = (k: keyof Safety) => (e: ChangeEvent<HTMLInputElement>) => void save({ [k]: e.target.checked })

  return (
    <section className="panel set" aria-labelledby="set-safety" data-testid="set-safety">
      <header className="row">
        <h2 id="set-safety" className="grow">Content check</h2>
        <label className="chk"><input type="checkbox" checked={c?.enabled ?? true} disabled={!c} onChange={toggle('enabled')} /><span>Check clips</span></label>
      </header>
      <p className="muted">Checks what each clip says, its title and its post caption for things that get TikTok videos taken down or held back, before anything is posted.</p>
      {c?.enabled && (
        <>
          <table className="table compact static sf-table" data-testid="sf-table">
            <thead><tr><th>Look for</th><th className="c">Check</th><th className="c">Censor</th></tr></thead>
            <tbody>
              {c.category_info.map(k => {
                const on = !!c.categories[k.key]
                return (
                  <tr key={k.key}>
                    <td>{k.label}<span className="muted small"> · {SEV[k.severity] ?? ''}</span></td>
                    <td className="c"><label className="cell-chk"><input type="checkbox" aria-label={`Check ${k.label}`} checked={on}
                      onChange={e => void save({ categories: { ...c.categories, [k.key]: e.target.checked } })} /></label></td>
                    <td className="c"><label className="cell-chk"><input type="checkbox" aria-label={`Censor ${k.label}`} disabled={!on} checked={c.censor_categories.includes(k.key)}
                      onChange={e => void save({ censor_categories: e.target.checked ? [...new Set([...c.censor_categories, k.key])]
                        : c.censor_categories.filter(x => x !== k.key) })} /></label></td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          <div className="fld">Sensitivity
            <Seg label="Sensitivity" value={c.sensitivity} options={SENS} onChange={v => void save({ sensitivity: v as Safety['sensitivity'] })} />
            <span className="hint">{SENS_HINT[c.sensitivity]}</span>
          </div>
          <div className="fld">When something is flagged
            <Seg label="When something is flagged" value={c.censor} options={CENSOR} onChange={v => void save({ censor: v as Safety['censor'] })} />
            <span className="hint">{CENSOR_HINT[c.censor]}</span>
          </div>
          <label className="chk"><input type="checkbox" checked={c.hold} onChange={toggle('hold')} /><span>Hands-free: hold back clips with serious flags</span></label>
          <h3 className="sub">Reviewing flags</h3>
          <label className="chk"><input type="checkbox" checked={c.auto} onChange={toggle('auto')} /><span>Check clips by themselves</span></label>
          <div className="fld">Show flags
            <Seg label="Show flags" value={c.notify} options={[['now', 'Right away'], ['later', 'Collect in the Flagged tab']]}
                 onChange={v => void save({ notify: v as Safety['notify'] })} />
          </div>
          <div className="fld">Rendering a clip with flags you haven't reviewed
            <Seg label="Rendering a clip with flags you haven't reviewed" value={c.export_flagged}
                 options={[['ask', 'Ask me first'], ['block', 'Not until reviewed'], ['allow', 'Just render it']]}
                 onChange={v => void save({ export_flagged: v as Safety['export_flagged'] })} />
          </div>
          <label className="chk"><input type="checkbox" checked={c.remember_dismissed} onChange={toggle('remember_dismissed')} /><span>Remember flags I dismiss</span></label>
          <label className="chk"><input type="checkbox" checked={c.auto_recheck} onChange={toggle('auto_recheck')} /><span>Check a clip again after I edit or fix it</span></label>
          <label className="fld">Your own blocked words
            <textarea className="input" rows={2} placeholder="Your real name, your town, a friend's gamertag… separated by commas"
                      value={custom ?? c.custom.join(', ')} onChange={e => setCustom(e.target.value)}
                      onBlur={() => { if (custom !== undefined && custom !== c.custom.join(', ')) void save({ custom }).then(() => setCustom(undefined)); else setCustom(undefined) }} />
            <span className="hint">Always treated as serious. Use it to keep personal details out of clips.</span>
          </label>
        </>
      )}
      <p className="fine">The check reads words only. It can't see what's on screen or recognise music, so it's a safety net, not a guarantee.</p>
    </section>
  )
}
