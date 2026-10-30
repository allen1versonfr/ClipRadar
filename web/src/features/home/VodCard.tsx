import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import { JobStarted, Ok, Picked, Ref, type Status } from '../../api/schemas'
import { useToast } from '../../components/Toasts'
import { go, reviewUrl } from '../../handoff'
import { useJob } from '../../hooks/useJob'
import { getPref, renderOpts, setPref } from '../../prefs'
import { fmtLeft } from '../exports/renderStatus'

const COUNTS = ['0', '5', '10', '15', '20', '25', '30', '35', '40', '45', '50', '55', '60', '70', '80', '90', '100']
const LENGTHS: Array<[string, string]> = [['0', 'Auto (15–90 s)'], ['15', '15 s'], ['20', '20 s'], ['30', '30 s'], ['45', '45 s'], ['60', '60 s'], ['90', '90 s']]
export const PRESETS: Array<[string, string]> = [['any', 'Any stream'], ['gaming', 'Gaming'], ['irl', 'IRL / Just Chatting'],
  ['reactions', 'Reactions'], ['interviews', 'Interviews'], ['podcasts', 'Podcasts']]
// a stream type moves "Rank by" to where that kind of stream's moments show best (as in the original page)
const PRESET_CHAT: Record<string, number> = { any: 60, gaming: 55, irl: 65, reactions: 50, interviews: 50, podcasts: 45 }

/** A setting kept under the original page's key, so both UIs start from the same choice. */
function usePref<T>(key: string, fallback: T): [T, (v: T) => void] {
  const [v, setV] = useState<T>(() => getPref(key, fallback))
  return [v, (nv: T) => { setV(nv); setPref(key, nv) }]
}

const optNum = (s: string) => (s === '' ? undefined : Number(s))

export function VodCard({ status, source, onSource, start = 0 }: { status: Status | undefined; source: string; onSource: (s: string) => void; start?: number }) {
  const toast = useToast()
  const [count, setCount] = usePref('count2', '0')
  const [length, setLength] = usePref('length2', '0')
  const [cw, setCw] = usePref<number | string>('cw', 60)
  const [preset, setPreset] = usePref('preset', 'any')
  const [sens, setSens] = usePref('sens', 'normal')
  const [deep, setDeep] = usePref('deep', false)
  const [events, setEvents] = usePref('events', true)
  const [deepModel, setDeepModel] = usePref('deepModel', 'auto')
  const [useEmotes, setUseEmotes] = usePref('useEmotes', true)
  const [delay, setDelay] = usePref('delay2', '')
  const [minLen, setMinLen] = usePref('minLen', '')
  // already a clip (an X post cut from a stream): the whole video, just captioned and cropped
  const [whole, setWhole] = usePref('whole', 'auto')
  const [maxLen, setMaxLen] = usePref('maxLen', '')
  const [chatFile, setChatFile] = useState('')
  const [jobId, setJobId] = useState<string>()
  const [startError, setStartError] = useState('')
  const [starting, setStarting] = useState(false)
  const { job, running } = useJob(jobId)
  const noWhisper = status?.whisper === false
  const noLaughter = status?.laughter === false

  useEffect(() => {
    if (!job || job.id !== jobId) return
    if (job.status === 'done') {
      const p = Ref.safeParse(job.result)
      if (p.success && p.data.pid) go(reviewUrl(p.data.pid))
      else toast('The video is ready. Open it from Recent.')
    }
  }, [job, jobId, toast])

  // the parent asks for a start (a Recent row was picked): find clips in what's now in the box
  useEffect(() => {
    if (start > 0) void findClips()
  }, [start])

  async function browse(kind: 'video' | 'chat') {
    try {
      const r = await api(Picked, '/api/pick_file', { kind })
      if (r.path) kind === 'video' ? onSource(r.path) : setChatFile(r.path)
    } catch { /* the picker was closed */ }
  }

  async function findClips() {
    const src = source.trim().replace(/^"(.*)"$/, '$1')
    if (!src) return
    if (status?.ffmpeg === false) { toast('Download the missing tools first.', 'err'); return }
    setPref('src', src)
    setStartError('')
    setStarting(true)
    try {
      const r = await api(JobStarted, '/api/analyze', {
        source: src, chat_file: chatFile, count: Number(count), length: Number(length), chat_weight: Number(cw),
        detect: { preset, sensitivity: sens, min_len: optNum(minLen), max_len: optNum(maxLen), chat_delay: delay === '' ? 'auto' : Number(delay) },
        emotes: useEmotes, max_height: Number(getPref('maxh', '1080')), cookies: getPref('cookies', ''), events: events && !noLaughter,
        deep_scan: deep && !noWhisper, deep_model: deepModel, language: renderOpts().language,
        whole: whole === 'yes' ? true : whole === 'no' ? false : 'auto',
      })
      setJobId(r.job)
    } catch (e) {
      setStartError(e instanceof Error ? e.message : String(e))
    }
    setStarting(false)
  }

  const err = startError || (job?.status === 'error' ? job.error ?? 'Something went wrong.' : '')
  return (
    <section className="panel source" aria-labelledby="vod-h">
      <header>
        <h2 id="vod-h">Past broadcast or video</h2>
        <p className="muted">Find the best moments in a Twitch VOD, a YouTube video or finished stream, an X video, or a recording on your PC.</p>
      </header>
      <div className="row">
        <input className="input grow" data-testid="source-input" aria-label="Video link or file" spellCheck={false} autoComplete="off"
               placeholder="Twitch, YouTube or X link, or a video file" value={source}
               onChange={e => onSource(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') void findClips() }} />
        <button className="btn" onClick={() => browse('video')}>Browse…</button>
      </div>
      <div className="form-grid">
        <label className="span2">The video is
          <select className="input" data-testid="whole" value={whole} onChange={e => setWhole(e.target.value)}>
            <option value="auto">Automatic (an X video under 3 minutes is taken as a clip)</option>
            <option value="no">A stream or long video: find the clips in it</option>
            <option value="yes">Already a clip: just add captions and crop it</option>
          </select>
        </label>
        <label>Clips to suggest
          <select className="input" value={count} disabled={whole === 'yes'} onChange={e => setCount(e.target.value)}>
            {COUNTS.map(c => <option key={c} value={c}>{c === '0' ? 'Automatic' : c}</option>)}
          </select>
        </label>
        <label>Clip length
          <select className="input" value={length} disabled={whole === 'yes'} onChange={e => setLength(e.target.value)}>
            {LENGTHS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
        <label>Rank by
          <span className="range-row"><span className="tiny">Audio</span>
            <input type="range" min={0} max={100} step={5} value={cw} aria-label="Chat versus audio" onChange={e => setCw(Number(e.target.value))} />
            <span className="tiny">Chat</span></span>
        </label>
        <label>Stream type
          <select className="input" value={preset} onChange={e => { setPreset(e.target.value); setCw(PRESET_CHAT[e.target.value] ?? 60) }}>
            {PRESETS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
        <label>How many moments
          <select className="input" value={sens} onChange={e => setSens(e.target.value)}>
            <option value="fewer">Fewer, stronger</option><option value="normal">Normal</option><option value="more">More</option>
          </select>
        </label>
      </div>
      <label className="chk">
        <input type="checkbox" checked={deep && !noWhisper} disabled={noWhisper} onChange={e => setDeep(e.target.checked)} />
        <span>Deep scan<small>{noWhisper ? "Needs Whisper, which isn't available in this install."
          : 'Also transcribes the whole stream to find quiet funny lines that chat and volume miss. Takes longer; you can start reviewing right away.'}</small></span>
      </label>
      <details className="adv">
        <summary>Advanced</summary>
        <div className="form-grid">
          <label className="span2">Chat log file
            <span className="row"><input className="input grow" placeholder="Optional, for local videos" spellCheck={false} value={chatFile}
                                         onChange={e => setChatFile(e.target.value)} />
              <button className="btn" onClick={() => browse('chat')}>Browse…</button></span>
          </label>
          <label>Chat delay (sec)<input className="input" type="number" min={0} max={30} placeholder="Auto" value={delay} onChange={e => setDelay(e.target.value)} /></label>
          <label>Shortest clip (sec)<input className="input" type="number" min={3} max={179} placeholder="Auto" value={minLen} onChange={e => setMinLen(e.target.value)} /></label>
          <label>Longest clip (sec)<input className="input" type="number" min={4} max={180} placeholder="Auto" value={maxLen} onChange={e => setMaxLen(e.target.value)} /></label>
          <label>Deep scan accuracy
            <select className="input" value={deepModel} onChange={e => setDeepModel(e.target.value)}>
              <option value="auto">Automatic</option><option value="base">Fast</option><option value="small">Balanced</option>
              <option value="large-v3">Best (needs a GPU, 3 GB download)</option>
            </select>
          </label>
          <label className="chk span2"><input type="checkbox" checked={useEmotes} onChange={e => setUseEmotes(e.target.checked)} />
            <span>Recognise 7TV, BetterTTV and FrankerFaceZ emotes<small>Downloads the channel's emote names (cached for a day).</small></span></label>
          <label className="chk span2"><input type="checkbox" checked={events && !noLaughter} disabled={noLaughter} onChange={e => setEvents(e.target.checked)} />
            <span>Listen for laughter, screams and cheering<small>{noLaughter ? "The laughter detector isn't installed in this copy." : 'Adds about a minute per hour of video.'}</small></span></label>
        </div>
      </details>
      {jobId && (running || job?.status === 'done') && (
        <div className="job" data-testid="vod-job" role="status">
          <div className="job-top">
            <span>{job?.step || 'Starting'}</span>
            <span className="tnum muted">{job ? `${Math.round(job.progress * 100)}%${job.eta != null ? ' · ' + fmtLeft(job.eta) : ''}` : ''}</span>
            {running && <button className="link" onClick={() => api(Ok, '/api/cancel', { job: jobId }).catch(() => {})}>Cancel</button>}
          </div>
          <span className="meter wide" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round((job?.progress ?? 0) * 100)}>
            <i style={{ width: `${Math.round((job?.progress ?? 0) * 100)}%` }} />
          </span>
          {job && job.log.length > 0 && <pre className="job-log">{job.log.slice(-4).join('\n')}</pre>}
        </div>
      )}
      {err && <p className="err-tx" role="alert" data-testid="vod-error">{err}</p>}
      <footer>
        <button className="btn primary" data-testid="find-clips" disabled={starting} onClick={() => void findClips()}>{whole === 'yes' ? 'Open the clip' : 'Find clips'}</button>
      </footer>
    </section>
  )
}
