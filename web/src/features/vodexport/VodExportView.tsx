import { type RefObject, useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../../api/client'
import { Job, JobStarted, Ok, Picked, PreviewCopy, SegAdded, Segments, VodExport, VodOpened, VodYoutube, type MusicSpan, type VodExportVideo, type VodFlag } from '../../api/schemas'
import { Seg } from '../../components/Seg'
import { useToast } from '../../components/Toasts'
import { usePoll } from '../../hooks/usePoll'
import { plural } from '../../format'
import { getPref, renderOpts, setPref } from '../../prefs'
import { fmtTime } from '../exports/renderStatus'
import { ACTIONS, isSongSource, musicSummary, songSource, spanLabel, type MusicAction, type Song } from './spans'
import { type Box, VodTimeline, type VodTimelineHandle, ZoomButtons } from './VodTimeline'

const loadView = () => api(VodExport, '/api/vodexport')
const err = (e: unknown) => (e instanceof Error ? e.message : String(e))

const WORDS: Array<[string, string]> = [['mute', 'Mute'], ['bleep', 'Bleep']]
const SIZES: Array<[string, string]> = [['source', 'Original'], ['1080', '1080p'], ['720', '720p'], ['480', '480p']]
const QUALITY: Array<[string, string]> = [['fast', 'Fast'], ['balanced', 'Balanced'], ['high', 'Best']]
/** Export VOD's choices (Segments uses size and quality too). No captions since 5.1.2: YouTube makes its own. */
export interface Choices { words: string; size: string; quality: string; model: string; language: string }
export const DEFAULTS: Choices = { words: 'mute', size: 'source', quality: 'balanced', model: '', language: '' }
export { SIZES, QUALITY }

/** Export VOD: a whole video saved as one file, with its music and flagged words dealt with (no captions since 5.1.2:
 *  YouTube makes its own). */
export function VodExportView() {
  const toast = useToast()
  const poll = usePoll(loadView, 2000)
  const vids = poll.data?.videos ?? []
  const [pick, setPick] = useState<string>(() => getPref('vodxVideo', ''))
  const [opening, setOpening] = useState('')
  const sel = vids.find(v => v.pid === pick) ?? vids[0]
  const choose = (pid: string) => { setPick(pid); setPref('vodxVideo', pid) }

  async function open(body: { pid?: string; path?: string }, label: string) {
    setOpening(label)
    try {
      const r = await api(VodOpened, '/api/vodexport/open', body)
      choose(r.pid)
      await poll.refresh()
    } catch (e) { toast(err(e), 'err') }
    setOpening('')
  }
  async function browse() {
    const path = (await api(Picked, '/api/pick_file', { kind: 'video' }).catch(() => undefined))?.path
    if (path) await open({ path }, 'file')
  }

  return (
    <section className="vodx" aria-labelledby="vodx-title">
      <header className="view-head">
        <h1 id="vodx-title">Export VOD</h1>
        <span className="muted">A whole video as one file, with its music and flagged words dealt with. YouTube adds captions itself.</span>
      </header>
      {poll.error && <p className="banner err" role="alert">{poll.error}</p>}
      {!poll.data && !poll.error && <p className="muted pad">Loading…</p>}
      {poll.data && (
        <div className="vodx-split">
          <div className="vodx-side">
            {vids.length > 0 && (
              <ul className="vodx-list" aria-label="Videos ready to export">
                {vids.map(v => (
                  <li key={v.pid}>
                    <button className={`vodx-vid${v.pid === sel?.pid ? ' on' : ''}`} aria-current={v.pid === sel?.pid} data-testid="vodx-video" onClick={() => choose(v.pid)}>
                      <span className="t1">{v.title || 'Untitled video'}</span>
                      <span className="t2 tnum">{fmtTime(v.duration)}{v.export_job?.status === 'running' ? ' · exporting' : ''}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <div className="vodx-add">
              <button className="btn" data-testid="vodx-browse" disabled={!!opening} onClick={() => void browse()}>
                {opening === 'file' ? 'Opening…' : 'Choose a video on this PC…'}
              </button>
              {poll.data.library.length > 0 && <>
                <h2 className="vodx-sub">On this PC</h2>
                <ul className="vodx-list" aria-label="Videos on this PC" data-testid="vodx-library">
                  {poll.data.library.map(l => (
                    <li key={l.pid}>
                      <button className="vodx-vid" disabled={!!opening} onClick={() => void open({ pid: l.pid }, l.pid)}>
                        <span className="t1">{opening === l.pid ? 'Opening…' : l.title}</span>
                        <span className="t2 tnum">{l.duration ? fmtTime(l.duration) + ' · ' : ''}{l.where === 'file' ? 'File' : 'Downloaded by Clip Radar'}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </>}
            </div>
          </div>
          {sel ? <Editor key={sel.pid} v={sel} view={poll.data} refresh={poll.refresh} />
            : <p className="empty" data-testid="vodx-empty">Pick a video on the left: one Clip Radar downloaded, or any video on this PC.
              Videos you open on <a href="#home">Home</a> show up here too.</p>}
        </div>
      )}
    </section>
  )
}

function Editor({ v, view, refresh }: { v: VodExportVideo; view: VodExport; refresh: () => Promise<void> }) {
  const toast = useToast()
  const base = renderOpts()
  const [c, setC] = useState<Choices>(() => ({ ...DEFAULTS, ...getPref<Partial<Choices>>('vodx', {}) }))
  const set = (patch: Partial<Choices>) => setC(cur => { const n = { ...cur, ...patch }; setPref('vodx', n); return n })
  const model = c.model || String(base.whisper_model || 'small'), language = c.language || String(base.language || '')

  const canRemove = view.voice_model
  // the music: what the scan found, as it suggests, until you change any of it; then your list, kept for this video
  // (moved edges, stretches you marked yourself). A new scan starts over from what it found, keeping the ones you marked.
  const scanAt = v.scan?.at ?? 0
  const fromScan = (): Song[] => (v.scan?.spans ?? []).map(s => ({ start: s.start, end: s.end, act: s.suggest, confidence: s.confidence, speech: s.speech, found: s.song }))
  // a list kept from before the songs were recognised: each stretch gets the song recognised for it (same edges)
  const foundAt = (s: Song) => s.found ?? v.scan?.spans.find(x => x.start === s.start && x.end === s.end)?.song
  // kept on the server with the video (so Segments uses it too, and it's there in any window), and here until it's saved
  const [songsFor, setSongsFor] = useState<{ at: number; songs: Song[] } | undefined>(
    () => v.music ?? getPref<Record<string, { at: number; songs: Song[] }>>('vodxSongs', {})[v.pid])
  const saveTimer = useRef(0)
  const listOf = (cur = songsFor): Song[] => (cur && cur.at === scanAt ? cur.songs.map(s => ({ ...s, found: foundAt(s) })) : [...fromScan(), ...(cur?.songs.filter(s => s.mine) ?? [])].sort((a, b) => a.start - b.start))
  // Erase song needs the eraser model, or the song's recording (named or recognised: that needs no model)
  const canErase = (s: Song) => canRemove || !!songSource(s)
  const songs: Song[] = listOf().map(s => (s.act === 'voice' && !canErase(s) ? { ...s, act: 'mute' as const } : s))
  const acts = songs.map(s => s.act)
  const setSongs = (fn: (s: Song[]) => Song[]) => setSongsFor(cur => {
    const n = { at: scanAt, songs: fn(listOf(cur)).sort((a, b) => a.start - b.start) }
    setPref('vodxSongs', { ...getPref<Record<string, unknown>>('vodxSongs', {}), [v.pid]: n })
    clearTimeout(saveTimer.current)          // a drag changes it many times a second: saved once it settles
    saveTimer.current = window.setTimeout(() => { void api(Ok, '/api/vodexport/music', { pid: v.pid, ...n }).catch(() => {}) }, 500)
    return n
  })
  const setAct = (i: number | 'all', a: MusicAction) => setSongs(ss => ss.map((s, k) => (i === 'all' || k === i ? { ...s, act: a === 'voice' && !canErase(s) ? s.act : a } : s)))
  const setSongOf = (i: number, song: string) => setSongs(ss => ss.map((s, k) => (k === i ? { ...s, song } : s)))
  const [selSong, setSelSong] = useState(-1)
  const addSong = (a: number, b: number) => {
    const s: Song = { start: +a.toFixed(2), end: +Math.min(v.duration || b, b).toFixed(2), act: canRemove ? 'voice' : 'mute', mine: true }
    setSongs(ss => [...ss, s])
    setSelSong(songs.filter(x => x.start <= s.start).length)
  }

  // the flagged words queue: each one ticked (muted) or not, kept with the check it came from
  const flags = v.flags?.flags ?? []
  const flagsAt = v.flags?.at ?? 0
  const [ticksFor, setTicksFor] = useState<{ at: number; on: Record<string, boolean> }>({ at: flagsAt, on: {} })
  const ticks = ticksFor.at === flagsAt ? ticksFor.on : {}
  const isOn = (f: VodFlag) => ticks[f.id] ?? f.on
  const tick = (ids: string[] | 'all' | 'none', on?: boolean) => setTicksFor(cur => {
    const prev = cur.at === flagsAt ? cur.on : {}
    if (ids === 'all' || ids === 'none') return { at: flagsAt, on: Object.fromEntries(flags.map(f => [f.id, ids === 'all'])) }
    return { at: flagsAt, on: { ...prev, ...Object.fromEntries(ids.map(id => [id, !!on])) } }
  })
  const ticked = flags.filter(isOn)

  const canHear = v.has_audio && (view.whisper || v.has_yt_words)
  const exporting = v.export_job?.status === 'running'
  // 5.1.2: no captions (YouTube makes its own), so the picture is copied whenever the size is the video's own
  const fast = v.copyable && c.size === 'source'

  // the player: where you are, so the exact preview starts there
  const vid = useRef<HTMLVideoElement>(null)
  const [now, setNow] = useState(0)
  const seek = useCallback((t: number) => { const el = vid.current; if (el) { el.currentTime = t; setNow(t) } }, [])

  // 5.7: after erasing, what Shazam still recognises is muted (YouTube still caught songs before)
  const [claimCheck, setClaimCheck] = useState(() => getPref('vodxClaim', true))
  const options = (): Record<string, unknown> => ({
    pid: v.pid, captions: 'off', words: c.words, size: c.size, quality: c.quality, whisper_model: model, language,
    hw_encode: base.hw_encode,
    music: songs.filter(s => s.act !== 'keep').map(s => (s.act === 'voice' && songSource(s) ? [s.start, s.end, s.act, songSource(s)] : [s.start, s.end, s.act])),
    word_spans: ticked.map(f => [Math.max(0, f.start - 0.05), f.end + 0.05]),
    claim_check: claimCheck,
  })
  async function start(path: string, body: object, what: string) {
    try {
      await api(JobStarted, path, body)
      if (what) toast(what)
    } catch (e) { toast(err(e), 'err') }
    await refresh()
  }
  const cancel = (job: string) => api(Ok, '/api/cancel', { job }).then(refresh).catch(() => {})
  const toolsLink = <a href="#settings/tools">Settings › Downloads and tools</a>
  const noSpeech = !v.has_audio ? <p className="hint">This video has no sound.</p>
    : <p className="hint">Needs the speech model (Whisper). Get it in {toolsLink}.</p>
  const nVoice = acts.filter(a => a === 'voice').length, nMute = acts.filter(a => a === 'mute').length

  const tl = useRef<VodTimelineHandle>(null)
  const exactAt = (t: number) => void start('/api/vodexport/preview', { ...options(), at: t }, '')
  return (
    <div className="vodx-body vodx-two">
      {!v.exists && <p className="banner err vodx-wide" role="alert">This video's file was deleted, so it can't be exported.</p>}

      <div className="vodx-stagecol">
        <PreviewPanel v={v} vid={vid} now={now} setNow={setNow} seek={seek} songs={songs} setSongs={setSongs} addSong={addSong}
                      sel={selSong} setSel={setSelSong} tl={tl} flags={ticked}
                      onExact={() => exactAt(now)} cancel={cancel} />
      </div>
      <div className="vodx-panels">
      <FindSegments v={v} view={view} model={model} language={language} seek={seek} tl={tl} cancel={cancel}
                    start={start} toolsLink={toolsLink} />


      <section className="panel set" aria-labelledby="vodx-cr" data-testid="vodx-copyright">
        <header className="row">
          <h2 id="vodx-cr" className="grow">Songs and music</h2>
          {v.scan_job?.status === 'running'
            ? <button className="btn ghost sm" onClick={() => void cancel(v.scan_job!.id)}>Stop</button>
            : <button className="btn" data-testid="vodx-scan" disabled={!v.has_audio || !v.exists}
                      onClick={() => void start('/api/vodexport/scan', { pid: v.pid }, 'Looking for music…')}>{v.scan ? 'Scan again' : 'Scan for music'}</button>}
        </header>
        <p className="muted">Music is the usual cause of copyright claims and muted VODs. <b>Erase song</b> takes the song out, its singing
          too, and keeps people talking and the game's sound, the way YouTube Studio's Erase song does. <b>Mute all sound</b> silences the
          whole stretch. Each song is recognised (as Shazam does) and its own recording taken out exactly, like YouTube Studio's; a song that isn't
          recognised is erased by the eraser model. Check what the scan found, and mark any it missed (Shift and drag on the timeline,
          or the button below).</p>
        {!view.music_model && !v.scan && <p className="hint">Needs the laughter detector (it also hears music). Get it in {toolsLink}.</p>}
        {!canRemove && <p className="hint" data-testid="vodx-no-remover">Erase song needs the song eraser
          (about {view.voice_model_mb} MB). Get it in {toolsLink}. Until then, music can only be muted.</p>}
        {v.scan_job?.status === 'running' && <JobBar job={v.scan_job} label={v.scan_job.step || "Looking for music"} />}
        {v.scan_job?.status === 'error' && <p className="banner err" role="alert">{v.scan_job.error}</p>}
        {v.scan && v.scan_job?.status !== 'running' && songs.length === 0 && <p data-testid="vodx-clean">No music found.</p>}
        {v.scan && v.scan.recognise && !v.scan.named && v.scan.spans.length > 0 && v.scan_job?.status !== 'running' && (
          <p className="row wrap" data-testid="vodx-recognise-row">
            <span className="grow">Which songs are these? Recognising them lets Erase song take each exact recording out, like YouTube Studio.</span>
            <button className="btn sm" data-testid="vodx-recognise" disabled={!v.exists}
                    onClick={() => void start('/api/vodexport/recognise', { pid: v.pid }, 'Recognising the songs…')}>Recognise the songs</button>
          </p>)}
        {v.scan && v.scan.recognise && v.scan_job?.status !== 'running' && (
          <p className="row wrap" data-testid="vodx-everywhere-row">
            <span className="grow">{v.scan.everywhere
              ? 'Looked for songs everywhere in this video.'
              : "Songs quiet under talking can be missed by the music scan, and YouTube still catches them. Look for songs everywhere listens to the whole video (a few minutes an hour)."}</span>
            <button className="btn sm" data-testid="vodx-everywhere" disabled={!v.exists}
                    onClick={() => void start('/api/vodexport/everywhere', { pid: v.pid }, 'Looking for songs everywhere…')}>
              {v.scan.everywhere ? 'Look again' : 'Look for songs everywhere'}</button>
          </p>)}
        {songs.length > 0 && v.scan_job?.status !== 'running' && <>
          <p data-testid="vodx-music-summary">{musicSummary(songs, acts)}</p>
          {v.scan?.recognise && <label className="chk" title="After erasing, each song stretch is listened to again; anything still recognisable is muted in those seconds">
            <input type="checkbox" checked={claimCheck} data-testid="vodx-claim"
                   onChange={e => { setClaimCheck(e.target.checked); setPref('vodxClaim', e.target.checked) }} />
            <span>Check YouTube won't still hear them<small>After erasing, every song stretch is listened to again the way YouTube would. Anything still recognisable is muted in those seconds, and the export says where.</small></span></label>}
          <div className="row wrap">
            <span className="muted small">Set all to</span>
            {ACTIONS.filter(([a]) => a !== 'voice' || canRemove).map(([a, l]) => (
              <button key={a} className="btn sm ghost" onClick={() => setAct('all', a)}>{l}</button>
            ))}
          </div>
          <ul className="vodx-spans" aria-label="Music">
            {songs.map((s, i) => (
              <li key={`${s.start}-${i}`} className={`vodx-span${i === selSong ? ' on' : ''}`} data-testid="vodx-span">
                <button className="link tnum" onClick={() => { setSelSong(i); seek(s.start); tl.current?.show(s.start, s.end) }}
                        title="Show it on the timeline and play from its start">{spanLabel(s)}</button>
                <small className="muted">{s.mine ? 'Marked by you' : talk(s)}</small>
                <Seg label={`Music at ${fmtTime(s.start)}`} value={s.act} options={ACTIONS.filter(([a]) => a !== 'voice' || canErase(s))}
                     onChange={a => setAct(i, a as MusicAction)} />
                {s.found && <p className="vodx-found small" data-testid="vodx-found">
                  ♪ <b>{s.found.artist ? `${s.found.artist} – ` : ''}{s.found.title}</b>{' '}
                  <span className="muted">{s.found.url ? (s.act === 'voice' ? 'recognised: its recording is taken out exactly' : 'recognised')
                    : "recognised, but its recording isn't on YouTube: the eraser model does it"}</span></p>}
                {(s.act === 'voice' || !canRemove) && s.act !== 'keep' && (
                  <details className="vodx-song-ref" open={!s.found && !!s.song}>
                    <summary className="small">{s.found ? 'Not this song?' : 'Know which song it is?'}</summary>
                    <input type="text" data-testid="vodx-song-ref" value={s.song ?? ''} placeholder="YouTube link or song file"
                           aria-invalid={!!s.song?.trim() && !isSongSource(s.song)}
                           aria-label={`Which song plays at ${fmtTime(s.start)}: a YouTube link or the path of a song file`}
                           onChange={e => setSongOf(i, e.target.value)} />
                    <small className="muted">{s.song?.trim() && !isSongSource(s.song) ? 'Paste a link (https://…) or the full path of a song file.'
                      : 'Paste its YouTube link (or the song file) and that exact recording is taken out.'}</small>
                  </details>)}
                <span className="row vodx-span-acts">
                  <button className="btn sm ghost" disabled={!v.exists || v.preview_job?.status === 'running'} data-testid="vodx-span-hear"
                          onClick={() => { setSelSong(i); exactAt(s.start) }}>Hear it</button>
                  {s.mine && <button className="btn sm ghost" aria-label={`Remove the music you marked at ${fmtTime(s.start)}`}
                                     onClick={() => { setSongs(ss => ss.filter((_, k) => k !== i)); setSelSong(-1) }}>Remove</button>}
                </span>
              </li>
            ))}
          </ul>
        </>}
        <div className="row wrap">
          <button className="btn sm" data-testid="vodx-add-song" disabled={!v.has_audio} onClick={() => addSong(now, now + 30)}>
            Mark music from {fmtTime(now)}</button>
          <span className="hint">Then drag its edges on the timeline to where the song starts and ends.</span>
        </div>
      </section>

      <section className="panel set" aria-labelledby="vodx-mute" data-testid="vodx-flags">
        <header className="row">
          <h2 id="vodx-mute" className="grow">Flagged words</h2>
          {v.words_job?.status === 'running'
            ? <button className="btn ghost sm" onClick={() => void cancel(v.words_job!.id)}>Stop</button>
            : canHear && <button className="btn" data-testid="vodx-check-words" disabled={!v.exists}
                                 onClick={() => void start('/api/vodexport/words', { pid: v.pid, whisper_model: model, language }, 'Listening for flagged words…')}>
                {v.flags ? 'Check again' : 'Check for flagged words'}</button>}
        </header>
        {!canHear && !(v.flags && v.has_audio) && noSpeech}
        {/* a check already made can be used without the speech model: only checking again needs it */}
        {(canHear || (v.flags && v.has_audio)) && <>
          {!canHear && <p className="hint" data-testid="vodx-flags-no-whisper">Checking again needs the speech model (Whisper). Get it in {toolsLink}.</p>}
          <p className="muted">Listens to the whole video and lists what the content check flags, using what counts in{' '}
            <a href="#settings/safety">Settings › Content check</a>. Ticked words are {c.words === 'bleep' ? 'bleeped' : 'silenced'} and
            hidden in the captions.</p>
          {v.words_job?.status === 'running' && <JobBar job={v.words_job} label="Listening for flagged words" />}
          {v.words_job?.status === 'error' && <p className="banner err" role="alert">{v.words_job.error}</p>}
          {v.flags?.stale && <p className="hint">What counts as a flag changed since this check. Check again to use the new rules.</p>}
          {v.flags && v.words_job?.status !== 'running' && (flags.length === 0
            ? <p data-testid="vodx-no-flags">Nothing flagged.</p>
            : <>
                <div className="row wrap">
                  <span data-testid="vodx-flag-summary">{plural(flags.length, 'flagged word')}, {ticked.length} {c.words === 'bleep' ? 'bleeped' : 'muted'}.</span>
                  <Seg label="Flagged words" value={c.words} options={WORDS} onChange={words => set({ words })} />
                  <button className="btn sm ghost" onClick={() => tick('all')}>Tick all</button>
                  <button className="btn sm ghost" onClick={() => tick('none')}>Untick all</button>
                </div>
                <ul className="vodx-flags" aria-label="Flagged words">
                  {flags.map(f => (
                    <li key={f.id} className="vodx-flag">
                      <label className="chk">
                        <input type="checkbox" checked={isOn(f)} onChange={e => tick([f.id], e.target.checked)} data-testid="vodx-flag" />
                        <span><span>{f.context || f.text}</span><small>{f.label}</small></span>
                      </label>
                      <button className="link tnum" onClick={() => seek(Math.max(0, f.start - 2))} title="Play from here in the preview">{fmtTime(f.start)}</button>
                    </li>
                  ))}
                </ul>
              </>)}
        </>}
      </section>


      <section className="panel set" aria-labelledby="vodx-vid">
        <h2 id="vodx-vid">Video</h2>
        <div className="row wrap"><span className="muted">Size</span><Seg label="Size" value={c.size} options={SIZES} onChange={size => set({ size })} /></div>
        {c.size !== 'source' ? <div className="row wrap"><span className="muted">Quality</span><Seg label="Quality" value={c.quality} options={QUALITY} onChange={quality => set({ quality })} /></div> : null}
        <p className="hint" data-testid="vodx-speed">{fast
          ? 'The picture is copied as it is and only the sound is made again, so this is quick: a few minutes for a long stream.'
          : !v.copyable ? "This video's picture can't be copied into an .mp4 as it is, so every frame is made again."
          : 'A new size means every frame is made again. Original skips that.'} Saved in the Exported VODs folder
          (<a href="#settings/storage">Settings › Storage</a>), apart from your clips.</p>
      </section>

      <footer className="vodx-go">
        {exporting
          ? <><JobBar job={v.export_job!} label="Exporting" /><button className="btn ghost" onClick={() => void cancel(v.export_job!.id)}>Cancel</button></>
          : <button className="btn primary" data-testid="vodx-export" disabled={!v.exists}
                    onClick={() => void start('/api/vodexport/start', options(), 'Exporting the whole video…')}>
              Export {fmtTime(v.duration)}{[nVoice ? `${plural(nVoice, 'song')} erased` : '', nMute ? `${nMute} muted` : '', ticked.length ? plural(ticked.length, 'word') + ' muted' : '']
                .filter(Boolean).map(x => ' · ' + x).join('')}
            </button>}
      </footer>
      {v.export_job && !exporting && <ExportResult job={v.export_job} />}
      {v.exported && !exporting && <YouTubePanel v={v} view={view} refresh={refresh} />}
      </div>
    </div>
  )
}

const LENGTHS: Array<[string, string]> = [['short', 'Short (2 to 8 min)'], ['medium', 'Medium (5 to 15 min)'], ['long', 'Long (10 to 30 min)']]
type Part = NonNullable<VodExportVideo['found']>['parts'][number]

/** Find segments (5.0.3): the video split into the parts it's made of (where the topic changes, long pauses, a new
 *  game), each named from its own words, to tick and send to Segments, where each is a video of its own. */
function FindSegments({ v, view, model, language, seek, tl, cancel, start, toolsLink }: {
  v: VodExportVideo; view: VodExport; model: string; language: string; seek: (t: number) => void
  tl: RefObject<VodTimelineHandle | null>; cancel: (job: string) => void
  start: (path: string, body: object, what: string) => Promise<void>; toolsLink: React.ReactNode
}) {
  const toast = useToast()
  const [length, setLength] = useState(() => getPref('segLen', 'medium'))
  const found = v.found, job = v.found_job, running = job?.status === 'running'
  const at = found?.at ?? 0
  // ticks and names you changed, kept with the search they were made on: a new search starts over
  const [mineFor, setMineFor] = useState<{ at: number; on: Record<number, boolean>; names: Record<number, string> }>({ at, on: {}, names: {} })
  const mine = mineFor.at === at ? mineFor : { at, on: {}, names: {} }
  const parts = (found?.parts ?? []).map((p, i): Part & { i: number } => ({ ...p, i, on: mine.on[i] ?? p.on, name: mine.names[i] ?? p.name }))
  const ticked = parts.filter(p => p.on)
  const change = (i: number, patch: { on?: boolean; name?: string }) => setMineFor({ at, on: { ...mine.on, ...(patch.on != null ? { [i]: patch.on } : {}) },
    names: { ...mine.names, ...(patch.name != null ? { [i]: patch.name } : {}) } })
  const [sending, setSending] = useState(false)
  async function send() {
    setSending(true)
    try {
      const r = await api(SegAdded, '/api/segments/add_many', { pid: v.pid, name: `${v.title || 'Stream'} segments`,
        parts: ticked.map(p => ({ start: p.start, end: p.end, name: p.name })) })
      toast(`Sent ${plural(ticked.length, 'segment')} to ${r.name}. Each exports as a video of its own.`, '',
        [{ label: 'Open Segments', fn: () => { location.hash = `#segments/${r.id}` } }])
    } catch (e) { toast(err(e), 'err') }
    setSending(false)
  }
  const canHear = v.has_audio && (view.whisper || v.has_yt_words)
  return (
    <section className="panel set" aria-labelledby="vodx-find" data-testid="vodx-find">
      <header className="row">
        <h2 id="vodx-find" className="grow">Find segments</h2>
        {running
          ? <button className="btn ghost sm" onClick={() => cancel(job!.id)}>Stop</button>
          : <button className="btn" data-testid="vodx-find-btn" disabled={!v.exists}
                    onClick={() => void start('/api/vodexport/segfind', { pid: v.pid, length, whisper_model: model, language }, 'Finding the segments…')}>
              {found ? 'Find again' : 'Find segments'}</button>}
      </header>
      <p className="muted">Splits the stream into the parts it's made of: where the topic changes, at long pauses and when the game
        changes. Each part is named from what was said in it. Send the ones you want to Segments, where each is a video of its own.</p>
      <div className="row wrap"><span className="muted">Length</span>
        <Seg label="How long the segments are" value={length} options={LENGTHS} onChange={x => { setLength(x); setPref('segLen', x) }} /></div>
      {!canHear && <p className="hint">Without the speech model (Whisper) only pauses and game changes split it, not topics. Get it in {toolsLink}.</p>}
      {canHear && !found && <p className="hint">The whole video is listened to first, which takes a while on a long stream. What's
        already been heard (captions, the flagged words check) is reused.</p>}
      {running && <JobBar job={job!} label="Finding the segments" />}
      {job?.status === 'error' && <p className="banner err" role="alert">{job.error}</p>}
      {found && !running && (parts.length === 0 ? <p>No parts found.</p> : <>
        <div className="row wrap">
          <span data-testid="vodx-found-summary">{plural(parts.length, 'part')}, {ticked.length} ticked.</span>
          <button className="btn sm ghost" onClick={() => setMineFor({ at, on: Object.fromEntries(parts.map(p => [p.i, true])), names: mine.names })}>Tick all</button>
          <button className="btn sm ghost" onClick={() => setMineFor({ at, on: Object.fromEntries(parts.map(p => [p.i, false])), names: mine.names })}>Untick all</button>
        </div>
        {found.heard !== 'all' && <p className="hint">Split by pauses and game changes only: the whole video hasn't been listened to.</p>}
        <ul className="vodx-found" aria-label="Parts found">
          {parts.map(p => (
            <li key={p.i} className="vodx-part" data-testid="vodx-part">
              <input type="checkbox" checked={p.on} aria-label={`Send part ${p.i + 1} to Segments`} onChange={e => change(p.i, { on: e.target.checked })} />
              <span className="vodx-part-main">
                <input className="input" value={p.name} maxLength={80} aria-label={`Name of part ${p.i + 1}`} onChange={e => change(p.i, { name: e.target.value })} />
                <span className="small muted">
                  <button className="link tnum" onClick={() => { seek(p.start); tl.current?.show(p.start, p.end) }}>{spanLabel(p)}</button>
                  {p.why ? ` · ${p.why}` : ''}</span>
              </span>
            </li>
          ))}
        </ul>
        <button className="btn primary" data-testid="vodx-found-send" disabled={!ticked.length || sending} onClick={() => void send()}>
          {sending ? 'Sending…' : `Send ${plural(ticked.length, 'part')} to Segments`}</button>
      </>)}
    </section>
  )
}

/** "people talking over most of it" */
function talk(s: Pick<MusicSpan, 'speech'> | Song): string {
  const p = s.speech ?? 0
  return p >= 0.6 ? 'People talking over most of it' : p >= 0.2 ? 'People talking over some of it' : p > 0 ? 'A little talking' : 'No talking heard'
}

/** The video with the captions drawn over it as they'll look, the music and flagged words marked on its timeline,
 *  and the exact preview: ten seconds from where you are, made the way the export will be. */
function PreviewPanel({ v, vid, now, setNow, seek, songs, setSongs, addSong, sel, setSel, tl, flags, onExact, cancel }: {
  v: VodExportVideo; vid: RefObject<HTMLVideoElement | null>; now: number; setNow: (t: number) => void; seek: (t: number) => void
  songs: Song[]; setSongs: (fn: (s: Song[]) => Song[]) => void; addSong: (a: number, b: number) => void
  sel: number; setSel: (i: number) => void; tl: RefObject<VodTimelineHandle | null>
  flags: VodFlag[]
  onExact: () => void; cancel: (job: string) => void
}) {
  const [playable, setPlayable] = useState<'yes' | 'no' | 'making'>('yes')
  const [src, setSrc] = useState(`/vod/${v.pid}`)
  const pj = v.preview_job
  const exact = pj?.status === 'done' ? (pj.result as { url?: string; start?: number } | null) : null
  const [showExact, setShowExact] = useState('')
  useEffect(() => { if (exact?.url) setShowExact(exact.url) }, [exact?.url])

  async function makePlayable() {
    setPlayable('making')
    try {
      const r = await api(PreviewCopy, '/api/preview', { pid: v.pid })
      if (r.ready) { setSrc(`/vod/${v.pid}?preview=1`); setPlayable('yes'); return }
      const end = Date.now() + 30 * 60_000
      while (Date.now() < end) {
        await new Promise(res => setTimeout(res, 1500))
        const j = await api(Job, `/api/job/${r.job ?? ''}`)
        if (j.status === 'done') { setSrc(`/vod/${v.pid}?preview=1&t=${Date.now()}`); setPlayable('yes'); return }
        if (j.status !== 'running') break
      }
    } catch { /* falls through */ }
    setPlayable('no')
  }

  const tryingMusic = songs.findIndex(s => s.act !== 'keep' && now >= s.start && now < s.end)
  const d = Math.max(1, v.duration)
  // the part picked to send to Segments: where it starts and ends (-1 = not set yet)
  // kept for this video, so a part marked before Clip Radar closed is still there (5.0.2)
  const [mark, setMarkNow] = useState<{ a: number; b: number }>(() => getPref<Record<string, { a: number; b: number }>>('vodxMark', {})[v.pid] ?? { a: -1, b: -1 })
  const setMark = (m: { a: number; b: number }) => { setMarkNow(m); setPref('vodxMark', { ...getPref<Record<string, unknown>>('vodxMark', {}), [v.pid]: m }) }
  const [what, setWhat] = useState<'music' | 'part'>('music')       // what Shift+drag marks on the timeline
  const boxes: Box[] = [
    ...songs.map((s, i): Box => ({ id: `m${i}`, start: s.start, end: s.end, lane: 0, kind: s.act, edit: true,
      label: s.act === 'voice' ? 'Erase song' : s.act === 'mute' ? 'Mute all sound' : 'Keep' })),
    ...(v.found?.parts ?? []).map((p, i): Box => ({ id: `f${i}`, start: p.start, end: p.end, lane: 1, kind: p.quiet ? 'off' : 'seg', label: p.name })),
    ...(mark.a >= 0 && mark.b > mark.a ? [{ id: 'pick', start: mark.a, end: mark.b, lane: 1, kind: 'pick', edit: true, label: 'For Segments' } as Box] : []),
  ]
  const onBox = (id: string, a: number, b: number) => {
    if (id === 'pick') return setMark({ a, b })
    const i = Number(id.slice(1))
    setSongs(ss => ss.map((s, k) => (k === i ? { ...s, start: a, end: b } : s)))
  }

  return (
    <section className="panel set" aria-labelledby="vodx-pv" data-testid="vodx-preview">
      <header className="row">
        <h2 id="vodx-pv" className="grow">Preview</h2>
        {showExact && <button className="btn sm ghost" onClick={() => setShowExact('')}>Back to the video</button>}
      </header>
      <div className="vodx-stage">
        {showExact
          ? <video key={showExact} src={showExact} controls autoPlay className="vodx-video" data-testid="vodx-exact" />
          : <>
              <video ref={vid} src={src} controls preload="metadata" className="vodx-video" data-testid="vodx-player"
                     onTimeUpdate={e => setNow(e.currentTarget.currentTime)} onSeeked={e => setNow(e.currentTarget.currentTime)}
                     onError={() => { if (playable === 'yes' && !src.includes('preview=1')) setPlayable('no') }} />
              {playable !== 'yes' && (
                <div className="vodx-cover">
                  <p>{playable === 'making' ? 'Making a version the preview can play…' : "The preview can't play this kind of video."}</p>
                  {playable === 'no' && <button className="btn" onClick={() => void makePlayable()}>Make a version it can play</button>}
                </div>
              )}
            </>}
      </div>
      <div className="vodx-strip" role="img" aria-label={`The whole video: ${plural(songs.length, 'music stretch', 'music stretches')}, ${plural(flags.length, 'muted word')}`}
           onClick={e => { const r = e.currentTarget.getBoundingClientRect(), t = (e.clientX - r.left) / r.width * d; seek(t); tl.current?.show(Math.max(0, t - 60), t + 120); setShowExact('') }}>
        {songs.map((s, i) => (
          <i key={i} className={`m ${s.act}`} title={spanLabel(s)} style={{ left: `${s.start / d * 100}%`, width: `${Math.max(0.3, (s.end - s.start) / d * 100)}%` }} />
        ))}
        {flags.map(f => <i key={f.id} className="w" style={{ left: `${f.start / d * 100}%` }} />)}
        {mark.a >= 0 && mark.b > mark.a && (
          <i className="pick" data-testid="vodx-pick" style={{ left: `${mark.a / d * 100}%`, width: `${Math.max(0.3, (mark.b - mark.a) / d * 100)}%` }} />
        )}
        <b style={{ left: `${Math.min(100, now / d * 100)}%` }} />
      </div>
      <VodTimeline ref={tl} pid={v.pid} dur={v.duration} video={() => vid.current} boxes={boxes} sel={sel >= 0 ? `m${sel}` : undefined}
                   ticks={flags.map(f => ({ t: f.start, label: f.text }))} label="Timeline of the video: the music on it and the part picked for Segments"
                   onSeek={t => { setNow(t); setShowExact('') }} onBox={onBox}
                   onPick={id => setSel(id.startsWith('m') ? Number(id.slice(1)) : -1)}
                   onCreate={(a, b) => (what === 'music' ? addSong(a, b) : setMark({ a, b }))} />
      <div className="row wrap vodx-tl-bar">
        <ZoomButtons tl={tl} />
        <span className="small muted grow">Shift and drag marks</span>
        <Seg label="What Shift and drag marks" value={what} options={[['music', 'Music'], ['part', 'A part for Segments']]} onChange={w => setWhat(w as 'music' | 'part')} />
      </div>
      <SendToSegments v={v} now={now} seek={seek} mark={mark} setMark={setMark} />
      <p className="hint">
        {tryingMusic >= 0 ? `Music here: ${songs[tryingMusic]!.act === 'voice' ? 'the song will be erased, talking and game sound kept' : 'this stretch will be silent'}. ` : ''}
        The video plays with its own sound. <b>Exact preview</b> (or <b>Hear it</b> on a song) makes
        ten seconds the way the export will, with the song erased and words muted, so you can hear the result.
      </p>
      <div className="row wrap">
        {pj?.status === 'running'
          ? <><JobBar job={pj} label="Making the exact preview" /><button className="btn ghost sm" onClick={() => cancel(pj.id)}>Stop</button></>
          : <button className="btn" data-testid="vodx-exact-btn" disabled={!v.exists} onClick={() => { setShowExact(''); onExact() }}>Exact preview from {fmtTime(now)}</button>}
      </div>
      {pj?.status === 'error' && <p className="banner err" role="alert">{pj.error}</p>}
    </section>
  )
}

/** Pick a part of the video with the player (where it starts, where it ends) and send it to an edit in Segments. */
function SendToSegments({ v, now, seek, mark, setMark }: {
  v: VodExportVideo; now: number; seek: (t: number) => void
  mark: { a: number; b: number }; setMark: (m: { a: number; b: number }) => void
}) {
  const toast = useToast()
  const [edits, setEdits] = useState<Array<{ id: string; name: string }>>([])
  const [to, setTo] = useState(() => getPref('segTo', ''))
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const loadEdits = useCallback(() => api(Segments, '/api/segments')
    .then(r => setEdits(r.edits.filter(e => e.pid === v.pid).map(e => ({ id: e.id, name: e.name })))).catch(() => {}), [v.pid])
  useEffect(() => { void loadEdits() }, [loadEdits])
  const target = edits.some(e => e.id === to) ? to : (edits[0]?.id ?? '')
  const { a, b } = mark
  const ready = a >= 0 && b - a >= 1
  async function send() {
    setBusy(true)
    try {
      const r = await api(SegAdded, '/api/segments/add', { pid: v.pid, start: a, end: b, name: name.trim(), edit: target === 'new' ? '' : target })
      setTo(r.id); setPref('segTo', r.id)
      setMark({ a: -1, b: -1 }); setName('')
      await loadEdits()
      toast(`Sent to ${r.name}.`, '', [{ label: 'Open Segments', fn: () => { location.hash = `#segments/${r.id}` } }])
    } catch (e) { toast(err(e), 'err') }
    setBusy(false)
  }
  return (
    <div className="vodx-send" data-testid="vodx-send">
      <div className="row wrap">
        <button className="btn sm" data-testid="vodx-mark-a" onClick={() => setMark({ a: now, b: b > now ? b : -1 })}>Start here</button>
        <button className="btn sm" data-testid="vodx-mark-b" disabled={a < 0 || now <= a} onClick={() => setMark({ a, b: now })}>End here</button>
        <span className="small tnum" data-testid="vodx-mark">
          {a < 0 ? 'Play to where a part starts, then press Start here.'
            : b < 0 ? <>From <button className="link tnum" onClick={() => seek(a)}>{fmtTime(a)}</button>. Play to where it ends, then press End here.</>
            : <><button className="link tnum" onClick={() => seek(a)}>{spanLabel({ start: a, end: b })}</button></>}
        </span>
        {a >= 0 && <button className="btn sm ghost" onClick={() => setMark({ a: -1, b: -1 })}>Clear</button>}
      </div>
      {ready && (
        <div className="row wrap">
          <label className="fld inline">Name
            <input className="input" value={name} maxLength={80} placeholder="Optional" onChange={e => setName(e.target.value)} />
          </label>
          <label className="fld inline">Add to
            <select className="input" value={target || 'new'} onChange={e => { setTo(e.target.value); setPref('segTo', e.target.value) }}>
              {edits.map(e => <option key={e.id} value={e.id}>{e.name}</option>)}
              <option value="new">A new edit</option>
            </select>
          </label>
          <button className="btn primary" data-testid="vodx-send-btn" disabled={busy} onClick={() => void send()}>
            {busy ? 'Sending…' : 'Send to Segments'}</button>
        </div>
      )}
    </div>
  )
}

export function JobBar({ job, label }: { job: NonNullable<VodExportVideo['export_job']>; label: string }) {
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
  const r = (job.result ?? {}) as { name?: string; folder?: string; srt?: string; muted_words?: number; muted_music?: number; voice_music?: number; copied?: boolean; took?: number }
  const show = useCallback(() => {
    api(Ok, '/api/open_folder', { folder: r.folder ?? 'vods' }).catch(e => toast(err(e), 'err'))
  }, [r.folder, toast])
  if (job.status === 'error') return <p className="banner err" role="alert" data-testid="vodx-error">Export failed: {job.error}</p>
  if (job.status === 'cancelled') return <p className="muted">Export cancelled.</p>
  if (job.status !== 'done') return null
  const bits = [r.voice_music ? `music removed from ${plural(r.voice_music, 'stretch', 'stretches')}` : '',
    r.muted_music ? plural(r.muted_music, 'music stretch', 'music stretches') + ' muted' : '',
    r.muted_words ? plural(r.muted_words, 'flagged word') + ' muted' : '', r.srt ? `captions in ${r.srt}` : ''].filter(Boolean)
  return (
    <div className="panel vodx-done" data-testid="vodx-done">
      <p><b>Saved {r.name}</b>{bits.length ? ` · ${bits.join(', ')}` : ''}</p>
      {r.took != null && <p className="hint">Took {fmtTime(r.took)}{r.copied ? ', with the picture copied as it is' : ''}.</p>}
      {job.warning && <p className="hint">{job.warning}</p>}
      <button className="btn" onClick={show}>Show in folder</button>
    </div>
  )
}

/** The exported file to YouTube Studio: Clip Radar's own window puts it in and types the title and description, or
 *  the upload page opens in your browser with the file shown. Audience, visibility and Publish stay yours. */
function YouTubePanel({ v, view, refresh }: { v: VodExportVideo; view: VodExport; refresh: () => Promise<void> }) {
  const toast = useToast()
  const [title, setTitle] = useState(v.title)
  const [desc, setDesc] = useState('')
  const job = v.youtube_job
  const busy = job?.status === 'running'
  async function go(auto: boolean) {
    try {
      const r = await api(VodYoutube, '/api/vodexport/youtube', { pid: v.pid, title, description: desc, auto })
      if (r.opened) toast('YouTube Studio is opening. The video is selected in File Explorer.')
    } catch (e) { toast(err(e), 'err') }
    await refresh()
  }
  return (
    <section className="panel set" aria-labelledby="vodx-yt" data-testid="vodx-youtube">
      <h2 id="vodx-yt">YouTube Studio</h2>
      <label className="fld">Title <input className="input" value={title} maxLength={100} onChange={e => setTitle(e.target.value)} /></label>
      <label className="fld">Description <textarea className="input" rows={3} value={desc} maxLength={5000} onChange={e => setDesc(e.target.value)} /></label>
      <div className="row wrap">
        <button className="btn" data-testid="vodx-yt-upload" disabled={busy || !view.browser} onClick={() => void go(true)}>
          {busy ? 'Uploading…' : 'Upload to YouTube Studio'}</button>
        <button className="btn" data-testid="vodx-yt-open" disabled={busy} onClick={() => void go(false)}>Open YouTube Studio</button>
      </div>
      {busy && <JobBar job={job} label="YouTube Studio" />}
      <p className="hint">{view.browser
        ? "Upload puts the video in and types the title and description in Clip Radar's own browser window. You choose the audience and visibility and press Publish yourself."
        : 'Upload needs Microsoft Edge or Google Chrome. Open YouTube Studio shows the file to drag in.'}</p>
      {v.youtube?.state === 'ready' && !busy && <p data-testid="vodx-yt-ready">The video is in YouTube Studio. Finish the details and publish it there.</p>}
      {job?.status === 'error' && <p className="banner err" role="alert">{job.error}</p>}
    </section>
  )
}
