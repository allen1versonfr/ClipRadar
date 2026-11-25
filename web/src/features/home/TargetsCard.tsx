import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import { type Target, Targets, type TargetSettings } from '../../api/schemas'
import { RecBadge } from '../../components/RecBadge'
import { Seg } from '../../components/Seg'
import { useToast } from '../../components/Toasts'
import { liveUrl } from '../../handoff'
import { usePoll } from '../../hooks/usePoll'
import { getPref, renderOpts } from '../../prefs'
import { FREQS, MODES } from './LiveCard'
import { PRESETS } from './VodCard'

/** What Live clipping sends besides a target's own settings: your render style, captions model and the rest. Sent to
 *  every target each time Home opens, so a target clips with the style you use now. */
function liveParams() {
  const o = renderOpts()
  const delay = getPref('delay2', '')
  return { opts: o, model: o.whisper_model, language: o.language, max_height: Number(getPref('maxh', '1080')),
    cookies: getPref('cookies', ''), chat_delay: delay === '' ? 'auto' : Number(delay), emotes: getPref('useEmotes', true),
    events: getPref('events', true) }
}

/** A new target starts with the settings Live stream is on. */
function liveDefaults(): TargetSettings {
  return { mode: getPref<string>('liveMode', 'hands') === 'review' ? 'review' : 'hands', frequency: getPref('liveFreq', 'normal'),
    preset: getPref('livePreset', 'any'), min_quality: Number(getPref('liveMinQ', '0')), length: Number(getPref('liveLength2', '0')),
    save_vod: getPref('liveSaveVod', true), prep_songs: getPref('livePrepSongs', true) }
}

const load = () => api(Targets, '/api/targets')

/** Home › Targets (5.5): streamers Clip Radar starts clipping on its own as soon as they go live. */
export function TargetsCard() {
  const toast = useToast()
  const { data, refresh } = usePoll(useCallback(load, []), 10_000)
  const [src, setSrc] = useState('')
  const [error, setError] = useState('')
  const [open, setOpen] = useState('')
  const list = data?.targets ?? []
  // a target clips with the render style and captions you use now
  useEffect(() => { void api(Targets, '/api/targets/update', { id: '*', params: liveParams() }).catch(() => {}) }, [])

  async function add() {
    const source = src.trim()
    if (!source) return
    setError('')
    try {
      await api(Targets, '/api/targets/add', { source, settings: liveDefaults(), params: liveParams() })
      setSrc('')
      await refresh()
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
  }
  async function change(t: Target, patch: Record<string, unknown>) {
    try { await api(Targets, '/api/targets/update', { id: t.id, ...patch }); await refresh() } catch (e) { toast(e instanceof Error ? e.message : String(e), 'err') }
  }
  async function remove(t: Target) {
    try { await api(Targets, '/api/targets/remove', { id: t.id }); await refresh() } catch (e) { toast(e instanceof Error ? e.message : String(e), 'err') }
  }

  return (
    <section className="panel source targets" aria-labelledby="targets-h" data-testid="targets">
      <header>
        <h2 id="targets-h">Targets</h2>
        <p className="muted">Streamers Clip Radar watches for you. As soon as one goes live it starts clipping them on its own, with
          their settings below, and starts again on their next stream. Clip Radar has to be open.</p>
      </header>
      <div className="row">
        <input className="input grow" aria-label="Streamer to add: a Twitch channel or a YouTube channel" data-testid="target-input"
               placeholder="twitch.tv/name, or youtube.com/@name" spellCheck={false} autoComplete="off" value={src}
               onChange={e => setSrc(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') void add() }} />
        <button className="btn" onClick={() => void add()} disabled={!src.trim()} data-testid="target-add">Add</button>
      </div>
      {error && <p className="err-tx" role="alert">{error}</p>}
      {list.length === 0
        ? <p className="muted small" data-testid="targets-empty">No targets yet. Add a streamer and they're clipped every time they go live.</p>
        : <ul className="targets-list" aria-label="Targets">
          {list.map(t => (
            <li key={t.id} className={`target${t.on ? '' : ' off'}`} data-testid="target">
              <div className="target-row">
                <span className={`dot ${!t.on ? 'off' : t.live ? 'live' : 'idle'}`} aria-hidden />
                <b className="ellipsis" title={t.source}>{t.name || t.channel}</b>
                <span className="muted small">{t.platform === 'youtube' ? 'YouTube' : 'Twitch'}</span>
                <span className="grow small target-note" data-testid="target-note">{t.note}</span>
                {t.session && <RecBadge vod={t.vod} active={t.recording} compact />}
                {t.session && <a className="btn sm" href={liveUrl(t.session)}>Open</a>}
                <label className="chk inline" title={t.on ? 'Watching: clipped when they go live' : 'Not watched'}>
                  <input type="checkbox" checked={t.on} aria-label={`Watch ${t.name || t.channel}`} data-testid="target-on"
                         onChange={e => void change(t, { on: e.target.checked })} /><span className="small">Watch</span></label>
                <button className="btn sm ghost" aria-expanded={open === t.id} onClick={() => setOpen(open === t.id ? '' : t.id)}
                        data-testid="target-edit">Settings</button>
                <button className="btn sm ghost" aria-label={`Remove ${t.name || t.channel}`} onClick={() => void remove(t)}>Remove</button>
              </div>
              {open === t.id && <TargetSettingsForm s={t.settings} name={t.name || t.channel} onChange={p => void change(t, { settings: p })} />}
            </li>
          ))}
        </ul>}
    </section>
  )
}

function TargetSettingsForm({ s, name, onChange }: { s: TargetSettings; name: string; onChange: (p: Partial<TargetSettings>) => void }) {
  return (
    <div className="target-set" data-testid="target-settings">
      <div className="fld">Mode
        <Seg label={`Mode for ${name}`} value={s.mode} options={MODES} onChange={v => onChange({ mode: v as TargetSettings['mode'] })} />
        <span className="hint">{s.mode === 'hands'
          ? 'Every moment is titled, checked and rendered with your style on its own. Finished clips appear in Exports.'
          : 'Moments wait for you in Moments. You keep the ones you want.'}</span>
      </div>
      <div className="form-grid two">
        <div className="fld">How many clips<Seg label={`How many clips for ${name}`} value={s.frequency} options={FREQS} onChange={v => onChange({ frequency: v })} /></div>
        <label>Stream type
          <select className="input" value={s.preset} onChange={e => onChange({ preset: e.target.value })}>
            {PRESETS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
        <label>Lowest score
          <select className="input" value={String(s.min_quality)} onChange={e => onChange({ min_quality: Number(e.target.value) })}>
            <option value="0">Any</option><option value="40">40</option><option value="50">50 (clear moments)</option>
            <option value="60">60</option><option value="75">75 (strong only)</option>
          </select>
        </label>
        <label>Clip length
          <select className="input" value={String(s.length)} onChange={e => onChange({ length: Number(e.target.value) })}>
            <option value="0">Auto (15–120 s)</option><option value="20">20 s</option><option value="30">30 s</option>
            <option value="45">45 s</option><option value="60">60 s</option><option value="90">90 s</option><option value="120">2 min</option>
          </select>
        </label>
      </div>
      <label className="chk"><input type="checkbox" checked={s.save_vod} onChange={e => onChange({ save_vod: e.target.checked })} data-testid="target-vod" />
        <span>Save the whole stream<small>Keeps the full recording and saves it as one video when the stream ends, ready in Export VOD. About 3 GB an hour at 1080p.</small></span></label>
      <label className="chk"><input type="checkbox" checked={s.save_vod && s.prep_songs} disabled={!s.save_vod}
                                    onChange={e => onChange({ prep_songs: e.target.checked })} data-testid="target-songs" />
        <span>Recognise songs while recording<small>{s.save_vod ? 'Each song that plays is recognised in the background as the stream goes on, so the saved stream opens in Export VOD with its music found and its songs named, ready to erase. Sends a few 12-second samples of each song to Shazam.'
          : 'Needs Save the whole stream.'}</small></span></label>
    </div>
  )
}
