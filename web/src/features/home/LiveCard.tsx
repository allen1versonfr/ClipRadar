import { useState } from 'react'
import { api } from '../../api/client'
import { Ref, Twitch, type Status } from '../../api/schemas'
import { useToast } from '../../components/Toasts'
import { go, legacyViewUrl, liveUrl } from '../../handoff'
import { usePoll } from '../../hooks/usePoll'
import { getPref, renderOpts, setPref } from '../../prefs'
import { PRESETS } from './VodCard'

const MODES: Array<[string, string]> = [['hands', 'Hands-free'], ['review', 'Review each clip']]
const FREQS: Array<[string, string]> = [['fewer', 'Fewer'], ['normal', 'Normal'], ['more', 'More'], ['lots', 'Lots']]
const loadTwitch = () => api(Twitch, '/api/twitch')

function Seg({ label, value, options, onChange }: { label: string; value: string; options: Array<[string, string]>; onChange: (v: string) => void }) {
  return (
    <div className="seg" role="radiogroup" aria-label={label}>
      {options.map(([v, l]) => (
        <button key={v} role="radio" aria-checked={value === v} className={value === v ? 'on' : ''} onClick={() => onChange(v)}>{l}</button>
      ))}
    </div>
  )
}

export function LiveCard({ status }: { status: Status | undefined }) {
  const toast = useToast()
  const pref = <T,>(k: string, d: T) => getPref<T>(k, d)
  const [src, setSrc] = useState(() => pref('liveSrc', ''))
  const [mode, setMode] = useState(() => pref('liveMode', 'hands'))
  const [freq, setFreq] = useState(() => pref('liveFreq', 'normal'))
  const [preset, setPreset] = useState(() => pref('livePreset', 'any'))
  const [minQ, setMinQ] = useState(() => pref('liveMinQ', '0'))
  const [length, setLength] = useState(() => pref('liveLength2', '0'))
  const [wait, setWait] = useState(() => pref('liveWait', true))
  const [keep, setKeep] = useState(() => pref('liveKeep', false))
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const twitch = usePoll(loadTwitch, 60_000).data
  const save = <T,>(k: string, set: (v: T) => void) => (v: T) => { set(v); setPref(k, v) }

  async function start() {
    const source = src.trim()
    if (!source) return
    if (status?.ffmpeg === false) { toast('Download the missing tools first.', 'err'); return }
    setPref('liveSrc', source)
    setError('')
    setBusy(true)
    const opts = renderOpts()
    const delay = getPref('delay2', '')
    try {
      const v = await api(Ref, '/api/live/start', {
        source, length: Number(length), frequency: freq, events: getPref('events', true), hands_free: mode === 'hands',
        wait_for_live: wait, keep_watching: keep, opts, model: opts.whisper_model, language: opts.language,
        max_height: Number(getPref('maxh', '1080')), cookies: getPref('cookies', ''),
        chat_delay: delay === '' ? 'auto' : Number(delay), preset, emotes: getPref('useEmotes', true), min_quality: Number(minQ),
      })
      if (v.id) go(liveUrl(v.id))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
    setBusy(false)
  }

  const hk = status?.hotkey
  return (
    <section className="panel source" aria-labelledby="live-h">
      <header>
        <h2 id="live-h">Live stream</h2>
        <p className="muted">Clip a Twitch or YouTube stream while it's on air, so you can just watch. You can clip more than one at once.</p>
      </header>
      <input className="input" aria-label="Twitch channel or YouTube livestream" placeholder="twitch.tv/name, or a YouTube live link"
             spellCheck={false} autoComplete="off" value={src} onChange={e => setSrc(e.target.value)}
             onKeyDown={e => { if (e.key === 'Enter') void start() }} />
      <div className="fld">Mode
        <Seg label="Mode" value={mode} options={MODES} onChange={save('liveMode', setMode)} />
        <span className="hint">{mode === 'hands'
          ? 'Every moment is titled, checked and rendered with your style on its own. Finished clips appear in Exports.'
          : 'Moments wait in Review. You keep the ones you want.'}</span>
      </div>
      <div className="form-grid two">
        <div className="fld">How many clips<Seg label="How many clips" value={freq} options={FREQS} onChange={save('liveFreq', setFreq)} /></div>
        <label>Stream type
          <select className="input" value={preset} onChange={e => save('livePreset', setPreset)(e.target.value)}>
            {PRESETS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
        <label>Lowest score
          <select className="input" value={minQ} onChange={e => save('liveMinQ', setMinQ)(e.target.value)}>
            <option value="0">Any</option><option value="40">40</option><option value="50">50 (clear moments)</option>
            <option value="60">60</option><option value="75">75 (strong only)</option>
          </select>
        </label>
        <label>Clip length
          <select className="input" value={length} onChange={e => save('liveLength2', setLength)(e.target.value)}>
            <option value="0">Auto (15–90 s)</option><option value="20">20 s</option><option value="30">30 s</option>
            <option value="45">45 s</option><option value="60">60 s</option>
          </select>
        </label>
      </div>
      <label className="chk"><input type="checkbox" checked={wait} onChange={e => save('liveWait', setWait)(e.target.checked)} />
        <span>Wait for the stream to start<small>Start now even if the channel is offline. Recording begins when it goes live.</small></span></label>
      <label className="chk"><input type="checkbox" checked={keep} onChange={e => save('liveKeep', setKeep)(e.target.checked)} />
        <span>Keep watching after the stream ends<small>Waits for the channel's next stream and starts again.</small></span></label>
      <p className="fine">
        {twitch?.signed_in
          ? twitch.turbo ? <>Signed in to Twitch as <b>{twitch.login}</b> with Turbo: recordings come without ads.</>
            : <>Signed in to Twitch as <b>{twitch.login}</b>. Without Turbo or a sub, ad breaks are left out of the recording.</>
          : <>Ad breaks are left out of recordings. <a href={legacyViewUrl('settings')}>Sign in to Twitch</a> with Turbo for an ad-free stream.</>}
      </p>
      <p className="fine">{hk?.registered ? `Press ${hk.keys ?? ''} anywhere to clip the last 30 seconds. ` : ''}Only the last 15 minutes of the stream are kept on disk.</p>
      {error && <p className="err-tx" role="alert">{error}</p>}
      <footer><button className="btn primary" disabled={busy} onClick={() => void start()}>Start live clipping</button></footer>
    </section>
  )
}
