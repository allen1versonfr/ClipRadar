import type { LiveView } from '../../api/schemas'
import { plural } from '../../format'
import { LIVE_LABEL, LIVE_ON, liveStatus } from './live'
import { fmt } from './model'

/** A live session's header: its state, what's recorded, chat, moments, and the controls (lowest score, how many
 *  clips, Hands-free, Clip last 30s, Stop). */
export function LiveHead({ live: L, moments, onSettings, onClip, onStop }: {
  live: LiveView; moments: number
  onSettings: (body: Record<string, unknown>) => void; onClip: () => void; onStop: () => void
}) {
  const on = ['recording', 'reconnecting'].includes(L.status), active = LIVE_ON.includes(L.status)
  const minQ = [0, 40, 50, 60, 75].includes(L.min_quality) ? L.min_quality : 0
  return (
    <div className="rv-live" data-testid="rv-live">
      <div className="rv-live-row">
        <span className={`live-tag${on ? '' : ' off'}`} data-testid="rv-live-state"><i />{LIVE_LABEL[L.status] ?? L.status}</span>
        {L.in_ad && L.status === 'recording' && <span className="muted small">Ad break: left out</span>}
        <span className="rv-facts-head muted small">
          <span>Recorded <b className="tnum">{fmt(L.recorded)}</b></span>
          <span>Chat <b className="tnum">{L.chat_connected ? L.chat_per_min : '–'}</b>/min</span>
          <span><b className="tnum">{moments}</b> {moments === 1 ? 'moment' : 'moments'}</span>
          {L.ads_skipped > 0 && <span title="Ads Twitch put in the stream, kept out of the recording">Ads skipped <b className="tnum">{fmt(L.ads_skipped)}</b></span>}
        </span>
      </div>
      <div className="rv-live-row">
        <select className="input" aria-label="Lowest score a moment needs to be clipped" value={minQ}
                onChange={e => onSettings({ min_quality: Number(e.target.value) })}>
          <option value={0}>Any score</option>{[40, 50, 60, 75].map(v => <option key={v} value={v}>Score {v}+</option>)}
        </select>
        <select className="input" aria-label="How many clips to make" value={L.frequency} onChange={e => onSettings({ frequency: e.target.value })}>
          <option value="fewer">Fewer clips</option><option value="normal">Normal</option><option value="more">More clips</option><option value="lots">Lots of clips</option>
        </select>
        <label className="fld inline" title="Title, check and render every moment by itself">
          <input type="checkbox" checked={L.hands_free} onChange={e => onSettings({ hands_free: e.target.checked })} data-testid="rv-live-auto" />Hands-free
        </label>
        <span className="grow" />
        <button className="btn" disabled={L.status !== 'recording'} onClick={onClip} title="Clip the last 30 seconds (C)" data-testid="rv-live-clip">Clip last 30s</button>
        {active && <button className="btn" onClick={onStop} data-testid="rv-live-stop">Stop</button>}
      </div>
      <p className="muted small rv-live-status">
        {liveStatus(L).map((b, i) => <span key={i} className={b.tone === 'warn' ? 'warn-tx' : ''} title={b.title}>{b.text}</span>)}
        {L.status === 'error' && L.error && <span className="err-tx">{L.error}</span>}
        {!active && moments > 0 && <span>{plural(moments, 'moment')} stay here to review.</span>}
      </p>
    </div>
  )
}
