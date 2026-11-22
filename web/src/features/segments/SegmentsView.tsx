import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, ApiError } from '../../api/client'
import { JobStarted, Ok, SegSaved, Segments, type SegEdit, YtHandoff } from '../../api/schemas'
import { useConfirm } from '../../components/Confirm'
import { Seg } from '../../components/Seg'
import { useToast } from '../../components/Toasts'
import { usePoll } from '../../hooks/usePoll'
import { plural } from '../../format'
import { getPref, renderOpts, setPref } from '../../prefs'
import { fmtTime } from '../exports/renderStatus'
import { YouTubeBox } from '../exports/Posting'
import { Help } from '../../components/Tip'
import { DEFAULTS, ExportEstimate, JobBar, QUALITY, SIZES, type Choices } from '../vodexport/VodExportView'
import { type Box, VodTimeline, type VodTimelineHandle, ZoomButtons } from '../vodexport/VodTimeline'
import { addCut, editLen, gainToVolume, kept, move, pastCut, playlist, segLen, step, type Segment } from './model'

const loadView = () => api(Segments, '/api/segments')
const err = (e: unknown) => (e instanceof Error ? e.message : String(e))
interface Draft { name: string; segments: Segment[]; ver: number }
const fromEdit = (e: SegEdit): Draft => ({ name: e.name, segments: e.segments as Segment[], ver: e.ver })

/** Segments: parts of a video sent from Export VOD, trimmed, put in order and exported as one video. */
export function SegmentsView({ open }: { open: string | undefined }) {
  const poll = usePoll(loadView, 2000)
  const edits = poll.data?.edits ?? []
  const sel = edits.find(e => e.id === open) ?? edits[0]
  return (
    <section className="segv" aria-labelledby="segv-title">
      <header className="view-head">
        <h1 id="segv-title">Segments</h1>
        <span className="muted">Parts of a stream, put together into one video, as long as you like.</span>
      </header>
      {poll.error && <p className="banner err" role="alert">{poll.error}</p>}
      {!poll.data && !poll.error && <p className="muted pad">Loading…</p>}
      {poll.data && edits.length === 0 && (
        <p className="empty" data-testid="segv-empty">No edits yet. In <a href="#vod">Export VOD</a>, play a video, press <b>Start here</b> and{' '}
          <b>End here</b> around a part you want, and send it to Segments. Send as many parts as you like.</p>
      )}
      {poll.data && sel && (
        <div className="vodx-split">
          <ul className="vodx-list" aria-label="Edits">
            {edits.map(e => (
              <li key={e.id}>
                <a href={`#segments/${e.id}`} className={`vodx-vid${e.id === sel.id ? ' on' : ''}`} aria-current={e.id === sel.id} data-testid="segv-edit">
                  <span className="t1">{e.name}</span>
                  <span className="t2 tnum">{fmtTime(e.length)} · {plural(e.segments.length, 'segment')}{e.export_job?.status === 'running' ? ' · exporting' : ''}</span>
                </a>
              </li>
            ))}
          </ul>
          <Editor key={sel.id} edit={sel} refresh={poll.refresh} yt={poll.data} />
        </div>
      )}
    </section>
  )
}

function Editor({ edit, refresh, yt }: { edit: SegEdit; refresh: () => Promise<void>; yt: Segments | undefined }) {
  const toast = useToast()
  const confirm = useConfirm()

  // ---- the edit as it is here, saved a moment after each change
  const [draft, setDraft] = useState<Draft>(() => fromEdit(edit))
  const [saving, setSaving] = useState<'saved' | 'saving' | 'error'>('saved')
  const rev = useRef(0), savedRev = useRef(0)
  const latest = useRef({ draft, id: edit.id })
  latest.current = { draft, id: edit.id }
  const change = (fn: (d: Draft) => Draft) => { rev.current++; setDraft(fn) }
  const dirty = rev.current !== savedRev.current
  // a segment sent from Export VOD while nothing is being changed here: shown straight away
  useEffect(() => { if (!dirty && edit.ver > draft.ver) setDraft(fromEdit(edit)) }, [edit, dirty, draft.ver])
  const save = useCallback(async () => {
    const mine = rev.current, d = latest.current.draft
    setSaving('saving')
    try {
      const r = await api(SegSaved, '/api/segments/save', { id: latest.current.id, ver: d.ver, name: d.name, segments: d.segments })
      savedRev.current = mine
      setDraft(cur => ({ ...cur, ver: r.ver }))
      setSaving('saved')
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        const fresh = (await loadView().catch(() => undefined))?.edits.find(x => x.id === latest.current.id)
        if (fresh) { savedRev.current = rev.current; setDraft(fromEdit(fresh)) }
        toast(err(e), 'err')
        setSaving('saved')
      } else setSaving('error')
    }
    void refresh()
  }, [refresh, toast])
  useEffect(() => {
    if (!dirty) return
    const t = setTimeout(() => void save(), 600)
    return () => clearTimeout(t)
  }, [draft, dirty, save])
  // leaving the screen before the save went out: send it now
  useEffect(() => () => {
    if (rev.current !== savedRev.current) {
      const { draft: d, id } = latest.current
      void api(SegSaved, '/api/segments/save', { id, ver: d.ver, name: d.name, segments: d.segments }).catch(() => {})
    }
  }, [])

  const segs = draft.segments
  const setSegs = (fn: (s: Segment[]) => Segment[]) => change(d => ({ ...d, segments: fn(d.segments) }))
  const update = (id: string, patch: Partial<Segment>) => setSegs(ss => ss.map(s => (s.id === id ? { ...s, ...patch } : s)))
  const [selId, setSelId] = useState(segs[0]?.id ?? '')
  const seg = segs.find(s => s.id === selId) ?? segs[0]
  const total = editLen(segs)

  // ---- the player: one segment (cuts skipped), or the whole edit in order
  const vid = useRef<HTMLVideoElement>(null)
  const [now, setNow] = useState(0)
  const [mode, setMode] = useState<'' | 'seg' | 'edit'>('')
  const list = useMemo(() => playlist(segs), [segs])
  const at = useRef(0)
  const [src, setSrc] = useState(`/vod/${edit.pid}`)
  const [unplayable, setUnplayable] = useState(false)
  useEffect(() => { setSrc(`/vod/${edit.pid}`); setUnplayable(false) }, [edit.pid])
  const seek = useCallback((t: number) => { const el = vid.current; if (el) { el.currentTime = t; setNow(t) } }, [])
  const playing = mode === 'edit' ? segs.find(s => s.id === list[at.current]?.seg) : seg
  useEffect(() => { if (vid.current) vid.current.volume = gainToVolume(playing?.gain_db ?? 0) }, [playing?.gain_db, playing?.id])
  function playSeg() {
    if (!seg) return
    setMode('seg'); seek(seg.start); void vid.current?.play()
  }
  function playEdit() {
    if (!list.length) return
    at.current = 0; setMode('edit'); seek(list[0]!.a); void vid.current?.play()
  }
  function onTime(t: number) {
    setNow(t)
    const el = vid.current
    if (!el || el.paused) return
    if (mode === 'edit') {
      const r = step(list, at.current, t)
      if (r.stop) { el.pause(); setMode(''); return }
      if (r.seek != null) { at.current = r.i; el.currentTime = r.seek }
    } else if (mode === 'seg' && seg) {
      const skip = pastCut(seg, t)
      if (skip != null) el.currentTime = skip
      else if (t >= seg.end) { el.pause(); setMode('') }
    }
  }
  function onSeeked(t: number) {
    setNow(t)
    if (mode !== 'edit') return
    // moved by hand while the edit plays: carry on from the part it landed in, or stop following the edit
    const i = list.findIndex(r => t >= r.a - 0.3 && t < r.b)
    if (i < 0) setMode(''); else at.current = i
  }

  // ---- the video isn't open after a restart: open it
  const [opening, setOpening] = useState(false)
  useEffect(() => {
    if (edit.open || !edit.exists || opening) return
    setOpening(true)
    api(Ok, '/api/segments/open', { id: edit.id }).then(refresh).catch(e => toast(err(e), 'err')).finally(() => setOpening(false))
  }, [edit.open, edit.exists, edit.id])

  // ---- reorder by dragging (and with the arrow buttons, for the keyboard)
  const [drag, setDrag] = useState(-1)
  const moveSeg = (from: number, to: number) => setSegs(ss => move(ss, from, to))

  async function remove() {
    if (!await confirm({ title: `Delete ${draft.name}?`, ok: 'Delete', danger: true,
      body: 'The edit and its segments are removed. The video itself and anything already exported stay.' })) return
    try {
      await api(Ok, '/api/segments/delete', { id: edit.id })
      location.hash = '#segments'
    } catch (e) { toast(err(e), 'err') }
    await refresh()
  }

  const d = Math.max(1, edit.duration)
  // mode: each segment a video of its own (5.0.1), or the ticked ones joined into one
  const [c, setC] = useState<Choices & { cleanup: boolean; mode: 'each' | 'join' }>(() => ({ ...DEFAULTS, ...getPref<Partial<Choices>>('vodx', {}), cleanup: true, mode: 'each' as const, ...getPref('segx', {}) }))
  const set = (patch: Partial<typeof c>) => setC(cur => { const n = { ...cur, ...patch }; setPref('segx', n); return n })
  const base = renderOpts()
  const model = c.model || String(base.whisper_model || 'small'), language = c.language || String(base.language || '')

  const tl = useRef<VodTimelineHandle>(null)
  const exporting = edit.export_job?.status === 'running'
  const ticked = segs.filter(s => s.on && segLen(s) > 0)
  const exportBody = (one?: string) => ({ id: edit.id, ...c, captions: 'off', segment: one ?? '', whisper_model: model, language, hw_encode: base.hw_encode })
  function startExport(one?: string) {
    void api(JobStarted, '/api/segments/export', exportBody(one))
      .then(() => toast(one ? `Exporting ${segs.find(s => s.id === one)?.name || 'the segment'}…`
        : c.mode === 'join' ? 'Exporting the edit as one video…' : `Exporting ${plural(ticked.length, 'video')}…`))
      .catch(e => toast(err(e), 'err')).finally(() => void refresh())
  }
  const boxes: Box[] = [
    ...segs.map((s, i): Box => ({ id: s.id, start: s.start, end: s.end, lane: 0, kind: s.on ? 'seg' : 'off', edit: s.id === seg?.id,
      label: s.name || `Segment ${i + 1}` })),
    ...(seg ? seg.cuts.map(([a, b], k): Box => ({ id: `cut${k}`, start: a!, end: b!, lane: 1, kind: 'cut', edit: true, label: 'Cut' })) : []),
  ]
  function onBox(id: string, a: number, b: number) {
    if (!seg) return
    if (id.startsWith('cut')) {
      const k = Number(id.slice(3))
      return update(seg.id, { cuts: seg.cuts.map((c, j) => (j === k ? [Math.max(seg.start, a), Math.min(seg.end, b)] : c)) })
    }
    update(id, { start: a, end: b })
  }
  function addSeg(a: number, b: number) {
    const s: Segment = { id: Math.random().toString(36).slice(2, 10), name: `Segment ${segs.length + 1}`, start: a, end: b, cuts: [], gain_db: 0, on: true, lines: null }
    setSegs(ss => [...ss, s])
    setSelId(s.id)
  }
  return (
    <div className="vodx-body segv-body vodx-two">
      <header className="row wrap segv-head vodx-wide">
        <label className="fld grow">Edit name
          <input className="input segv-name" value={draft.name} maxLength={80} data-testid="segv-name"
                 onChange={e => change(x => ({ ...x, name: e.target.value }))} />
        </label>
        <span className="small muted" role="status" data-testid="segv-saved">
          {saving === 'saving' || dirty ? 'Saving…' : saving === 'error' ? <span className="err-tx">Not saved. Check that Clip Radar is running.</span> : 'Saved'}
        </span>
        <button className="btn danger sm" onClick={() => void remove()}>Delete edit</button>
      </header>
      {!edit.exists && <p className="banner err vodx-wide" role="alert">This edit's video was deleted, so it can't be played or exported.</p>}

      <div className="vodx-stagecol">
      <section className="panel set" aria-labelledby="segv-pv">
        <header className="row wrap">
          <h2 id="segv-pv" className="grow">Preview</h2>
          <span className="tnum small" data-testid="segv-length">{fmtTime(total)} long</span>
        </header>
        <div className="vodx-stage">
          {edit.open && (
            <video ref={vid} src={src} controls preload="metadata" className="vodx-video" data-testid="segv-player"
                   onTimeUpdate={e => onTime(e.currentTarget.currentTime)} onSeeked={e => onSeeked(e.currentTarget.currentTime)}
                   onPause={() => { if (mode === 'seg') setMode('') }}
                   onError={() => { if (!src.includes('preview=1')) setSrc(`/vod/${edit.pid}?preview=1`); else setUnplayable(true) }} />
          )}
          {(!edit.open || unplayable) && (
            <div className="vodx-cover"><p>{unplayable
              ? <>The preview can't play this kind of video. Open it in <a href="#vod">Export VOD</a> and press Make a version it can play.</>
              : opening ? 'Opening the video…' : edit.exists ? 'Opening the video…' : 'The video was deleted.'}</p></div>
          )}
        </div>
        <div className="vodx-strip segv-strip" role="img" aria-label={`Where the segments are in ${edit.title || 'the video'}`}
             onClick={e => { const r = e.currentTarget.getBoundingClientRect(), t = (e.clientX - r.left) / r.width * d; setMode(''); seek(t); tl.current?.show(Math.max(0, t - 60), t + 120) }}>
          {segs.map(s => (
            <i key={s.id} className={`sg${s.on ? '' : ' off'}${s.id === seg?.id ? ' sel' : ''}`} title={s.name}
               style={{ left: `${s.start / d * 100}%`, width: `${Math.max(0.3, (s.end - s.start) / d * 100)}%` }} />
          ))}
          <b style={{ left: `${Math.min(100, now / d * 100)}%` }} />
        </div>
        {edit.open && <VodTimeline ref={tl} pid={edit.pid} dur={edit.duration} video={() => vid.current} boxes={boxes} sel={seg?.id}
                                   label="Timeline of the video: the segments on it, and the stretches cut out of the one picked"
                                   onSeek={t => { setNow(t); if (mode === 'seg') setMode('') }} onBox={onBox}
                                   onPick={id => { if (!id.startsWith('cut')) setSelId(id) }} onCreate={addSeg} />}
        <div className="row wrap vodx-tl-bar">
          <ZoomButtons tl={tl} />
          {seg && <button className="btn sm ghost" onClick={() => tl.current?.show(seg.start, seg.end)}>Show {seg.name || 'this segment'}</button>}
          <span className="small muted">Drag a segment's edges to trim it. Shift and drag to add a segment.</span>
        </div>
        <div className="row wrap">
          <button className="btn primary" data-testid="segv-play-edit" disabled={!list.length || !edit.open} onClick={playEdit}>
            {mode === 'edit' ? 'Playing the edit' : 'Play the edit'}</button>
          {seg && <button className="btn" disabled={!edit.open} onClick={playSeg}>Play {seg.name || 'this segment'}</button>}
          <span className="small muted tnum">{fmtTime(now)}</span>
        </div>
        <p className="hint">Play the edit plays the ticked segments in order, cuts skipped, the way the export will be.
          Turning a segment down is heard here; turning it up is only heard in the export.</p>
      </section>
      </div>
      <div className="vodx-panels">

      <section className="panel set" aria-labelledby="segv-list">
        <header className="row wrap">
          <h2 id="segv-list" className="grow">Segments</h2>
          <span className="small muted">{segs.filter(s => s.on).length} of {segs.length} ticked for the export</span>
        </header>
        {segs.length === 0
          ? <p className="muted" data-testid="segv-no-segs">No segments left. Send parts from <a href="#vod">Export VOD</a>.</p>
          : <ol className="segv-segs" aria-label="Segments, in the order they play">
              {segs.map((s, i) => (
                <li key={s.id} className={`segv-seg${s.id === seg?.id ? ' on' : ''}${drag === i ? ' dragging' : ''}`} data-testid="segv-seg"
                    draggable onDragStart={() => setDrag(i)} onDragEnd={() => setDrag(-1)}
                    onDragOver={e => e.preventDefault()} onDrop={() => { if (drag >= 0) moveSeg(drag, i); setDrag(-1) }}>
                  <input type="checkbox" checked={s.on} aria-label={`Use ${s.name} in the export`} onChange={e => update(s.id, { on: e.target.checked })} />
                  <button className="segv-pick" aria-current={s.id === seg?.id} onClick={() => { setSelId(s.id); setMode(''); seek(s.start); tl.current?.show(s.start, s.end) }}>
                    <span className="t1">{s.name || `Segment ${i + 1}`}</span>
                    <span className="t2 tnum">{fmtTime(s.start)} to {fmtTime(s.end)} · {fmtTime(segLen(s))}{s.cuts.length ? ` · ${plural(s.cuts.length, 'cut')}` : ''}</span>
                  </button>
                  <span className="segv-acts">
                    <button className="ibtn" aria-label={`Move ${s.name} up`} disabled={i === 0} onClick={() => moveSeg(i, i - 1)}>↑</button>
                    <button className="ibtn" aria-label={`Move ${s.name} down`} disabled={i === segs.length - 1} onClick={() => moveSeg(i, i + 1)}>↓</button>
                    <button className="ibtn" aria-label={`Remove ${s.name}`} onClick={() => setSegs(ss => ss.filter(x => x.id !== s.id))}>×</button>
                  </span>
                </li>
              ))}
            </ol>}
        <p className="hint">Drag a segment, or use the arrows, to change the order. Unticked segments stay here but aren't exported.</p>
      </section>

      {seg && <SegmentPanel key={seg.id} seg={seg} now={now} seek={seek} update={p => update(seg.id, p)} edit={edit}
                            exporting={exporting} canExport={edit.exists && !dirty} onExport={() => startExport(seg.id)} />}

      <section className="panel set" aria-labelledby="segv-ex">
        <h2 id="segv-ex">Export</h2>
        <Seg label="How the segments are exported" value={c.mode} options={[['each', 'A video for each segment', 'Each ticked segment is saved as a video of its own, in a folder named after the edit.'],
                   ['join', 'All joined into one video', 'The ticked segments one after another, in the order they are in, as one video.']]}
             onChange={mode => set({ mode: mode as 'each' | 'join' })} />
        <p className="hint" data-testid="segv-mode-hint">{c.mode === 'each'
          ? <>Each ticked segment is saved as its own video, named after the segment, in a folder named after this edit. To export
            just one, use <b>Export this segment</b> on it.</>
          : 'The ticked segments are played one after another in one video, in the order shown.'}</p>
        <div className="row wrap"><span className="muted">Size<Help label="Size" text="Original copies each segment's picture as it is, the quickest by far (a segment then starts on the keyframe just before where you set it). Any other size makes every frame again." /></span><Seg label="Size" value={c.size} options={SIZES} onChange={size => set({ size })} /></div>
        <div className="row wrap"><span className="muted">Quality<Help label="Quality" text="How hard the encoder works on each frame when the picture is made again (any size but Original)." /></span><Seg label="Quality" value={c.quality} options={QUALITY} onChange={quality => set({ quality })} /></div>
        <p className="hint" data-testid="segv-speed">{c.size === 'source'
          ? 'Quick: the picture is copied as it is and only the sound is made again. Each segment starts on the nearest keyframe up to 2.5 seconds before where you set it. No captions: YouTube adds its own.'
          : 'A new size means every frame is made again. Original makes the export many times quicker.'}</p>
        <label className="chk"><input type="checkbox" checked={c.cleanup} onChange={e => set({ cleanup: e.target.checked })} />
          <span>Deal with music and flagged words<small>Music the copyright scan found is removed or muted as it suggests, and flagged words in the
            categories <a href="#settings/safety">Settings › Content check</a> mutes are muted. Scan the video in Export VOD first.</small></span></label>
        <footer className="vodx-go">
          {exporting
            ? <><JobBar job={edit.export_job!} label="Exporting" />
                <button className="btn ghost" onClick={() => void api(Ok, '/api/cancel', { job: edit.export_job!.id }).then(refresh).catch(() => {})}>Cancel</button></>
            : <button className="btn primary" data-testid="segv-export" disabled={!edit.exists || total < 1 || dirty}
                      onClick={() => startExport()}>
                {c.mode === 'each' ? `Export ${plural(ticked.length, 'video')} · ${fmtTime(total)}`
                  : `Export one video · ${fmtTime(total)}${segs.some(s => s.on) ? ` · ${plural(list.length, 'part')}` : ''}`}</button>}
        </footer>
        {!exporting && edit.exists && !dirty && total >= 1 && <ExportEstimate path="/api/segments/estimate" body={exportBody()} />}
        <ExportDone job={edit.export_job} edit={edit} yt={yt} refresh={refresh} />
      </section>
      </div>
    </div>
  )
}

/** One segment: its name, where it starts and ends, the stretches cut out and its volume. */
function SegmentPanel({ seg, now, seek, update, edit, exporting, canExport, onExport }: {
  seg: Segment; now: number; seek: (t: number) => void; update: (p: Partial<Segment>) => void; edit: SegEdit
  exporting: boolean; canExport: boolean; onExport: () => void
}) {
  const [cutFrom, setCutFrom] = useState(-1)
  const inside = now >= seg.start && now <= seg.end
  const setStart = (t: number) => update({ start: Math.max(0, Math.min(t, seg.end - 1)) })
  const setEnd = (t: number) => update({ end: Math.min(edit.duration || Infinity, Math.max(t, seg.start + 1)) })
  return (
    <section className="panel set" aria-labelledby="segv-one" data-testid="segv-detail">
      <header className="row wrap">
        <h2 id="segv-one" className="grow">{seg.name || 'Segment'}</h2>
        <button className="btn" data-testid="segv-export-one" disabled={!canExport || exporting || segLen(seg) < 0.5} onClick={onExport}>
          Export this segment · {fmtTime(segLen(seg))}</button>
      </header>
      <label className="fld">Name <input className="input" value={seg.name} maxLength={80} onChange={e => update({ name: e.target.value })} data-testid="segv-seg-name" /></label>

      <div className="segv-ends">
        <Trim label="Starts" at={seg.start} set={setStart} now={now} seek={seek} />
        <Trim label="Ends" at={seg.end} set={setEnd} now={now} seek={seek} />
      </div>

      <div className="set">
        <h3 className="segv-h3">Cut out</h3>
        <div className="row wrap">
          {cutFrom < 0
            ? <button className="btn sm" disabled={!inside} onClick={() => setCutFrom(now)} data-testid="segv-cut-a">Cut from here</button>
            : <>
                <button className="btn sm primary" disabled={now <= cutFrom} data-testid="segv-cut-b"
                        onClick={() => { update(addCut(seg, cutFrom, now)); setCutFrom(-1) }}>Cut to here</button>
                <span className="small tnum">From {fmtTime(cutFrom)}{now > cutFrom ? ` to ${fmtTime(now)} (${fmtTime(now - cutFrom)})` : ''}</span>
                <button className="btn sm ghost" onClick={() => setCutFrom(-1)}>Never mind</button>
              </>}
        </div>
        {!inside && cutFrom < 0 && <p className="hint">Play inside this segment to cut a stretch out of it.</p>}
        {seg.cuts.length > 0 && (
          <ul className="segv-cuts" aria-label="Stretches cut out">
            {seg.cuts.map(([a, b], i) => (
              <li key={i}>
                <button className="link tnum" onClick={() => seek(Math.max(seg.start, a! - 2))}>{fmtTime(a!)} to {fmtTime(b!)} ({fmtTime(b! - a!)})</button>
                <button className="btn sm ghost" onClick={() => update({ cuts: seg.cuts.filter((_, k) => k !== i) })}>Put back</button>
              </li>
            ))}
          </ul>
        )}
        <p className="hint tnum">Plays for {fmtTime(segLen(seg))}{kept(seg).length > 1 ? ` in ${kept(seg).length} parts` : ''}.</p>
      </div>

      <label className="fld">Volume <span className="range-row">
        <input type="range" min={-12} max={12} step={1} value={seg.gain_db} aria-label="Volume of this segment" data-testid="segv-gain"
               onChange={e => update({ gain_db: Number(e.target.value) })} />
        <span className="tnum">{seg.gain_db > 0 ? '+' : ''}{seg.gain_db} dB</span></span></label>

    </section>
  )
}

function Trim({ label, at, set, now, seek }: { label: string; at: number; set: (t: number) => void; now: number; seek: (t: number) => void }) {
  return (
    <div className="segv-trim">
      <span className="muted small">{label}</span>
      <button className="link tnum" onClick={() => seek(at)}>{fmtTime(at)}</button>
      <span className="row">
        <button className="btn sm ghost" aria-label={`${label} a second earlier`} onClick={() => set(at - 1)}>−1s</button>
        <button className="btn sm ghost" aria-label={`${label} a second later`} onClick={() => set(at + 1)}>+1s</button>
        <button className="btn sm" onClick={() => set(now)}>Set to {fmtTime(now)}</button>
      </span>
    </div>
  )
}

function ExportDone({ job, edit, yt, refresh }: {
  job: SegEdit['export_job']; edit: SegEdit; yt: Segments | undefined; refresh: () => Promise<void>
}) {
  const toast = useToast()
  if (!job || job.status === 'running') return null
  if (job.status === 'error') return <p className="banner err" role="alert" data-testid="segv-error">Export failed: {job.error}</p>
  if (job.status === 'cancelled') return <p className="muted">Export cancelled.</p>
  if (job.status !== 'done') return null
  const r = (job.result ?? {}) as { name?: string; folder?: string; srt?: string; length?: number; parts?: number; took?: number
    files?: Array<{ name: string; length: number; srt?: string; path?: string | undefined }> }
  const files: Array<{ name: string; length: number; path?: string | undefined }> = r.files ?? [{ name: r.name ?? '', length: r.length ?? 0, path: (r as { path?: string }).path }]
  return (
    <div className="vodx-done" data-testid="segv-done">
      {files.length === 1
        ? <p><b>Saved {files[0]!.name}</b> · {fmtTime(files[0]!.length)}</p>
        : <>
            <p><b>Saved {files.length} videos</b> · {fmtTime(r.length ?? 0)} in all</p>
            <ul className="segv-files">{files.map(f => <li key={f.name}>{f.name} <span className="muted tnum">{fmtTime(f.length)}</span></li>)}</ul>
          </>}
      {r.took != null && <p className="hint">Made from {plural(r.parts ?? 0, 'part')} in {fmtTime(r.took)}.</p>}
      {job.warning && <p className="hint">{job.warning}</p>}
      <button className="btn" onClick={() => void api(Ok, '/api/open_folder', { folder: r.folder ?? 'segments' }).catch(e => toast(err(e), 'err'))}>Show in folder</button>
      {/* 5.1.3: each exported segment to YouTube */}
      {files.filter(f => f.path).map(f => (
        <YouTubeBox key={f.path} id={f.path!} label={files.length > 1 ? `YouTube: ${f.name}` : 'YouTube'}
                    title0={f.name.replace(/\.[^.]+$/, '').replace(/ \(\d+\)$/, '')} desc0="" state={yt?.youtube[f.path!]}
                    can={yt?.uploads?.youtube ?? true} publish={yt?.uploads?.publish ?? false} onDone={refresh}
                    send={(auto, title, description) => api(YtHandoff, '/api/segments/youtube', { id: edit.id, file: f.path, title, description, auto })} />
      ))}
    </div>
  )
}
