import { useState } from 'react'
import { api } from '../../api/client'
import { type Clip, SeriesClip, type SeriesView } from '../../api/schemas'

/** Review › Clip: the series a clip is in, or one it looks like part of, or none. Out of the way when you don't use
 *  series: a suggestion shows only when Clip Radar has one (Settings › Series › Suggest series), and otherwise it's
 *  one small "Series" menu. */
export function SeriesBox({ clip: c, kept, view, onChanged, onError }: {
  clip: Clip; kept: boolean; view: SeriesView | undefined
  onChanged: (clip: Partial<Clip>, view: SeriesView) => void; onError: (msg: string) => void
}) {
  const [naming, setNaming] = useState<string>()             // the name typed for a new series
  const [hidden, setHidden] = useState(false)                // "Not now" on a suggestion
  const [busy, setBusy] = useState(false)
  async function act(action: 'add' | 'new' | 'remove', id = '', name = '') {
    setBusy(true)
    try {
      const r = await api(SeriesClip, '/api/series/clip', { pid: c.pid, clip: c.id, action, id, name })
      const d = r.clip as Partial<Clip>
      onChanged({ series: d.series ?? null, series_hint: d.series_hint ?? null }, r.series)
      setNaming(undefined)
    } catch (e) { onError(e instanceof Error ? e.message : String(e)) }
    setBusy(false)
  }
  const list = view?.list ?? []
  const hint = c.series_hint
  if (c.series) {
    return (
      <div className="series-box row wrap" data-testid="rv-series">
        <span>Part of <b>{c.series.name}</b> · Pt. {c.series.part}<span className="muted small"> · added to the title and caption when it renders</span></span>
        {!kept && <button className="btn sm ghost" disabled={busy} onClick={() => void act('remove')}>Take out</button>}
      </div>
    )
  }
  if (kept) return null
  if (naming !== undefined) {
    return (
      <form className="series-box row wrap" data-testid="rv-series" onSubmit={e => { e.preventDefault(); void act('new', '', naming) }}>
        <input className="input grow" autoFocus maxLength={60} aria-label="New series name" value={naming} onChange={e => setNaming(e.target.value)} />
        <button className="btn sm primary" disabled={busy}>Start series</button>
        <button type="button" className="btn sm ghost" onClick={() => setNaming(undefined)}>Cancel</button>
      </form>
    )
  }
  return (
    <div className="series-box row wrap" data-testid="rv-series">
      {hint && !hidden && (
        <span className="series-hint" data-testid="rv-series-hint">
          {hint.new ? <>Start a series: <b>{hint.name}</b>?</> : <>Part of <b>{hint.name}</b>?</>}
          <span className="muted small"> {hint.why}.</span>
          <button className="btn sm" disabled={busy} data-testid="rv-series-accept"
                  onClick={() => (hint.new ? setNaming(hint.name) : void act('add', hint.id))}>
            {hint.new ? 'Start it' : `Add as Pt. ${hint.part}`}
          </button>
          <button className="btn sm ghost" onClick={() => setHidden(true)}>Not now</button>
        </span>
      )}
      <select className="input series-pick" aria-label="Series" value="" data-testid="rv-series-pick" disabled={busy}
              onChange={e => { const v = e.target.value; if (v === '+') setNaming(hint?.name ?? ''); else if (v) void act('add', v) }}>
        <option value="">Series: none</option>
        {list.map(x => <option key={x.id} value={x.id}>{x.name} (next: Pt. {x.next})</option>)}
        <option value="+">New series…</option>
      </select>
    </div>
  )
}
