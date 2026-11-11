import { useCallback, useState } from 'react'
import { api } from '../../api/client'
import { JobStarted, Ok, VodExport, type MusicSpan, type VodExportVideo } from '../../api/schemas'
import { Seg } from '../../components/Seg'
import { useToast } from '../../components/Toasts'
import { usePoll } from '../../hooks/usePoll'
import { plural } from '../../format'
import { getPref, renderOpts, setPref } from '../../prefs'
import { fmtTime } from '../exports/renderStatus'
import { musicSummary, spanLabel } from './spans'

const loadView = () => api(VodExport, '/api/vodexport')

const CAPTIONS: Array<[string, string]> = [['burn', 'Burned in'], ['srt', 'Subtitle file'], ['both', 'Both'], ['off', 'Off']]
const CAPTIONS_HINT: Record<string, string> = {
  burn: 'The words are drawn on the video, at the bottom, a sentence at a time.',
  srt: 'Saved as a .srt file next to the video. YouTube and most players can show it.',
  both: 'Drawn on the video, and saved as a .srt file next to it.',
  off: 'No captions.',
}
const CAP_SIZES: Array<[string, string]> = [['50', 'Small'], ['60', 'Medium'], ['75', 'Large']]
const WORDS: Array<[string, string]> = [['mute', 'Mute'], ['bleep', 'Bleep'], ['off', 'Leave in']]
const SIZES: Array<[string, string]> = [['1080', '1080p'], ['720', '720p'], ['480', '480p']]
const QUALITY: Array<[string, string]> = [['fast', 'Fast'], ['balanced', 'Balanced'], ['high', 'Best']]

interface Choices { captions: string; capSize: string; words: string; size: string; quality: string }
const DEFAULTS: Choices = { captions: 'burn', capSize: '60', words: 'mute', size: '1080', quality: 'balanced' }

/** Export VOD: a whole video saved as one file, with the muting system and captions, and the copyright
 *  scan, which finds the music in it so each stretch can be muted or kept. */
export function VodExportView() {
  const poll = usePoll(loadView, 2000)
  const vids = poll.data?.videos ?? []
  const [pick, setPick] = useState<string>(() => getPref('vodxVideo', ''))
  const sel = vids.find(v => v.pid === pick) ?? vids[0]

  return (
    <section className="vodx" aria-labelledby="vodx-title">
      <header className="view-head">
        <h1 id="vodx-title">Export VOD</h1>
        <span className="muted">A whole video as one file, with muting and captions.</span>
      </header>
      {poll.error && <p className="banner err" role="alert">{poll.error}</p>}
      {!poll.data && !poll.error && <p className="muted pad">Loading…</p>}
      {poll.data && !vids.length && (
        <p className="empty" data-testid="vodx-empty">No video is open. Open one on <a href="#home">Home</a> and it shows up here.</p>
      )}
      {sel && poll.data && (
        <div className="vodx-split">
          <ul className="vodx-list" aria-label="Open videos">
            {vids.map(v => (
              <li key={v.pid}>
                <button className={`vodx-vid${v.pid === sel.pid ? ' on' : ''}`} aria-current={v.pid === sel.pid} data-testid="vodx-video"
                        onClick={() => { setPick(v.pid); setPref('vodxVideo', v.pid) }}>
                  <span className="t1">{v.title || 'Untitled video'}</span>
                  <span className="t2 tnum">{fmtTime(v.duration)}{v.export_job?.status === 'running' ? ' · exporting' : ''}</span>
                </button>
              </li>
            ))}
          </ul>
          <Editor key={sel.pid} v={sel} view={poll.data} refresh={poll.refresh} />
        </div>
      )}
    </section>
  )
}

function Editor({ v, view, refresh }: { v: VodExportVideo; view: VodExport; refresh: () => Promise<void> }) {
  const toast = useToast()
  const [c, setC] = useState<Choices>(() => ({ ...DEFAULTS, ...getPref<Partial<Choices>>('vodx', {}) }))
  const set = (patch: Partial<Choices>) => setC(cur => { const n = { ...cur, ...patch }; setPref('vodx', n); return n })
  const spans = v.scan?.spans ?? []
  // which music stretches stay as they are; every one is muted until you keep it. A new scan starts over.
  // the ticks belong to the scan they were made on: a new scan starts over. Kept with it rather than cleared by an
  // effect, which could run after a click made as the screen appeared and undo it
  const scanAt = v.scan?.at ?? 0
  const [keptFor, setKeptFor] = useState<{ at: number; set: Set<number> }>({ at: scanAt, set: new Set() })
  const kept = keptFor.at === scanAt ? keptFor.set : new Set<number>()
  const setKept = (f: Set<number> | ((k: Set<number>) => Set<number>)) =>
    setKeptFor(cur => {
      const base = cur.at === scanAt ? cur.set : new Set<number>()
      return { at: scanAt, set: typeof f === 'function' ? f(base) : f }
    })
  const scanning = v.scan_job?.status === 'running'
  const ex = v.export_job
  const exporting = ex?.status === 'running'
  const muted = spans.filter((_, i) => !kept.has(i))
  // captions and word muting need the words heard: Whisper, or the video's own YouTube captions
  const canHear = v.has_audio && (view.whisper || v.has_yt_words)
  const words = canHear ? c.words : 'off', captions = canHear ? c.captions : 'off'
  const noSpeech = !v.has_audio ? <p className="hint">This video has no sound.</p>
    : <p className="hint">Needs the speech model (Whisper). Get it in <a href="#settings/tools">Settings › Downloads and tools</a>.</p>

  async function start(path: string, body: object, what: string) {
    try {
      await api(JobStarted, path, { pid: v.pid, ...body })
      toast(what)
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), 'err')
    }
    await refresh()
  }
  const exportNow = () => {
    const o = renderOpts()
    void start('/api/vodexport/start', {
      captions, caption_size: Number(c.capSize), words, size: c.size, quality: c.quality,
      music: muted.map(s => [s.start, s.end]), whisper_model: o.whisper_model, language: o.language,
      caption_font: o.caption_font, hw_encode: o.hw_encode,
    }, 'Exporting the whole video…')
  }
  const cancel = (job: string) => api(Ok, '/api/cancel', { job }).then(refresh).catch(() => {})
  const toggle = (i: number) => setKept(k => { const n = new Set(k); if (n.has(i)) n.delete(i); else n.add(i); return n })

  return (
    <div className="vodx-body">
      {!v.exists && <p className="banner err" role="alert">This video's file was deleted, so it can't be exported.</p>}

      <section className="panel set" aria-labelledby="vodx-cr" data-testid="vodx-copyright">
        <header className="row">
          <h2 id="vodx-cr" className="grow">Copyright scan</h2>
          {scanning
            ? <button className="btn ghost sm" onClick={() => void cancel(v.scan_job!.id)}>Stop</button>
            : <button className={`btn${v.scan ? '' : ' primary'}`} data-testid="vodx-scan" disabled={!v.has_audio || !v.exists}
                      onClick={() => void start('/api/vodexport/scan', {}, 'Looking for music…')}>{v.scan ? 'Scan again' : 'Scan for music'}</button>}
        </header>
        <p className="muted">Finds the stretches where music plays, the usual cause of copyright claims and muted VODs, so you can mute them in the export.
          It hears that music is playing, not which song, so treat it as a safety net.</p>
        {!view.music_model && !v.scan && <p className="hint">Needs the laughter detector (it also hears music). Get it in <a href="#settings/tools">Settings › Downloads and tools</a>.</p>}
        {!v.has_audio && <p className="hint">This video has no sound.</p>}
        {scanning && <JobBar job={v.scan_job!} label="Looking for music" />}
        {v.scan_job?.status === 'error' && <p className="banner err" role="alert">{v.scan_job.error}</p>}
        {v.scan && !scanning && (
          spans.length === 0
            ? <p data-testid="vodx-clean">No music found.</p>
            : <>
                <MusicStrip spans={spans} kept={kept} duration={v.duration} />
                <p data-testid="vodx-music-summary">{musicSummary(spans, kept)}</p>
                <div className="row wrap">
                  <button className="btn sm" onClick={() => setKept(new Set())}>Mute all</button>
                  <button className="btn sm ghost" onClick={() => setKept(new Set(spans.map((_, i) => i)))}>Keep all</button>
                </div>
                <ul className="vodx-spans" aria-label="Music found">
                  {spans.map((s, i) => (
                    <li key={i}>
                      <label className="chk">
                        <input type="checkbox" checked={!kept.has(i)} onChange={() => toggle(i)} data-testid="vodx-span" />
                        <span><span className="tnum">{spanLabel(s)}</span><small>{Math.round(s.confidence * 100)}% sure it's music</small></span>
                      </label>
                    </li>
                  ))}
                </ul>
              </>
        )}
      </section>

      <section className="panel set" aria-labelledby="vodx-mute">
        <h2 id="vodx-mute">Flagged words</h2>
        {!canHear && noSpeech}
        {canHear && <Seg label="Flagged words" value={c.words} options={WORDS} onChange={words => set({ words })} />}
        {canHear && <p className="hint">
          {c.words === 'off' ? 'Nothing said is changed.' : `What the content check flags is ${c.words === 'bleep' ? 'bleeped' : 'silenced'} and hidden in the captions`}
          {c.words !== 'off' && (view.censor_categories.length ? `: ${view.censor_categories.join(', ').toLowerCase()}. ` : '. ')}
          {c.words !== 'off' && <>Choose what counts in <a href="#settings/safety">Settings › Content check</a>.</>}
        </p>}
      </section>

      <section className="panel set" aria-labelledby="vodx-cap">
        <h2 id="vodx-cap">Captions</h2>
        {!canHear && noSpeech}
        {canHear && <Seg label="Captions" value={c.captions} options={CAPTIONS} onChange={captions => set({ captions })} />}
        {canHear && <p className="hint">{CAPTIONS_HINT[c.captions]}</p>}
        {(captions === 'burn' || captions === 'both') && (
          <div className="row wrap"><span className="muted">Size</span><Seg label="Caption size" value={c.capSize} options={CAP_SIZES} onChange={capSize => set({ capSize })} /></div>
        )}
        {canHear && (captions !== 'off' || words !== 'off') && (
          <p className="hint">{v.has_yt_words ? "Uses the video's own YouTube captions." : 'The whole video is listened to first. Stretches already heard for clips are reused. On a long stream this is most of the wait.'}</p>
        )}
      </section>

      <section className="panel set" aria-labelledby="vodx-vid">
        <h2 id="vodx-vid">Video</h2>
        <div className="row wrap"><span className="muted">Size</span><Seg label="Size" value={c.size} options={SIZES} onChange={size => set({ size })} /></div>
        <div className="row wrap"><span className="muted">Quality</span><Seg label="Quality" value={c.quality} options={QUALITY} onChange={quality => set({ quality })} /></div>
        <p className="hint">A wide 16:9 video. Saved in your clips folder.</p>
      </section>

      <footer className="vodx-go">
        {exporting
          ? <><JobBar job={ex!} label="Exporting" /><button className="btn ghost" onClick={() => void cancel(ex!.id)}>Cancel</button></>
          : <button className="btn primary" data-testid="vodx-export" disabled={!v.exists} onClick={exportNow}>
              Export {fmtTime(v.duration)}{muted.length ? ` · ${plural(muted.length, 'music stretch', 'music stretches')} muted` : ''}
            </button>}
      </footer>
      {ex && !exporting && <ExportResult job={ex} />}
    </div>
  )
}

function JobBar({ job, label }: { job: NonNullable<VodExportVideo['export_job']>; label: string }) {
  const pct = Math.round(job.progress * 100)
  const left = job.eta && job.eta > 5 ? ` · about ${fmtTime(job.eta)} left` : ''
  return (
    <div className="vodx-job" data-testid="vodx-job">
      <span className="small">{job.step || label} · {pct}%{left}</span>
      <span className="meter wide" role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
        <i style={{ width: `${pct}%` }} />
      </span>
    </div>
  )
}

function ExportResult({ job }: { job: NonNullable<VodExportVideo['export_job']> }) {
  const toast = useToast()
  const r = (job.result ?? {}) as { name?: string; folder?: string; srt?: string; muted_words?: number; muted_music?: number }
  const show = useCallback(() => {
    api(Ok, '/api/open_folder', { folder: r.folder ?? 'clips' }).catch(e => toast(e instanceof Error ? e.message : String(e), 'err'))
  }, [r.folder, toast])
  if (job.status === 'error') return <p className="banner err" role="alert" data-testid="vodx-error">Export failed: {job.error}</p>
  if (job.status === 'cancelled') return <p className="muted">Export cancelled.</p>
  if (job.status !== 'done') return null
  const bits = [r.muted_music ? plural(r.muted_music, 'music stretch', 'music stretches') + ' muted' : '',
    r.muted_words ? plural(r.muted_words, 'flagged word') + ' muted' : '', r.srt ? `captions in ${r.srt}` : ''].filter(Boolean)
  return (
    <div className="panel vodx-done" data-testid="vodx-done">
      <p><b>Saved {r.name}</b>{bits.length ? ` · ${bits.join(', ')}` : ''}</p>
      {job.warning && <p className="hint">{job.warning}</p>}
      <button className="btn" onClick={show}>Show in folder</button>
    </div>
  )
}

/** Where the music is across the whole video: muted stretches solid, kept ones outlined. */
function MusicStrip({ spans, kept, duration }: { spans: MusicSpan[]; kept: Set<number>; duration: number }) {
  const d = Math.max(1, duration)
  return (
    <div className="vodx-strip" role="img" aria-label={`Music across the video: ${musicSummary(spans, kept)}`}>
      {spans.map((s, i) => (
        <i key={i} className={kept.has(i) ? 'kept' : ''} title={spanLabel(s)}
           style={{ left: `${s.start / d * 100}%`, width: `${Math.max(0.3, (s.end - s.start) / d * 100)}%` }} />
      ))}
    </div>
  )
}
