import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import type { Clip, Project, DeepStatus } from '../../api/schemas'
import { plural } from '../../format'
import { fmt } from './model'

// Review's tools for the whole stream (VOD only): the facts in the header, detection settings,
// deep scan, and the title A/B choice with the streamer's name. As in the original page's buildHead,
// openDetect, renderDeep and renderTitleOpts.

export interface Detect { preset: string; sensitivity: string; min_len?: number | undefined; max_len?: number | undefined }
export interface Comparison { count: number; same: number; added: number; dropped: number; top: Array<{ start: number; quality: number; reason: string }> }

/** "Clips start 2 s earlier, end 1 s later": what Clip Radar learned from your trims. */
export function trimNote(t: { n: number; start: number; end: number } | null | undefined): string {
  if (!t || t.n < 5 || (!t.start && !t.end)) return ''
  const part = (v: number, w: string) => (v ? `${w} ${Math.abs(v)} s ${v < 0 ? 'earlier' : 'later'}` : '')
  return `Clips ${[part(t.start, 'start'), part(t.end, 'end')].filter(Boolean).join(', ')}`
}

/** The VOD's facts: where it's from, how long, chat, how many strong moments, what ranking learned. */
export function HeadFacts({ meta, onDetect }: { meta: Project; onDetect: () => void }) {
  const p = meta, cs = p.chat_stats as { chatters: number; ignored?: number; raids?: number }, est = p.estimate, lr = p.learning, og = p.origin
  const hours = p.duration / 3600
  const len = hours >= 1 ? `${Math.round(hours * 10) / 10}-hour` : `${Math.max(1, Math.round(p.duration / 60))}-minute`
  const chatTip = cs.ignored ? `${cs.ignored.toLocaleString()} bot, command and sub messages ignored${cs.raids ? `, ${plural(cs.raids, 'raid')} played down` : ''}` : undefined
  const trim = trimNote(lr.trim)
  return (
    <span className="rv-facts-head muted small" data-testid="rv-facts">
      {og.platform && og.platform !== 'local' && <span title={og.url}>{[og.label, og.uploader, og.uploaded].filter(Boolean).join(' · ')}</span>}
      <span><b className="tnum">{fmt(p.duration)}</b> long</span>
      <span title={chatTip}>{!p.has_chat ? 'No chat, ranked by audio' : cs.chatters
        ? <><b className="tnum">{cs.chatters.toLocaleString()}</b> chatters</> : <><b className="tnum">{p.chat_count.toLocaleString()}</b> chat messages</>}</span>
      {est.strong != null && <span title={`Distinct moments that stand out clearly (${est.good ?? 0} worth a look in total)`} data-testid="rv-estimate">
        About <b className="tnum">{est.strong}</b> strong {est.strong === 1 ? 'moment' : 'moments'} in this {len} stream</span>}
      {lr.active && <span className="tuned" title={`Ranking learned from ${lr.decisions} of your keeps and skips${lr.using === 'all your picks' ? ' across all streamers' : ''}`}>Tuned to your picks</span>}
      {trim && <span className="tuned" title={`Learned from ${lr.trim?.n ?? 0} clips you trimmed before keeping. Settings › Clip ranking can forget it.`}>{trim}</span>}
      <button className="link" onClick={onDetect} title="Stream type, how many moments, clip lengths. Click to change or compare." data-testid="rv-detect-open">
        {p.detect_text || 'Detection settings'}</button>
    </span>
  )
}

/** Deep scan: the whole stream transcribed to find quiet funny lines chat and volume missed. */
export function DeepBox({ deep, gems, onStart, onStop }: { deep: DeepStatus | undefined; gems: number; onStart: () => void; onStop: () => void }) {
  const d = deep
  if (!d) return null
  if (d.state === 'running') return (
    <span className="rv-deep" data-testid="rv-deep">
      <span className="small">{d.step || 'Deep scan'}</span>
      <span className="meter" role="progressbar" aria-label="Deep scan" aria-valuenow={Math.round(d.progress * 100)} aria-valuemin={0} aria-valuemax={100}>
        <i style={{ width: `${Math.round(d.progress * 100)}%` }} /></span>
      <button className="link" onClick={onStop}>Stop</button>
    </span>
  )
  if (d.state === 'done') return <span className="rv-deep muted small" data-testid="rv-deep">{gems ? plural(gems, 'quiet gem') : 'No quiet gems'}</span>
  if (!d.can) return null
  return (
    <span className="rv-deep" data-testid="rv-deep">
      <button className="btn sm" onClick={onStart} title={d.state === 'error' && d.error ? d.error : 'Transcribe the whole stream to find quiet funny lines chat and volume missed'}>
        {d.state === 'error' ? 'Retry deep scan' : 'Deep scan'}</button>
    </span>
  )
}

/** Title A (from what was said) or B (with the streamer's name), and that name. */
export function TitleChoices({ clip: c, name, kept, onPick, onName }: {
  clip: Clip; name: string; kept: boolean; onPick: (t: string, which: 'A' | 'B') => void; onName: (name: string) => Promise<boolean>
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(name)
  if (!c.title_a) return null
  const chip = (k: 'A' | 'B', t: string, sub: string) => (
    <button key={k} className={`topt${c.title === t ? ' on' : ''}`} disabled={kept} aria-pressed={c.title === t} onClick={() => onPick(t, k)} title="Use this title">
      <b>{k}</b><span>{t}</span>{sub && <i>{sub}</i>}
    </button>
  )
  return (
    <div className="topts" data-testid="rv-title-opts">
      {chip('A', c.title_a, '')}
      {c.title_b && c.title_b !== c.title_a && chip('B', c.title_b, c.title_b_named ? '' : 'no streamer name yet')}
      {editing ? (
        <form className="row" onSubmit={async e => { e.preventDefault(); if (await onName(draft.trim())) setEditing(false) }}>
          <input className="input" autoFocus value={draft} maxLength={40} aria-label="The streamer's name as it should appear in titles"
                 placeholder="For example JasonTheWeen" onChange={e => setDraft(e.target.value)} data-testid="rv-streamer-input" />
          <button className="btn sm primary" type="submit">Save</button>
          <button className="btn sm ghost" type="button" onClick={() => { setDraft(name); setEditing(false) }}>Cancel</button>
        </form>
      ) : (
        <button className="link" onClick={() => { setDraft(name); setEditing(true) }} data-testid="rv-streamer">
          {name ? `Streamer: ${name} · change` : "Add the streamer's name for title B"}</button>
      )}
    </div>
  )
}

const SENS = [['fewer', 'Fewer, stronger'], ['normal', 'Normal'], ['more', 'More']] as const
const COUNTS = ['0', '5', '10', '15', '20', '25', '30', '40', '50', '60', '80', '100']
const LENGTHS = [['0', 'Auto (15 to 90 s)'], ['15', '15 s'], ['20', '20 s'], ['30', '30 s'], ['45', '45 s'], ['60', '60 s'], ['90', '90 s']] as const

/** Detection settings: stream type, how many moments, clip lengths, chat or audio. Try compares with the list
 *  you have; Apply re-ranks it, and clips you kept, skipped, trimmed or added stay. */
export function DetectDialog({ meta, count, length, cw, onTry, onApply, onClose }: {
  meta: Project; count: string; length: string; cw: number
  onTry: (d: Detect, cw: number, count: string, length: string) => Promise<Comparison | null>
  onApply: (d: Detect, cw: number, count: string, length: string) => void; onClose: () => void
}) {
  const d = meta.detect
  const [preset, setPreset] = useState(d.preset)
  const [sens, setSens] = useState(d.sensitivity)
  const [min, setMin] = useState(d.min_len ? String(d.min_len) : '')
  const [max, setMax] = useState(d.max_len ? String(d.max_len) : '')
  const [w, setW] = useState(cw)
  const [n, setN] = useState(count)
  const [len, setLen] = useState(length)
  const [cmp, setCmp] = useState<Comparison | null>(null)
  const [trying, setTrying] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  const hint = meta.presets.find(p => p.key === preset)?.hint ?? ''
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null
    box.current?.querySelector<HTMLElement>('select')?.focus()
    return () => before?.focus()
  }, [])
  const chosen = (): Detect => ({ preset, sensitivity: sens, min_len: Number(min) || undefined, max_len: Number(max) || undefined })
  const changed = <T,>(f: (v: T) => void) => (v: T) => { f(v); setCmp(null) }
  function onKey(e: KeyboardEvent) {
    if (e.key === 'Escape') { e.preventDefault(); onClose() }
    else if (e.key === 'Tab') {                         // keep the focus in the dialog
      const f = [...(box.current?.querySelectorAll<HTMLElement>('button,select,input') ?? [])]
      const i = f.indexOf(document.activeElement as HTMLElement)
      e.preventDefault()
      f[(i + (e.shiftKey ? f.length - 1 : 1)) % f.length]?.focus()
    }
  }
  return (
    <div className="modal-back" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="modal det" role="dialog" aria-modal="true" aria-labelledby="det-title" ref={box} onKeyDown={onKey} data-testid="rv-detect">
        <h2 id="det-title">Detection settings</h2>
        <p className="muted small">Now: {meta.detect_text}{meta.emotes_known ? ` · ${meta.emotes_known.toLocaleString()} 7TV/BTTV/FFZ emotes` : ''}</p>
        <div className="det-grid">
          <label className="fld">Stream type
            <select className="input" value={preset} data-testid="rv-det-preset" onChange={e => {
              const v = e.target.value, p = meta.presets.find(x => x.key === v)
              setPreset(v); setCmp(null)
              if (p) { setMin(String(p.min)); setMax(String(p.max)); setW(p.chat) }
            }}>
              {(meta.presets.length ? meta.presets : [{ key: 'any', label: 'Any stream' }]).map(p => <option key={p.key} value={p.key}>{p.label}</option>)}
            </select>
          </label>
          <label className="fld">How many moments
            <select className="input" value={sens} onChange={e => changed(setSens)(e.target.value)}>{SENS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
          </label>
          <label className="fld">Shortest clip, seconds
            <input className="input" type="number" min={3} max={179} value={min} onChange={e => changed(setMin)(e.target.value)} />
          </label>
          <label className="fld">Longest clip, seconds
            <input className="input" type="number" min={4} max={180} value={max} onChange={e => changed(setMax)(e.target.value)} />
          </label>
          <label className="fld">Clips to suggest
            <select className="input" value={n} data-testid="rv-det-count" onChange={e => changed(setN)(e.target.value)}>
              {(COUNTS.includes(n) ? COUNTS : [...COUNTS, n].sort((a, b) => Number(a) - Number(b))).map(v => <option key={v} value={v}>{v === '0' ? 'Automatic' : v}</option>)}
            </select>
          </label>
          <label className="fld">Clip length
            <select className="input" value={len} onChange={e => changed(setLen)(e.target.value)}>{LENGTHS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
          </label>
          <label className="fld span2">Rank by: audio {100 - w}%, chat {w}%
            <input type="range" min={0} max={100} step={5} value={w} aria-label="Chat versus audio" onChange={e => changed(setW)(Number(e.target.value))} />
          </label>
        </div>
        {hint && <p className="hint">{hint}</p>}
        {cmp && (
          <div className="det-cmp" data-testid="rv-det-cmp" aria-live="polite">
            With these settings: <b>{plural(cmp.count, 'clip')}</b>. {cmp.same} the same as now, <b>{cmp.added} new</b>, <b>{cmp.dropped} dropped</b>.
            {cmp.top.length > 0 && <ul>{cmp.top.map((t, i) => <li key={i}>{fmt(t.start)} · score {t.quality} · {t.reason}</li>)}</ul>}
          </div>
        )}
        <p className="fine muted small">Clips you kept, skipped, trimmed or added stay either way. Scores rank moments within this stream; they can't promise views.</p>
        <div className="row modal-actions">
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn" disabled={trying} data-testid="rv-det-try"
                  onClick={async () => { setTrying(true); try { setCmp(await onTry(chosen(), w, n, len)) } finally { setTrying(false) } }}>Try</button>
          <button className="btn primary" data-testid="rv-det-apply" onClick={() => onApply(chosen(), w, n, len)}>Apply</button>
        </div>
      </div>
    </div>
  )
}
