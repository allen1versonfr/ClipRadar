import { useState } from 'react'
import { api } from '../../api/client'
import { Explain, Learning, type Trim } from '../../api/schemas'
import { useConfirm } from '../../components/Confirm'
import { useToast } from '../../components/Toasts'
import { plural } from '../../format'
import { usePoll } from '../../hooks/usePoll'

const errText = (e: unknown) => e instanceof Error ? e.message : String(e)

/** "clips start 1.5 s earlier, end 2 s later (from your trims)", once 5 trims are recorded. */
export function trimNote(t: Trim | null): string {
  if (!t || t.n < 5 || (!t.start && !t.end)) return ''
  const part = (v: number, w: string) => v ? `${w} ${Math.abs(v)} s ${v < 0 ? 'earlier' : 'later'}` : ''
  return `clips ${[part(t.start, 'start'), part(t.end, 'end')].filter(Boolean).join(', ')} (from your trims)`
}

/** The facts stored for one streamer, as the original page lists them. */
export function explainFacts(x: Explain): string[] {
  const f = x.facts
  return [
    `${plural(f.decisions, 'decision')} stored: ${f.kept} kept${f.posted ? ` (${f.posted} posted)` : ''}, ${f.skipped} skipped`,
    f.added ? `${plural(f.added, 'clip')} you made by hand (moments the finder missed)` : '',
    f.reasons.length ? 'Skipped for: ' + f.reasons.map(r => `${r.label.toLowerCase()} (${r.n})`).join(', ') : '',
    trimNote(f.trim) || (f.trim?.n ? `${plural(f.trim.n, 'trim')} recorded (start and end shift after 5)` : ''),
  ].filter(Boolean)
}

/** Settings › Clip ranking: learning from keeps and skips, per streamer, with what it learned. */
export function LearningPanel() {
  const toast = useToast()
  const confirm = useConfirm()
  const poll = usePoll(() => api(Learning, '/api/learning'), 60_000)
  const [onNow, setOnNow] = useState<boolean>()
  const [open, setOpen] = useState('')
  const [explain, setExplain] = useState<Explain>()
  const L = poll.data
  const on = onNow ?? L?.enabled ?? true

  async function post(path: '/api/learning' | '/api/learning/forget', body: unknown, done?: () => void) {
    try {
      await api(Learning, path, body)
      done?.()
    } catch (e) {
      toast(errText(e), 'err')
    }
    await poll.refresh()
  }
  async function toggle(streamer: string) {
    if (open === streamer) return setOpen('')
    try {
      setExplain(await api(Explain, `/api/learning/explain?streamer=${encodeURIComponent(streamer)}`))
      setOpen(streamer)
    } catch (e) {
      toast(errText(e), 'err')
    }
  }
  async function forgetAll() {
    if (!await confirm({ title: 'Forget everything learned?', body: 'Clip ranking goes back to the default for every streamer.', ok: 'Forget', danger: true })) return
    await post('/api/learning/forget', { streamer: '' }, () => setOpen(''))
  }

  const rows = L?.profiles ?? []
  return (
    <section className="panel set" aria-labelledby="set-learn" data-testid="set-learn">
      <header className="row">
        <h2 id="set-learn" className="grow">Clip ranking</h2>
        <label className="chk"><input type="checkbox" checked={on} disabled={!L} onChange={e => {
          const enabled = e.target.checked
          setOnNow(enabled)
          void post('/api/learning', { enabled }).finally(() => setOnNow(undefined))
        }} /><span>Learn from my picks</span></label>
      </header>
      <p className="muted">Clip Radar learns from the clips you keep, skip and post, separately for each streamer, and moves moments like the ones you pick up the list. It starts after about {L?.min_decisions ?? 8} decisions. <b>What it learned</b> shows exactly what's stored and what it changes.</p>
      {on && (
        <>
          {!L ? null : !rows.length ? <p className="muted">Nothing learned yet. Keep and skip a few suggested clips.</p> : (
            <ul className="lr-list" data-testid="lr-list">
              {rows.map(r => {
                const sub = [r.added ? `${plural(r.added, 'clip')} you added by hand (moments it missed)` : '',
                  r.reasons.length ? 'skipped for: ' + r.reasons.map(x => `${x.label.toLowerCase()} ${x.n}`).join(', ') : '',
                  trimNote(r.trim)].filter(Boolean).join(' · ')
                const id = `lr-${r.streamer.replace(/\W/g, '_')}`
                return (
                  <li key={r.streamer}>
                    <div className="lr-row">
                      <div className="lr-main">
                        <b>{r.streamer}</b>
                        <span className="muted small">
                          {plural(r.decisions, 'decision')}, {r.kept} kept{r.accept != null ? ` (${r.accept}% of suggestions)` : ''}
                          {r.posted ? `, ${r.posted} posted` : ''}{r.decisions < (L?.min_decisions ?? 8) ? ` · starts after ${L?.min_decisions ?? 8}` : ''}
                        </span>
                        {sub && <span className="hint">{sub}</span>}
                      </div>
                      <span className="row wrap">
                        <button className="btn sm" aria-expanded={open === r.streamer} aria-controls={id} onClick={() => void toggle(r.streamer)}>What it learned</button>
                        <button className="btn sm" onClick={() => void post('/api/learning/forget', { streamer: r.streamer }, () => {
                          if (open === r.streamer) setOpen('')
                          toast(`Forgot what Clip Radar learned for ${r.streamer}.`)
                        })} aria-label={`Forget what it learned for ${r.streamer}`}>Forget</button>
                      </span>
                    </div>
                    {open === r.streamer && explain && <Explanation id={id} x={explain} />}
                  </li>
                )
              })}
            </ul>
          )}
          {rows.length > 0 && (
            <div className="row wrap">
              <button className="btn sm danger" onClick={() => void forgetAll()}>Forget everything</button>
              {L?.total ? <span className="muted small">{plural(L.total, 'decision')} in total</span> : null}
            </div>
          )}
        </>
      )}
    </section>
  )
}

function Explanation({ id, x }: { id: string; x: Explain }) {
  const cmp = x.compare.filter(c => c.says !== 'about the same')
  return (
    <div className="explain" id={id} data-testid="lr-explain">
      <p className={`ex-status ${x.status}`}>{x.status_text}</p>
      <h4>What's stored <span className="tag">facts</span></h4>
      <ul>{explainFacts(x).map(t => <li key={t}>{t}</li>)}</ul>
      {x.compare.length > 0 && (
        <>
          <h4>How kept and skipped clips differed <span className="tag">facts</span></h4>
          {cmp.length ? <ul>{cmp.map(c => <li key={c.label}>{c.label}: {c.says} ({c.kept} kept vs {c.skipped} skipped)</li>)}</ul>
            : <p className="muted small">No clear difference yet.</p>}
        </>
      )}
      {x.learned.length > 0 && (
        <>
          <h4>What ranking does with it <span className="tag">learned</span></h4>
          <ul>{x.learned.map(l => <li key={l.label}>{l.label} moves clips {l.direction === 'up' ? 'up' : 'down'} {l.strength}</li>)}</ul>
        </>
      )}
      <h4>Rules every clip is scored on <span className="tag">built in</span></h4>
      <ul>{x.rules.map(r => <li key={r}>{r}</li>)}</ul>
      {x.method && <p className="fine">{x.method}</p>}
    </div>
  )
}
