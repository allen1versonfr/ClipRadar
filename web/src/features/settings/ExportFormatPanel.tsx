import { useRef, useState } from 'react'
import { api } from '../../api/client'
import { ExportFormat } from '../../api/schemas'
import { Seg } from '../../components/Seg'
import { useToast } from '../../components/Toasts'
import { usePoll } from '../../hooks/usePoll'

type St = ExportFormat['settings']
type Pr = ExportFormat['prefs']
const QUALITY: Array<[string, string]> = [['q:16', 'Highest'], ['q:18', 'High'], ['q:20', 'Good'], ['q:23', 'Smaller file'],
  ['b:6000', '6 Mbps'], ['b:12000', '12 Mbps'], ['b:20000', '20 Mbps']]
const LOUD: Array<[number, string]> = [[-14, 'Even out (−14 LUFS, like TikTok)'], [-16, 'Even out, a bit quieter (−16)'],
  [-18, 'Quieter (−18)'], [-23, 'Broadcast (−23)'], [0, 'Leave as is (just stop clipping)']]

/** Codec and file type go together: VP9 is WebM, and leaving WebM goes back to H.264 (the current UI's rule). */
export function codecPatch(st: St, change: { vcodec?: string; container?: string }): Partial<St> {
  if (change.vcodec) return { vcodec: change.vcodec, container: change.vcodec === 'vp9' ? 'webm' : st.container === 'webm' ? 'mp4' : st.container }
  const c = change.container ?? st.container
  return { container: c, vcodec: c === 'webm' ? 'vp9' : st.vcodec === 'vp9' ? 'h264' : st.vcodec }
}
/** Put {token} at the cursor, with " - " before it when it would touch the text before. */
export function insertToken(value: string, pos: number, tok: string): string {
  const sep = value && pos > 0 && !/\s$/.test(value.slice(0, pos)) ? ' - ' : ''
  return value.slice(0, pos) + sep + `{${tok}}` + value.slice(pos)
}

/** Settings › Export format (the watermark is still in the current UI). */
export function ExportFormatPanel() {
  const toast = useToast()
  const poll = usePoll(() => api(ExportFormat, '/api/export'), 60_000)
  // a choice shows at once, and the server's answer takes over once it's saved
  const [pendPr, setPendPr] = useState<Partial<Pr>>({})
  const [pendSt, setPendSt] = useState<Partial<St>>({})
  const [tpl, setTpl] = useState<string>()
  const tplBox = useRef<HTMLInputElement>(null)
  const tplTimer = useRef<ReturnType<typeof setTimeout>>(undefined)
  const v = poll.data
  const st = v && { ...v.settings, ...pendSt }
  const pr = v && { ...v.prefs, ...pendPr }

  async function save(patch: object, shown: { pr?: Partial<Pr>; st?: Partial<St> } = {}) {
    if (shown.pr) setPendPr(p => ({ ...p, ...shown.pr }))
    if (shown.st) setPendSt(p => ({ ...p, ...shown.st }))
    try {
      await api(ExportFormat, '/api/export', patch)
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), 'err')
    }
    await poll.refresh()
    setPendPr({})
    setPendSt({})
  }
  const custom = (kv: Partial<St>) => void save({ custom: { ...(v?.prefs.custom ?? {}), ...kv } }, { st: kv })
  const setPref = (kv: Partial<Pr>) => void save(kv, { pr: kv })
  function template(value: string, now = false) {
    setTpl(value)
    clearTimeout(tplTimer.current)
    tplTimer.current = setTimeout(() => void save({ template: value || '{title}' }).then(() => setTpl(undefined)), now ? 0 : 400)
  }

  if (!v || !st || !pr) return <section className="panel set" data-testid="set-export"><header><h2>Export format</h2></header></section>
  const qual = st.vbitrate ? `b:${st.vbitrate}` : `q:${st.crf}`
  return (
    <section className="panel set" aria-labelledby="set-export" data-testid="set-export">
      <header><h2 id="set-export">Export format</h2></header>
      <p className="muted" data-testid="ex-describe">{v.describe}</p>
      <label className="chk" title="Once when you add a clip to the render queue, once when it's ready (also while Clip Radar is minimised)">
        <input type="checkbox" checked={pr.sounds} onChange={e => setPref({ sounds: e.target.checked })} />
        <span>Play a sound when a clip is queued and when it's ready</span>
      </label>
      <div className="fld">Made for
        <Seg label="Made for" value={pr.preset} options={v.presets.map(p => [p.key, p.label])} onChange={k => setPref({ preset: k })} />
        <span className="hint">{v.presets.find(p => p.key === pr.preset)?.note}</span>
      </div>
      <div className="fld">Quality
        <Seg label="Quality" value={pr.quality} options={v.quality.map(q => [q.key, q.label])} onChange={k => setPref({ quality: k })} />
        <span className="hint">{v.quality.find(q => q.key === pr.quality)?.note}</span>
      </div>
      <details className="adv">
        <summary>Advanced</summary>
        <div className="form-grid">
          <label>Resolution
            <select className="input" value={String(st.scale >= 0.99 ? 1 : st.scale)} onChange={e => custom({ scale: Number(e.target.value) })}>
              <option value="1">Full (1080 wide)</option><option value="0.6667">720</option><option value="0.5">540</option>
            </select>
          </label>
          <label>Frame rate
            <select className="input" value={String(st.fps)} title="Never higher than the stream's own: extra frames would only be copies"
                    onChange={e => custom({ fps: Number(e.target.value) })}>
              <option value="0">Same as the stream</option>{[24, 25, 30, 50, 60].map(n => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
          <label>Video codec
            <select className="input" value={st.vcodec} onChange={e => custom(codecPatch(st, { vcodec: e.target.value }))}>
              <option value="h264">H.264 (works everywhere)</option><option value="hevc">H.265 (smaller files)</option><option value="vp9">VP9 (.webm)</option>
            </select>
          </label>
          <label>Video quality
            <select className="input" value={QUALITY.some(([k]) => k === qual) ? qual : 'q:20'}
                    onChange={e => { const [k, n] = e.target.value.split(':'); custom(k === 'b' ? { vbitrate: Number(n) } : { crf: Number(n), vbitrate: 0 }) }}>
              {QUALITY.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
          </label>
          <label>Audio
            <select className="input" value={String(st.abitrate)} onChange={e => custom({ abitrate: Number(e.target.value) })}>
              {[96, 128, 160, 192, 256, 320].map(n => <option key={n} value={n}>{n} kbps</option>)}
            </select>
          </label>
          <label>File type
            <select className="input" value={st.container} onChange={e => custom(codecPatch(st, { container: e.target.value }))}>
              <option value="mp4">MP4</option><option value="mov">MOV</option><option value="webm">WebM</option>
            </select>
          </label>
        </div>
        {Object.keys(v.prefs.custom).length > 0 && <button className="link" onClick={() => void save({ custom: {} })}>Back to the preset's settings</button>}
      </details>
      <div className="fld">Captions
        <Seg label="Captions" value={pr.subtitles} onChange={k => setPref({ subtitles: k as Pr['subtitles'] })}
             options={[['burn', 'On the video'], ['burn_srt', 'On the video + .srt file'], ['srt', 'Only a .srt file']]} />
      </div>
      <div className="fld">Sound
        <div className="form-grid">
          <label>Loudness
            <select className="input" value={String(st.loudness || 0)} onChange={e => custom({ loudness: Number(e.target.value) })}>
              {LOUD.map(([n, l]) => <option key={n} value={n}>{l}</option>)}
            </select>
          </label>
          <label className="chk"><input type="checkbox" checked={pr.denoise} onChange={e => setPref({ denoise: e.target.checked })} />
            <span>Reduce background noise<small>Fan hum, hiss.</small></span></label>
          <label className="chk"><input type="checkbox" checked={pr.voice} onChange={e => setPref({ voice: e.target.checked })} />
            <span>Make voices clearer<small>Gently, without burying game sound.</small></span></label>
        </div>
      </div>
      <label className="fld">File names
        <input className="input" ref={tplBox} spellCheck={false} placeholder="{title}" value={tpl ?? pr.template} onChange={e => template(e.target.value)} />
      </label>
      <div className="row wrap" aria-label="Add to the file name">
        {v.tokens.map(t => (
          <button key={t} className="btn sm ghost" onClick={() => {
            const box = tplBox.current, cur = tpl ?? pr.template
            template(insertToken(cur, box?.selectionStart ?? cur.length, t), true)
          }}>{`{${t}}`}</button>
        ))}
      </div>
      <span className="hint">Example: <b data-testid="ex-preview">{v.preview_name}</b></span>
    </section>
  )
}
