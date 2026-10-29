import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../../api/client'
import { Campaigns, Clip, ClipsUpdate, Compared, DeepStatus, Job, JobStarted, LiveView, StreamerSet, Rechecked, Restored, ExportFormat, type Mark, Ok, Project, Queue, type QueueItem, Safety, Sources, TitlePrefs, TrackPath } from '../../api/schemas'
import { useConfirm } from '../../components/Confirm'
import { useToast } from '../../components/Toasts'
import { plural } from '../../format'
import { usePoll } from '../../hooks/usePoll'
import { getPref, type RenderOpts, renderOpts, setPref } from '../../prefs'
import { CHECK_LABEL, checkStatus, type Filter, fmt, gate, isGem, keepBody, keyOf, merge, nextTodo, openFlags, postFor,
  prep, qClass, queueFor, snapshot, visible } from './model'
import { bounds, clamp, fmtA, frameStep, type History, push, snapOf, type Snap } from './edit'
import { Player, type PlayerHandle } from './Player'
import { layoutOf } from './draw'
import { Preview, type Track } from './Preview'
import type { Hits } from './draw'
import { LayoutPanel, savePresets, TextPanel } from './StylePanels'
import { addLine, editLine, resetLines } from './captions'
import { CaptionsPanel } from './CaptionsPanel'
import { applied, fixSpans, flaggedList, isFlagged, previewPlan, FIX_LABEL } from './checks'
import { ContentCheck, type Proposal } from './ContentCheck'
import { Timeline, type TimelineHandle } from './Timeline'
import { Overview } from './Overview'
import { filterFor, type Span } from './streamMap'
import { LiveHead } from './LiveHead'
import { setReviewTodo } from '../../shell'
import { DeepBox, type Detect, DetectDialog, HeadFacts, TitleChoices } from './StreamTools'

const SPEEDS = [0.25, 0.5, 0.75, 1, 1.5, 2]
/** What following the face found (Layout tab). */
const trackNote = (t: (Track & { reason?: string; coverage?: number }) | null | undefined) => t === undefined ? ''
  : t === null ? 'Finding the face in this clip…'
  : t.path.length ? `${t.reason || 'Following the face'} (seen in ${Math.round((t.coverage ?? 0) * 100)}% of the clip). The crop moves smoothly, holds still while a face is hidden, and only moves to someone else once the person it follows has gone.`
  : `${t.reason || 'No face found'}. The crop stays at the focus point.`
/** What the face-following path depends on (the original page's trackKey). */
const trackKey = (c: Clip, o: RenderOpts) => [c.pid, c.start.toFixed(2), c.end.toFixed(2), o.track_target,
  o.track_target === 'point' ? `${Number(o.game_x).toFixed(2)},${Number(o.focus_y ?? 0.4).toFixed(2)}` : '', o.aspect, o.game_zoom].join('|')

const err = (e: unknown) => (e instanceof Error ? e.message : String(e))
const save = (c: Clip) => { api(Ok, '/api/clip_state', snapshot(c)).catch(() => {}) }
const feedback = (c: Clip, action: string, extra: object = {}) => { api(Ok, '/api/feedback', { pid: c.pid, clip: c.id, action, ...extra }).catch(() => {}) }

/** Review: #review lists the videos open now; #review/<project> reviews one, #review/live/<session> a live session. */
export function ReviewView({ pid, live = false, active = true, whisper = true }: { pid: string | undefined; live?: boolean; active?: boolean; whisper?: boolean }) {
  return pid ? <Workspace key={`${live}:${pid}`} pid={pid} live={live} active={active} whisper={whisper} /> : <Picker />
}

function Picker() {
  const src = usePoll(useCallback(() => api(Sources, '/api/sources'), []), 5000)
  const ready = (src.data?.items ?? []).filter(x => x.type === 'vod' && x.status === 'done' && x.pid)
  const lives = (src.data?.items ?? []).filter(x => x.type === 'live' && x.id)
  return (
    <section className="rv-pick" data-testid="review-picker">
      <h1>Review</h1>
      {ready.length ? (
        <ul className="rv-open">
          {ready.map(x => <li key={x.id}><a className="btn" href={`#review/${x.pid}`}>{x.title || x.source}</a></li>)}
          {lives.map(x => <li key={x.id}><a className="btn" href={`#review/live/${x.id}`}>Live: {x.title || x.source}</a></li>)}
        </ul>
      ) : lives.length ? (
        <ul className="rv-open">{lives.map(x => <li key={x.id}><a className="btn" href={`#review/live/${x.id}`}>Live: {x.title || x.source}</a></li>)}</ul>
      ) : <p className="muted">No video is open. Find clips in a video on <a href="#home">Home</a>, then review them here.</p>}
    </section>
  )
}

/** `active` is false while another screen is shown: the workspace stays as you left it, without its keys or sound. */
function Workspace({ pid, live: isLive, active, whisper }: { pid: string; live: boolean; active: boolean; whisper: boolean }) {
  const [live, setLive] = useState<LiveView>()
  // a live moment's times are in its own file: where each starts in the stream, kept from when it arrived
  const offs = useRef(new Map<string, number>())
  const noteOff = (x: Clip) => { if (!offs.current.has(keyOf(x))) offs.current.set(keyOf(x), x.abs_start != null ? x.abs_start - x.start : 0) }
  const span: Span = x => { const o = offs.current.get(keyOf(x)) ?? 0; return [x.start + o, x.end + o] }
  const [tab, setTab] = useState<'clip' | 'captions' | 'text' | 'layout'>('clip')
  const [now, setNow] = useState(0)
  const [flagFilter, setFlagFilter] = useState('all')
  const [proposal, setProposal] = useState<Proposal | null>(null)
  const [free, setFree] = useState<number | null>(null)       // previewing any moment of the stream, not a clip
  const [detOpen, setDetOpen] = useState(false)
  const [exact, setExact] = useState<{ url: string; start: number; end: number } | null>(null)   // a few seconds rendered like the export
  const [exactStep, setExactStep] = useState('')
  const [deep, setDeep] = useState<DeepStatus>()
  const trying = useRef<{ to: number; started: boolean } | null>(null)
  const toast = useToast()
  const confirm = useConfirm()
  const [meta, setMeta] = useState<Project>()
  const [failed, setFailed] = useState('')
  const [clips, setClips] = useState<Clip[]>([])
  const [cur, setCur] = useState<string>()
  const [filter, setFilter] = useState<Filter>('todo')
  const [listening, setListening] = useState(false)
  const [batchN, setBatchN] = useState('5')
  const [under, setUnder] = useState('0')
  const [busy, setBusy] = useState(false)
  const player = useRef<PlayerHandle>(null)
  const timeline = useRef<TimelineHandle>(null)
  const [hist, setHist] = useState<History>({ undo: [], redo: [] })
  const [speed, setSpeed] = useState(1)
  const dragFrom = useRef<Snap>(undefined)
  const getVideo = useCallback(() => player.current?.video() ?? null, [])
  useEffect(() => { if (!active) getVideo()?.pause() }, [active, getVideo])       // another screen: the preview stops
  // your render style (vc_opts, shared with the current page): what the preview draws and Keep renders
  const [opts, setOptsState] = useState<RenderOpts>(renderOpts)
  const setOpts = useCallback((patch: Partial<RenderOpts>, persist = true) => {
    setOptsState(o => { const n = { ...o, ...patch }; if (persist) setPref('opts', n); return n })
  }, [])
  const [guides, setGuides] = useState(() => !!getPref('guides', false))
  // the watermark clips get: the active campaign's own, or yours (Settings › Watermark)
  const exp = usePoll(useCallback(() => api(ExportFormat, '/api/export'), []), 30_000)
  const camps = usePoll(useCallback(() => api(Campaigns, '/api/campaigns'), []), 30_000)
  const camp = camps.data?.list.find(x => x.id === camps.data?.active && x.watermark.use)
  const wmServer = camp ? { on: true, marks: camp.watermark.marks } : exp.data?.prefs.watermark
  const [dragMarks, setDragMarks] = useState<Mark[]>()
  const marks = wmServer?.on ? dragMarks ?? wmServer.marks : []
  const replace = useMemo(() => Object.fromEntries((exp.data?.prefs.replace ?? []).map(([a, b]) => [a.toLowerCase(), b])), [exp.data])
  async function saveMarks(ms: Mark[]) {
    try {
      if (camp) await api(Campaigns, '/api/campaigns/save', { campaign: { ...camp, watermark: { use: true, marks: ms } } })
      else await api(ExportFormat, '/api/export', { watermark: { on: true, marks: ms } })
    } catch (e) { toast(err(e), 'err') }
    await Promise.all([exp.refresh(), camps.refresh()])
    setDragMarks(undefined)
  }
  const [tracks, setTracks] = useState<Record<string, Track | null>>({})
  const clipsRef = useRef(clips)
  clipsRef.current = clips
  const saveT = useRef(new Map<string, ReturnType<typeof setTimeout>>())      // typing: saved after a pause, per clip

  const safety = usePoll(useCallback(() => api(Safety, '/api/safety'), []), 60_000).data
  const titlesPoll = usePoll(useCallback(() => api(TitlePrefs, '/api/titles'), []), 60_000)
  const titles = titlesPoll.data
  const hashtagsOn = titles?.hashtags !== false
  // the render queue: a keep or remove since a request left means its answer is out of date
  const epoch = useRef(0)
  const queue = usePoll(useCallback(async () => ({ e: epoch.current, q: await api(Queue, '/api/queue') }), []), 2000)
  const items: QueueItem[] = queue.data?.q.items ?? []

  const update = useCallback((k: string, f: (c: Clip) => Clip, persist: 'now' | 'soon' | 'no' = 'now') => {
    setClips(cs => cs.map(c => {
      if (keyOf(c) !== k) return c
      const n = f(c)
      if (persist === 'now') { clearTimeout(saveT.current.get(k)); saveT.current.delete(k); save(n) }
      else if (persist === 'soon') {
        // saved after a pause, as the clip is then: a keep or skip in the meantime isn't undone by an older copy
        clearTimeout(saveT.current.get(k))
        saveT.current.set(k, setTimeout(() => { saveT.current.delete(k); const x = clipsRef.current.find(y => keyOf(y) === k); if (x) save(x) }, 600))
      }
      return n
    }))
  }, [])

  useEffect(() => {                                  // a live session: its moments, as one stream
    if (!isLive) return
    api(LiveView, `/api/live/${pid}`).then(v => {
      const cs = v.clips.map(prep)
      cs.forEach(noteOff)
      setLive(v)
      setMeta(Project.parse({ pid, title: v.channel || v.title, duration: Math.max(1, v.recorded), curve: v.curve }))
      setClips(cs)
      if (v.hands_free) setFilter('all')
      const first = cs.find(c => (v.hands_free || c.state === 'todo'))
      setCur(first && keyOf(first))
    }).catch(e => setFailed(err(e)))
  }, [pid, isLive])
  useEffect(() => {                                  // new moments, titles and the session's state as it records
    if (!isLive || !meta) return
    const t = setInterval(async () => {
      if (document.hidden) return
      let v: LiveView
      try { v = await api(LiveView, `/api/live/${pid}`) } catch { return }
      if (v.next && v.next !== pid) {                // Keep watching started the channel's next stream: follow it
        toast(`Stream ended with ${plural(clipsRef.current.length, 'clip')}. They're in Exports. Waiting for the next stream.`)
        location.hash = `#review/live/${v.next}`
        return
      }
      setLive(v)
      setMeta(m => (m ? { ...m, duration: Math.max(1, v.recorded), curve: v.curve } : m))
      const have = new Set(clipsRef.current.map(keyOf))
      const fresh = v.clips.filter(s => !have.has(keyOf(s))).map(prep)
      fresh.forEach(noteOff)
      const by = new Map(v.clips.map(s => [keyOf(s), s]))
      setClips(cs => cs.map(c => { const s = by.get(keyOf(c)); return s ? merge(c, s, saveT.current.has(keyOf(c))) : c }).concat(fresh))
      const last = fresh.at(-1)
      if (last) {
        if (curRef.current) toast(`New moment: ${last.title || `clip ${clipsRef.current.length + fresh.length}`}`)
        else setCur(keyOf(last))
      }
    }, 1500)
    return () => clearInterval(t)
  }, [isLive, !!meta, pid, toast])
  async function liveSettings(body: Record<string, unknown>) {
    setLive(v => (v ? { ...v, ...body } : v))         // shown at once; the session's answer follows
    try {
      setLive(await api(LiveView, `/api/live/${pid}/settings`, body))
      if ('hands_free' in body) toast(body.hands_free ? 'Hands-free is on: new moments render by themselves.' : 'Hands-free is off: new moments wait for you in Review.')
    } catch (e) { toast(err(e), 'err') }
  }
  async function clipNow() {
    try { await api(Ok, `/api/live/${pid}/clip`, { seconds: 30 }); toast('Clipping the last 30 seconds. It appears in the list in a few seconds.') }
    catch (e) { toast(err(e), 'err') }
  }
  async function stopLive() {
    if (!await confirm({ title: 'Stop live clipping?', body: 'Recording stops. Moments already found stay here so you can keep reviewing them.', ok: 'Stop', danger: true })) return
    try { await api(Ok, `/api/live/${pid}/stop`, {}); toast('Stopping…') } catch (e) { toast(err(e), 'err') }
  }

  useEffect(() => {                                  // open the video, and start writing titles
    if (isLive) return
    api(Project, `/api/project/${pid}`).then(p => {
      const cs = p.clips.concat(p.gems).map(prep)
      setMeta(p)
      setClips(cs)
      setDeep(p.deep)
      if (p.restored && p.settings) {                // opened before: the sliders go back to where you left them for this VOD
        setPref('count2', String(p.settings.count || 0)); setPref('length2', String(p.settings.length || 0)); setPref('cw', p.settings.chat_weight)
      }
      const first = cs.find(c => c.state === 'todo' && !isGem(c))
      setCur(first && keyOf(first))
      if (p.restored) toast(`Picked up where you left off: ${plural(p.review.kept, 'clip')} kept, ${p.review.skipped} skipped, ${p.review.todo} to review.`)
      else if (p.pick_note) toast(p.pick_note)
      const o = renderOpts()
      api(Ok, '/api/listen', { pid, order: cs.map(c => c.id), model: o.whisper_model, language: o.language })
        .then(() => setListening(true)).catch(() => {})
    }).catch(e => setFailed(err(e)))
  }, [pid, isLive, toast])

  const deepRunning = deep?.state === 'running'
  useEffect(() => {                                  // titles, captions, checks and quiet gems arrive in the background
    if (!listening && !deepRunning) return
    const t = setInterval(async () => {
      if (document.hidden) return
      try {
        const r = await api(ClipsUpdate, `/api/clips/${pid}`)
        const by = new Map(r.clips.concat(r.gems).map(s => [keyOf(s), s]))
        setClips(cs => {
          const have = new Set(cs.map(keyOf))
          const gems = r.gems.filter(s => !have.has(keyOf(s))).map(prep)          // deep scan found another
          return cs.map(c => { const s = by.get(keyOf(c)); return s ? merge(c, s, saveT.current.has(keyOf(c))) : c }).concat(gems)
        })
        setListening(r.listening)
        if (r.deep) setDeep(d => {
          if (d?.state === 'running' && r.deep!.state === 'done') {
            const n = r.gems.length
            toast(n ? `Deep scan found ${plural(n, 'quiet gem')}. They're in the Gems tab.` : 'Deep scan finished. No quiet gems stood out.')
          }
          return r.deep
        })
      } catch { /* the next tick tries again */ }
    }, 1200)
    return () => clearInterval(t)
  }, [listening, deepRunning, pid, toast])

  useEffect(() => {                                  // the render queue decides which clips are kept
    if (!queue.data || queue.data.e !== epoch.current) return
    const q = queue.data.q.items
    setClips(cs => cs.map(c => {
      const has = !!queueFor(c, q)
      if (has && c.state !== 'kept') { const n = { ...c, state: 'kept' as const }; save(n); return n }
      if (!has && c.state === 'kept') { const n = { ...c, state: 'todo' as const }; save(n); return n }
      return c
    }))
  }, [queue.data])

  // following the face (Layout › Follow the face): the crop's path for this clip, worked out by Clip Radar
  const trackFor = clips.find(x => keyOf(x) === cur)
  const tk = trackFor ? trackKey(trackFor, opts) : ''
  useEffect(() => {
    if (!trackFor || !tk || tk in tracks || layoutOf(opts) !== 'full' || opts.track === 'off') return
    const t = setTimeout(() => {
      setTracks(m => ({ ...m, [tk]: null }))
      api(TrackPath, '/api/framing/track', { pid: trackFor.pid, start: trackFor.start, end: trackFor.end, opts })
        .then(r => setTracks(m => ({ ...m, [tk]: r }))).catch(() => {})
    }, 500)
    return () => clearTimeout(t)
  }, [tk]) // the key holds everything that changes the path

  useEffect(() => {                                  // the Captions tab follows the line being said
    if (tab !== 'captions' && free == null) return
    const t = setInterval(() => setNow(getVideo()?.currentTime ?? 0), 250)
    return () => clearInterval(t)
  }, [tab, free, getVideo])
  const list = useMemo(() => (filter === 'flagged' ? flaggedList(clips, flagFilter, safety?.enabled !== false) : visible(clips, filter)),
    [clips, filter, flagFilter, safety?.enabled])
  const c = clips.find(x => keyOf(x) === cur)
  const q = c && queueFor(c, items)
  const counts = useMemo(() => {
    const hype = clips.filter(x => !isGem(x))
    return { todo: hype.filter(x => x.state === 'todo').length, skipped: hype.filter(x => x.state === 'skipped').length,
      all: hype.length, gems: clips.length - hype.length, flagged: clips.filter(isFlagged).length,
      flaggedOpen: clips.filter(x => checkStatus(x, safety?.enabled !== false) === 'needs_review').length }
  }, [clips, safety?.enabled])

  useEffect(() => { setReviewTodo(counts.todo) }, [counts.todo])     // the badge on Review in the menu
  useEffect(() => () => setReviewTodo(0), [])
  function select(x: Clip | undefined) { setFree(null); setCur(x && keyOf(x)) }
  function step(dir: 1 | -1) {
    const i = list.findIndex(x => keyOf(x) === cur)
    select(i < 0 ? list[0] : list[i + dir] ?? list[i])
  }
  function after(x: Clip) { select(nextTodo(clipsRef.current, x)) }

  /** An edit you can undo: recorded, shown at once, saved after a pause, and checked again. */
  function edit(x: Clip, label: string, patch: Partial<Clip>, group?: string) {
    setHist(h => push(h, keyOf(x), label, snapOf(x), snapOf({ ...x, ...patch }), group))
    update(keyOf(x), y => ({ ...y, ...patch }), 'soon')
    recheckSoon(keyOf(x))
  }
  /** The content check looks at the clip again after an edit (Settings › Content check › Recheck after edits). */
  const scanT = useRef(new Map<string, ReturnType<typeof setTimeout>>())
  function recheckSoon(k: string) {
    if (safety?.enabled === false) return
    if (safety?.auto_recheck === false) {        // switched off: just say the last check is out of date
      update(k, y => (y.safety ? { ...y, check: { excluded: false, fixes: [], ...y.check, stale: true } } : y), 'no')
      return
    }
    clearTimeout(scanT.current.get(k))
    scanT.current.set(k, setTimeout(async () => {
      const x = clipsRef.current.find(y => keyOf(y) === k)
      if (!x) return
      try {
        const r = await api(Rechecked, '/api/check', { ...snapshot(x), action: '' })
        const v = r.clips[0]
        if (v) update(k, y => ({ ...y, safety: v.safety ?? y.safety, check: v.check ?? y.check }), 'no')
      } catch { /* the next edit checks again */ }
    }, 350))
  }
  /** After a trim: a clip cut past the words heard is listened to again, so its captions and title fit. */
  function afterTrim(x: Clip) {
    if (isLive) return                               // a live session listens to its moments itself
    if (!x.words.length || (x.start >= x.words[0]!.start - 6 && x.end <= x.words.at(-1)!.end + 6)) return
    update(keyOf(x), y => ({ ...y, listened: false }), 'no')
    const o = renderOpts()
    api(Ok, '/api/listen', { pid, order: [x.id], model: o.whisper_model, language: o.language }).then(() => setListening(true)).catch(() => {})
  }
  /** Accuracy or language changed: every clip is listened to again. */
  function relisten() {
    if (isLive) return
    setClips(cs => cs.map(x => ({ ...x, listened: false })))
    api(Ok, '/api/listen', { pid, order: clipsRef.current.map(x => x.id), model: opts.whisper_model, language: opts.language })
      .then(() => setListening(true)).catch(() => {})
  }
  function trim(x: Clip, start: number, end: number, label: string, group?: string): boolean {
    if (x.state === 'kept') return false
    const b = bounds(x, start, end, isLive ? getVideo()?.duration || x.end + 30 : meta?.duration ?? x.end)
    if (!b) return false
    edit(x, label, { ...b, userTrim: true }, group)
    afterTrim({ ...x, ...b })
    return true
  }
  function nudge(which: 's' | 'e', by: number | 'now' | 'f' | '-f') {
    const x = c, vid = getVideo()
    if (!x || !vid) return
    const f = 1 / x.fps, d = by === 'f' ? f : by === '-f' ? -f : by === 'now' ? 0 : by
    const now = vid.currentTime
    const ok = which === 's'
      ? trim(x, by === 'now' ? now : x.start + d, x.end, by === 'now' ? 'set start' : 'move start', by === 'now' ? undefined : 'trim-s')
      : trim(x, x.start, by === 'now' ? now : x.end + d, by === 'now' ? 'set end' : 'move end', by === 'now' ? undefined : 'trim-e')
    if (!ok || by === 'now') return
    const start = which === 's' ? x.start + d : x.start, end = which === 'e' ? x.end + d : x.end
    if (by === 'f' || by === '-f') { vid.pause(); vid.currentTime = which === 'e' ? Math.max(start, end - f) : start }
    else { vid.currentTime = which === 'e' ? Math.max(start, end - 3) : start; void vid.play().catch(() => {}) }
  }
  function restore(x: Clip) {
    if (!x.orig || x.state === 'kept') return
    edit(x, 'restore original cut', { start: x.orig[0], end: x.orig[1], userTrim: false })
    timeline.current?.fit()
    const vid = getVideo()
    if (vid) vid.currentTime = x.orig[0]
    toast('Back to the original cut. Undo brings your trim back.')
  }
  function undoRedo(redo: boolean) {
    const from = redo ? hist.redo : hist.undo, e = from.at(-1)
    if (!e) return
    const x = clipsRef.current.find(y => keyOf(y) === e.key)
    if (!x) { setHist(h => (redo ? { ...h, redo: h.redo.slice(0, -1) } : { ...h, undo: h.undo.slice(0, -1) })); return }
    if (x.state === 'kept') return toast('That clip is in the render queue. Remove it from the queue to change it.', 'err')
    setHist(h => (redo ? { undo: [...h.undo, e], redo: h.redo.slice(0, -1) } : { undo: h.undo.slice(0, -1), redo: [...h.redo, e] }))
    update(e.key, y => ({ ...y, ...(redo ? e.after : e.before) }))
    if (cur !== e.key) setCur(e.key)
    toast(`${redo ? 'Redone' : 'Undone'}: ${e.label}`)
  }
  function changeSpeed(v: number) {
    setSpeed(v)
    const vid = getVideo()
    if (vid) vid.playbackRate = v
  }
  function stepFrame(dir: 1 | -1) {
    const vid = getVideo()
    if (!vid || !c) return
    vid.pause()
    vid.currentTime = frameStep(vid.currentTime, c.fps, dir)
  }

  // ---- the whole stream: re-ranking, previewing any moment, deep scan, the streamer's name
  function listen(ids: number[]) {
    const o = renderOpts()
    api(Ok, '/api/listen', { pid, order: ids, model: o.whisper_model, language: o.language }).then(() => setListening(true)).catch(() => {})
  }
  /** The clips suggested again with other settings; clips still suggested keep everything you did to them. */
  async function repick(det?: Detect, count = String(getPref('count2', '0')), length = String(getPref('length2', '0')), cw = Number(getPref('cw', 60))) {
    try {
      const p = await api(Project, '/api/repick', { pid, count: Number(count), length: Number(length), chat_weight: cw, ...(det ? { detect: det } : {}) })
      setPref('count2', count); setPref('length2', length); setPref('cw', cw)
      const old = new Map(clipsRef.current.map(x => [keyOf(x), x]))
      const next = p.clips.map(s => { const o = old.get(keyOf(s)); return o ? merge(o, s, saveT.current.has(keyOf(o))) : prep(s) })
        .concat(clipsRef.current.filter(isGem))
      setMeta(m => ({ ...p, gems: m?.gems ?? p.gems }))
      setClips(next)
      if (p.pick_note) toast(p.pick_note)
      if (!next.some(x => keyOf(x) === cur)) { const f = next.find(x => x.state === 'todo' && !isGem(x)); setCur(f && keyOf(f)) }
      listen(next.filter(x => !x.listened).map(x => x.id))
      epoch.current++; void queue.refresh()
      return true
    } catch (e) { toast(err(e), 'err'); return false }
  }
  function suggestMore() {
    const n = Math.min(100, (Number(getPref('count2', '0')) || clipsRef.current.filter(x => !isGem(x)).length) + 5)
    void repick(undefined, String(n))
  }
  function previewAt(t: number) {
    stopTrying()
    const at = clamp(t, 0, Math.max(0, (meta?.duration ?? t) - 0.5))
    setNow(at)
    setFree(at)
  }
  async function addHere() {
    const t = getVideo()?.currentTime ?? free ?? 0
    try {
      const x = { ...prep(await api(Clip, '/api/add_clip', { pid, t, length: Number(getPref('length2', '0')) })), kind: 'hype', manual: true }
      setClips(cs => [...cs, x])
      setFilter('todo')
      select(x)
      listen([x.id])
    } catch (e) { toast(err(e), 'err') }
  }
  async function startDeep() {
    try {
      setDeep(await api(DeepStatus, '/api/deepscan', { pid, model: getPref('deepModel', 'auto'), language: opts.language }))
      toast('Deep scan started. Quiet gems show up in the Gems tab as it goes.')
    } catch (e) { toast(err(e), 'err') }
  }
  async function stopDeep() {
    try { setDeep(await api(DeepStatus, '/api/deepscan', { pid, cancel: true })) } catch (e) { toast(err(e), 'err') }
  }
  async function setStreamer(name: string): Promise<boolean> {
    try {
      const r = await api(StreamerSet, '/api/streamer', { pid, name })
      setMeta(m => (m ? { ...m, streamer_name: r.name } : m))
      const by = new Map(r.clips.map(v => [v.id, v]))
      setClips(cs => cs.map(x => {
        const v = by.get(x.id)
        if (!v) return x
        const n: Clip = { ...x, title_a: v.title_a ?? undefined, title_b: v.title_b ?? undefined, title_b_named: !!v.title_b_named,
          titles: v.titles ?? x.titles, description: v.description ?? x.description, hashtags: v.hashtags ?? x.hashtags, post: v.post ?? x.post }
        if (!x.userTitle && v.title) n.title = v.title
        return n
      }))
      toast(r.name ? `Title B now uses "${r.name}".` : 'Streamer name removed.')
      return true
    } catch (e) { toast(err(e), 'err'); return false }
  }

  async function queueKeep(x: Clip, quiet = false, sound = true): Promise<boolean> {
    epoch.current++
    try {
      await api(Ok, '/api/queue/add', keepBody(x, postFor(x, hashtagsOn), opts, sound))
    } catch (e) {
      if (!quiet) toast(err(e), 'err')
      return false
    }
    epoch.current++
    update(keyOf(x), y => ({ ...y, state: 'kept' }))
    feedback(x, 'keep')
    if (!quiet) toast(`Rendering "${x.title.trim() || `clip ${x.id}`}" in the background.`)
    void queue.refresh()
    return true
  }
  async function keep(x: Clip | undefined = c) {
    if (!x) return
    if (x.state === 'kept') {
      if (q?.status === 'error') {
        try { await api(Ok, '/api/queue/retry', { id: q.id }); toast('Rendering again…') } catch (e) { toast(err(e), 'err') }
        void queue.refresh()
      }
      return
    }
    const g = gate(x, safety)
    if (g === 'excluded') return toast('This clip is excluded from export. Include it again in the content check first.', 'err')
    if (g === 'blocked') return toast('Review its flags first: leave it as is, fix them or dismiss them.', 'err')
    if (g === 'ask') {
      const flags = openFlags(x).map(f => `${f.label}: “${f.text}”`).join(' · ')
      if (!await confirm({ title: 'Render a clip with flags?', ok: 'Render anyway',
        body: `${flags}. Rendering it records that you saw this warning. Clip Radar's check isn't TikTok's decision either way.` })) return
      try { await api(Ok, '/api/check', { ...snapshot(x), action: 'ack', args: {} }) } catch (e) { toast(err(e), 'err'); return }
    }
    if (await queueKeep(x)) after(x)
  }
  async function skip(x: Clip | undefined = c) {
    if (!x) return
    if (x.state === 'kept') {
      if (q?.status === 'done') return
      epoch.current++
      if (q) await api(Ok, '/api/queue/remove', { id: q.id }).catch(() => {})
      epoch.current++
      update(keyOf(x), y => ({ ...y, state: 'todo' }))
      feedback(x, 'undo')
      void queue.refresh()
      return
    }
    if (x.state === 'skipped') { update(keyOf(x), y => ({ ...y, state: 'todo' })); feedback(x, 'undo'); return }
    update(keyOf(x), y => ({ ...y, state: 'skipped' }))
    feedback(x, 'skip')
    after(x)
    if (!x.manual) {
      const why = (reason: string) => () => feedback(x, 'reason', { reason })
      toast('Skipped. Why? (optional)', '', [{ label: 'Not interesting', fn: why('boring') }, { label: 'Bad cut', fn: why('cut') },
        { label: 'Duplicate', fn: why('duplicate') }, { label: 'Just noise', fn: why('noise') }])
    }
  }
  function nextTitle() {
    if (!c || c.state === 'kept' || c.titles.length < 2) return
    const i = c.titles.indexOf(c.title)
    edit(c, 'new title', { title: c.titles[(i + 1) % c.titles.length]!, userTitle: true })
  }

  async function batchKeep() {
    const n = Number(batchN)
    const todo = list.filter(x => x.state === 'todo' && !isGem(x))
    const pick = n ? todo.slice(0, n) : todo
    if (!pick.length) return toast('Nothing left to review in this list.')
    if (!await confirm({ title: `Keep and render ${plural(pick.length, 'clip')}?`, ok: 'Keep and render',
      body: `The top ${plural(pick.length, 'clip')} in this list, each rendered with the current export settings and file name, one after another (Settings › Performance sets how many at once). Clips with content flags to review are left for you.` })) return
    let done = 0, held = 0, bad = 0
    setBusy(true)
    try {
      for (const x of pick) {
        if (gate(x, safety) !== 'ok') { held++; continue }
        if (await queueKeep(x, true, !done)) done++; else bad++          // one queue sound for the batch
      }
    } finally { setBusy(false) }
    if (c && pick.some(x => keyOf(x) === cur)) after(c)
    toast(`Rendering ${plural(done, 'clip')}.` + (held ? ` ${plural(held, 'clip')} left for you: review the content flags first.` : '')
      + (bad ? ` ${plural(bad, 'clip')} couldn't be added.` : ''), bad ? 'err' : '')
  }
  async function batchSkip() {
    const u = Number(under)
    const pick = list.filter(x => x.state === 'todo' && !isGem(x) && !x.manual && (!u || x.quality < u))
    if (!pick.length) return toast(u ? `No clips scored under ${u} left to review.` : 'Nothing left to review in this list.')
    if (pick.length > 3 && !await confirm({ title: `Skip ${plural(pick.length, 'clip')}?`, ok: 'Skip',
      body: (u ? `Every clip in this list scored under ${u}.` : 'Every clip left in this list.') + ' They move to Skipped, where you can still keep any of them.' })) return
    const keys = new Set(pick.map(keyOf))
    setClips(cs => cs.map(x => { if (!keys.has(keyOf(x))) return x; const n = { ...x, state: 'skipped' as const }; save(n); return n }))
    if (c && keys.has(keyOf(c))) select(clipsRef.current.find(x => x.state === 'todo' && !isGem(x) && !keys.has(keyOf(x))))
    toast(`Skipped ${plural(pick.length, 'clip')}.`, '', [{ label: 'Undo', fn: () => {
      setClips(cs => cs.map(x => { if (!keys.has(keyOf(x)) || x.state !== 'skipped') return x; const n = { ...x, state: 'todo' as const }; save(n); return n }))
    } }])
  }

  // ---- the content check box: actions on one clip, fixes tried before they're made, Remove
  async function checkAct(x: Clip, action: string, args: Record<string, unknown> = {}) {
    try {
      const r = await api(Rechecked, '/api/check', { ...snapshot(x), action, args })
      const v = r.clips[0]
      if (v) update(keyOf(x), y => ({ ...y, ...applied(y, v) }), 'no')
      if (action === 'fix') toast(`${FIX_LABEL[String(args.kind)] ?? 'Fix'}: done. Undo is next to the flag.`)
      if (action === 'exclude') { epoch.current++; void queue.refresh() }
    } catch (e) { toast(err(e), 'err') }
  }
  async function flagBatch(action: string, args: Record<string, unknown>, verb: string, what: string) {
    const pick = action === 'exclude' ? list.filter(x => !x.check?.excluded) : list.filter(x => x.state !== 'kept' && (x.safety?.open ?? 0) > 0)
    if (!pick.length) return toast('Nothing in this list needs that.')
    if (!await confirm({ title: `${verb} for ${plural(pick.length, 'clip')}?`, ok: verb, body: what })) return
    try {
      // a live session's moments are each their own project: one request for each
      const pids = [...new Set(pick.map(x => x.pid))]
      const answers = await Promise.all(pids.map(p => api(Rechecked, '/api/check', { pid: p, ids: pick.filter(x => x.pid === p).map(x => x.id), action, args })))
      const by = new Map<string, (typeof answers)[number]['clips'][number]>(answers.flatMap((r, i) => r.clips.map(v => [`${pids[i]!}:${v.id}`, v] as const)))
      setClips(cs => cs.map(x => { const v = by.get(keyOf(x)); return v ? { ...x, safety: v.safety ?? x.safety, check: v.check ?? x.check,
        ...(typeof v.state === 'string' ? { state: v.state as Clip['state'] } : {}) } : x }))
      if (action === 'exclude') { epoch.current++; void queue.refresh() }
      toast(`Done for ${plural(pick.length, 'clip')}.`)
    } catch (e) { toast(err(e), 'err') }
  }
  function stopTrying() {
    trying.current = null
    setProposal(p => (p ? { ...p, playing: false } : p))
    getVideo()?.pause()
  }
  async function propose(x: Clip, fp: string, kind: string) {
    stopTrying()
    try {
      const r = await api(Rechecked, '/api/check', { ...snapshot(x), action: 'fix', args: { fp, kind, preview: true } })
      const fix = (r as { fix?: { span?: [number, number] } }).fix
      setProposal({ fp, kind, view: (r.clips[0] ?? {}) as Record<string, unknown>, span: fix?.span, playing: false })
    } catch (e) { toast(err(e), 'err') }
  }
  function tryFix(x: Clip) {
    const p = proposal, vid = getVideo()
    if (!p || !vid) return
    if (p.playing) { stopTrying(); return }
    const plan = previewPlan(x, p.kind, p.view, p.span)
    trying.current = { to: plan.to, started: false }
    setProposal({ ...p, playing: true })
    vid.currentTime = plan.from
    void vid.play().catch(() => {})
  }
  async function removeClip(x: Clip) {
    if (!await confirm({ title: `Remove clip #${x.id} from this project?`, ok: 'Remove', danger: true,
      body: 'It leaves the clip list for good, even when you change the sliders. Your stream recording and any file already rendered stay where they are. You can undo this right away.' })) return
    try { await api(Ok, '/api/clip_remove', { pid: x.pid, id: x.id }) } catch (e) { toast(err(e), 'err'); return }
    stopTrying()
    const i = clipsRef.current.findIndex(y => keyOf(y) === keyOf(x))
    const rest = clipsRef.current.filter(y => keyOf(y) !== keyOf(x))
    setClips(rest)
    const nx = rest[i] ?? rest[i - 1]
    setCur(nx && keyOf(nx))
    epoch.current++; void queue.refresh()
    toast(`Clip #${x.id} removed.`, '', [{ label: 'Undo', fn: async () => {
      try {
        const r = await api(Restored, '/api/clip_restore', { pid: x.pid, id: x.id })
        if (r.clip) setClips(cs => (cs.some(y => keyOf(y) === keyOf(x)) ? cs : [...cs.slice(0, Math.max(0, i)), prep(r.clip!), ...cs.slice(Math.max(0, i))]))
        setCur(keyOf(x))
      } catch (e) { toast(err(e), 'err') }
    } }])
  }
  // what's heard while playing: muted or bleeped where a fix is (or one being tried), as it will render
  useEffect(() => {
    let raf = 0, actx: AudioContext | null = null, osc: OscillatorNode | null = null
    const beep = (on: boolean) => {
      if (on && !osc) {
        try {
          actx ??= new AudioContext()
          osc = actx.createOscillator(); const g = actx.createGain()
          g.gain.value = 0.06 * (player.current?.level() ?? 1); osc.frequency.value = 1000; osc.connect(g).connect(actx.destination); osc.start()
        } catch { osc = null }
      } else if (!on && osc) { try { osc.stop() } catch { /* already stopped */ } osc = null }
    }
    const tick = () => {
      raf = requestAnimationFrame(tick)
      const vid = getVideo(), x = clipsRef.current.find(y => keyOf(y) === cur), userMuted = player.current?.userMuted() ?? false
      if (!vid || !x) return
      const tr = trying.current
      if (tr) {                                        // a fix being tried plays its stretch, then stops
        if (!vid.paused) tr.started = true
        if (vid.currentTime >= tr.to || (vid.paused && tr.started)) stopTrying()
      }
      if (vid.paused) { beep(false); if (vid.muted !== userMuted) vid.muted = userMuted; return }
      const p = tr && proposalRef.current
      const hit = fixSpans(x, p ? { span: p.span, kind: p.kind } : null).find(([a, b]) => vid.currentTime >= a && vid.currentTime <= b)
      const m = userMuted || !!hit
      if (vid.muted !== m) vid.muted = m
      beep(!!hit && hit[2] === 'bleep' && !userMuted)
    }
    raf = requestAnimationFrame(tick)
    return () => { cancelAnimationFrame(raf); beep(false); void actx?.close() }
  }, [cur, getVideo])
  const proposalRef = useRef(proposal)
  proposalRef.current = proposal
  useEffect(() => { stopTrying(); setProposal(null) }, [cur])        // another clip: nothing being tried
  useEffect(() => { setExact(null) }, [cur, opts, free])              // what the exact preview showed has changed

  /** Exact preview: a few seconds around the playhead rendered by the real export pipeline. */
  async function exactPreview(x: Clip) {
    if (exactStep) return
    const clip: Record<string, unknown> = { id: x.id, start: x.start, end: x.end, title: x.title.trim() }
    if (x.wordsEdited) clip.words = x.words.filter(w => w.start >= x.start - 0.2 && w.start < x.end)
    setExactStep('Getting ready…')
    try {
      const { job } = await api(JobStarted, '/api/preview_render', { pid: x.pid, clip, opts, at: getVideo()?.currentTime ?? x.start })
      for (;;) {
        const j = await api(Job, `/api/job/${job}`)
        if (j.status === 'done') {
          const r = j.result as { url: string; start: number; end: number }
          if (keyOf(x) === curRef.current) { getVideo()?.pause(); setExact(r) }
          break
        }
        if (j.status !== 'running') { if (j.status === 'error') toast(j.error || 'The exact preview stopped.', 'err'); break }
        setExactStep(j.step === 'Rendering' ? `Rendering ${Math.round(j.progress * 100)}%` : 'Getting ready…')
        await new Promise(res => setTimeout(res, 500))
      }
    } catch (e) { toast(err(e), 'err') }
    setExactStep('')
  }
  const curRef = useRef(cur)
  curRef.current = cur

  // the UI test's way in (tests/test_ui.py), the same as the original page's window.ClipRadarTest
  const probe = useRef<{ hits: () => Hits; width: () => number } | null>(null)
  const exp_ = useRef(exp), camps_ = useRef(camps)
  exp_.current = exp
  camps_.current = camps
  useEffect(() => {
    const t = {
      clips: () => clipsRef.current.map(x => ({ state: x.state, quality: x.quality, gem: isGem(x) })),
      open: () => ({ kind: isLive ? 'live' : 'vod', id: pid }),
      current: () => curRef.current ?? null,
      setOption: (k: string, v: unknown) => setOpts({ [k]: v } as Partial<RenderOpts>),
      setTodoQualities: (qs: number[]) => {   // qs[i] is the score for the i-th clip still to review (gems left out)
        let i = 0
        setClips(cs => cs.map(x => (x.state === 'todo' && !isGem(x) && i < qs.length ? { ...x, quality: qs[i++]! } : x)))
      },
      reloadExport: () => Promise.all([exp_.current.refresh(), camps_.current.refresh()]),
      hitBox: (name: string) => probe.current?.hits()[name] ?? null,
      previewWidth: () => probe.current?.width() ?? 0,
      volume: () => {
        const v = getVideo(), b = document.querySelector('[data-testid=mute-btn]')
        return { vol: v?.volume ?? 0, muted: v?.muted ?? false, level: b?.getAttribute('data-level') ?? '',
          ex: Math.min(1, Math.max(0, Number(getPref('volume', 1)) || 0)) }
      },
    }
    ;(window as unknown as { ClipRadarTest?: unknown }).ClipRadarTest = t
    return () => { if ((window as unknown as { ClipRadarTest?: unknown }).ClipRadarTest === t) delete (window as unknown as { ClipRadarTest?: unknown }).ClipRadarTest }
  }, [pid, isLive, setOpts, getVideo])

  useEffect(() => {                                  // the original page's Review keys
    function onKey(e: KeyboardEvent) {
      if (!active) return                            // on another screen: Enter or X there mustn't keep or skip a clip
      if (document.querySelector('[role=alertdialog],[role=dialog]')) return
      const typing = !!(e.target as HTMLElement).closest?.('input,select,textarea'), mod = e.ctrlKey || e.metaKey
      if (mod && !typing && ['z', 'Z', 'y'].includes(e.key)) { e.preventDefault(); undoRedo(e.key === 'y' || e.shiftKey); return }
      if (typing || mod || e.altKey) return
      const k = e.key, vid = getVideo()
      if (c && vid && free == null) {                // the editor's keys
        const hit = k === 'i' || k === 'I' ? (trim(c, vid.currentTime, c.end, 'set start'), true)
          : k === 'o' || k === 'O' ? (trim(c, c.start, vid.currentTime, 'set end'), true)
          : k === ',' ? (stepFrame(-1), true) : k === '.' ? (stepFrame(1), true)
          : k === '<' || k === '>' ? (changeSpeed(SPEEDS[clamp(SPEEDS.indexOf(speed) + (k === '<' ? -1 : 1), 0, SPEEDS.length - 1)]!), true)
          : k === '[' ? ((vid.currentTime = c.start), true) : k === ']' ? ((vid.currentTime = Math.max(c.start, c.end - 0.05)), true)
          : k === '+' || k === '=' ? (timeline.current?.zoom(0.5), true) : k === '-' || k === '_' ? (timeline.current?.zoom(2), true)
          : k === '0' ? (timeline.current?.fit(), true) : false
        if (hit) { e.preventDefault(); return }
      }
      if (k === ' ') { e.preventDefault(); player.current?.toggle() }
      else if (k === 'ArrowDown' || k === 'j') { e.preventDefault(); step(1) }
      else if (k === 'ArrowUp' || k === 'k') { e.preventDefault(); step(-1) }
      else if (k === 'Enter' && !(e.target as HTMLElement).closest?.('button,a')) { e.preventDefault(); void keep() }
      else if (k === 'x' || k === 'Delete') void skip()
      else if (k === 'ArrowLeft') player.current?.seekBy(-2)
      else if (k === 'ArrowRight') player.current?.seekBy(2)
      else if (k === 't') nextTitle()
      else if (k === 'm') player.current?.toggleMute()
      else if ((k === 'c' || k === 'C') && isLive) void clipNow()
      else if (k === 'Escape') setExact(null)
    }
    addEventListener('keydown', onKey)
    return () => removeEventListener('keydown', onKey)
  })

  if (failed) return (
    <section className="rv-pick"><h1>Review</h1><p className="banner err" role="alert">{failed}</p>
      <p className="muted">Open the video again from <a href="#home">Home › Recent</a>: your keeps, skips and edits come back with it.</p></section>
  )
  if (!meta) return <section className="rv-pick"><h1>Review</h1><p className="muted">Opening…</p></section>

  // a fix being tried shows in the preview: the title as it would be, the words masked where they would be
  const shown = c && proposal?.playing ? {
    ...c, ...(proposal.kind === 'mask_title' ? { title: String(proposal.view.title ?? c.title) } : {}),
    ...(proposal.span ? { check: { excluded: false, ...c.check, fixes: [...(c.check?.fixes ?? []), { span: proposal.span, kind: proposal.kind }] } } : {}),
  } : c
  const st = c && checkStatus(c, safety?.enabled !== false)
  const kept = c?.state === 'kept'
  const keepLabel = !c ? '' : kept ? (q?.status === 'error' ? 'Retry render' : q?.status === 'done' ? 'Rendered' : 'In render queue') : 'Keep and render'
  const done = clips.filter(x => x.state === 'kept').length
  return (
    <section className="review" aria-labelledby="rv-title" data-testid="review">
      <header className="rv-head">
        <h1 id="rv-title" className="ellipsis" title={meta.title}>{meta.title}</h1>
        {live ? <LiveHead live={live} moments={clips.length} onSettings={b => void liveSettings(b)} onClip={() => void clipNow()} onStop={() => void stopLive()} />
          : <>
            <HeadFacts meta={meta} onDetect={() => setDetOpen(true)} />
            <DeepBox deep={deep} gems={counts.gems} onStart={() => void startDeep()} onStop={() => void stopDeep()} />
          </>}
        {listening && clips.some(x => !x.listened) && <span className="muted small" data-testid="rv-listening">Writing titles ({clips.filter(x => x.listened).length} of {clips.length})</span>}
      </header>

      <div className="rv-grid">
        <aside className="rv-list" aria-label="Clips">
          <div className="seg" role="tablist" aria-label="Which clips">
            {([['todo', 'To review', counts.todo], ['skipped', 'Skipped', counts.skipped], ['all', 'All', counts.all],
              ...(counts.gems ? [['gems', 'Gems', counts.gems]] : []),
              ...(counts.flagged || filter === 'flagged' ? [['flagged', 'Flagged', counts.flaggedOpen]] : [])] as Array<[Filter, string, number]>).map(([f, l, n]) => (
              <button key={f} role="tab" aria-selected={filter === f} className={filter === f ? 'on' : ''} onClick={() => setFilter(f)}>
                {l} <span className="tnum muted">{n}</span>
              </button>
            ))}
          </div>
          {filter === 'todo' && counts.todo > 0 && (
            <div className="rv-batch" data-testid="batch-bar">
              <label className="fld inline">Keep the top
                <select className="input" value={batchN} onChange={e => setBatchN(e.target.value)} data-testid="batch-count">
                  {['3', '5', '10'].map(n => <option key={n} value={n}>{n}</option>)}<option value="0">all</option>
                </select>
              </label>
              <button className="btn sm" disabled={busy} onClick={() => void batchKeep()} data-testid="batch-keep">Keep and render</button>
              <label className="fld inline">Skip
                <select className="input" value={under} onChange={e => setUnder(e.target.value)} data-testid="skip-score">
                  <option value="0">all left</option>{['40', '50', '60'].map(n => <option key={n} value={n}>scored under {n}</option>)}
                </select>
              </label>
              <button className="btn sm ghost" onClick={() => void batchSkip()} data-testid="batch-skip">Skip</button>
            </div>
          )}
          {filter === 'flagged' && (
            <div className="rv-batch" data-testid="rv-flagbar">
              <select className="input" aria-label="Show flagged clips by status" value={flagFilter} onChange={e => setFlagFilter(e.target.value)}>
                <option value="all">All flagged</option><option value="needs_review">Needs review</option><option value="reviewed">Reviewed</option>
                <option value="corrected">Corrected</option><option value="dismissed">Dismissed</option><option value="excluded">Excluded from export</option>
              </select>
              <button className="btn sm" title="Leave every listed clip as it is" onClick={() => void flagBatch('leave', {}, 'Leave as is', 'The clips stay exactly as they are and their open flags are marked as reviewed. You can undo it on each clip.')}>Leave all</button>
              <button className="btn sm" title="Dismiss the open flags on every listed clip" onClick={() => void flagBatch('dismiss', {}, 'Dismiss flags', "Their open flags are dismissed (they come back if the words change or the rules do). Dismissing doesn't make a clip safe.")}>Dismiss all</button>
              <button className="btn sm" title="Keep every listed clip out of rendering" onClick={() => void flagBatch('exclude', { on: true }, 'Exclude from export', "They stay in the project but won't render until you include them again. Unfinished renders of them are taken out of the queue.")}>Exclude all</button>
            </div>
          )}
          {list.length ? (
            <ul className="rv-rows" data-testid="rv-rows">
              {list.map(x => {
                const xq = queueFor(x, items)
                const state = x.state === 'kept' ? (xq?.status === 'error' ? 'Render failed' : xq?.status === 'done' ? 'Rendered'
                  : xq?.status === 'rendering' ? 'Rendering' : xq ? 'In render queue' : 'Kept') : x.state === 'skipped' ? 'Skipped' : ''
                const flagged = checkStatus(x, safety?.enabled !== false) === 'needs_review'
                return (
                  <li key={keyOf(x)}>
                    <button className={`rv-row${keyOf(x) === cur ? ' on' : ''}`} aria-current={keyOf(x) === cur} onClick={() => select(x)} data-testid="rv-row">
                      <img src={x.thumb} alt="" loading="lazy" />
                      <span className="rv-row-main">
                        <span className={`rv-row-t ellipsis${x.listened || x.userTitle ? '' : ' pending'}`}>{flagged && <span className="rv-flag" title="Content check: needs review">!</span>}{x.title || 'Untitled'}</span>
                        <span className="rv-row-m muted small">
                          {x.quality > 0 && <span className={`qpill q${qClass(x.quality)}`} title={`Score ${x.quality} of 100`}>{x.quality}</span>}
                          <span>#{x.id}</span><span>{fmt(x.start)}</span><span>{Math.round(x.end - x.start)}s</span>
                          {state && <span className={xq?.status === 'error' ? 'err-tx' : ''}>{state}</span>}
                        </span>
                      </span>
                    </button>
                  </li>
                )
              })}
            </ul>
          ) : (
            <p className="muted small rv-empty">{!clips.length ? 'Nothing stood out in this video.' : filter === 'todo' ? 'Nothing left to review.'
              : filter === 'skipped' ? 'No skipped clips.' : filter === 'flagged' ? (flagFilter === 'all' ? 'Nothing flagged.' : 'No flagged clips with this status.') : 'No clips here.'}</p>
          )}
        </aside>

        <div className={`rv-main${c && free == null ? ' has-insp' : ''}`}>
          {free != null ? (
            <>
              <Player ref={player} src={meta.vod} pid={pid} clipKey={`free:${free}`} start={free} end={meta.duration} />
              <div className="rv-free" data-testid="rv-free">
                <p>Previewing <b className="tnum" data-testid="rv-free-at">{fmt(now || free)}</b> of the stream.</p>
                <div className="row wrap">
                  <button className="btn primary" onClick={() => void addHere()} data-testid="rv-add-here">Make a clip here</button>
                  <button className="btn" onClick={() => select(c ?? list[0] ?? clips[0])}>Back to clips</button>
                </div>
              </div>
            </>
          ) : c ? (
            <>
              <div className="rv-stagecol">
              <Player ref={player} src={c.vod || meta.vod} pid={c.pid} clipKey={keyOf(c)} start={c.start} end={c.end}>
                <Preview clip={shown ?? c} video={getVideo} thumb={c.thumb} opts={opts} marks={marks} safety={safety} replace={replace}
                         track={tracks[trackKey(c, opts)] ?? null} guides={guides}
                         onOpts={(patch, persist) => setOpts(patch, persist)}
                         onMark={(i, patch, persist) => {
                           const ms = (dragMarks ?? wmServer?.marks ?? []).map((m, j) => (j === i ? { ...m, ...patch } : m))
                           setDragMarks(ms)
                           if (persist) void saveMarks(ms)
                         }}
                         onToggle={() => player.current?.toggle()} probe={probe}>
                  {exact && (
                    <div className="rv-exact">
                      <video src={exact.url} autoPlay loop playsInline muted={player.current?.userMuted() ?? false} data-testid="rv-exact" />
                      <button className="rv-exact-close" onClick={() => setExact(null)} title="Back to the live preview (Esc)">
                        Exact preview {fmtA(exact.start)} to {fmtA(exact.end)} · close</button>
                    </div>
                  )}
                </Preview>
              </Player>

              <div className="rv-editor" data-testid="rv-editor">
                <div className="row wrap rv-edbar">
                  <button className="btn sm" disabled={!hist.undo.length} onClick={() => undoRedo(false)}
                          title={hist.undo.length ? `Undo ${hist.undo.at(-1)!.label} (Ctrl+Z)` : 'Nothing to undo'}>Undo</button>
                  <button className="btn sm" disabled={!hist.redo.length} onClick={() => undoRedo(true)}
                          title={hist.redo.length ? `Redo ${hist.redo.at(-1)!.label} (Ctrl+Shift+Z)` : 'Nothing to redo'}>Redo</button>
                  <button className="btn sm ghost" onClick={() => stepFrame(-1)} title="One frame back (,)">‹ Frame</button>
                  <button className="btn sm ghost" onClick={() => stepFrame(1)} title="One frame on (.)">Frame ›</button>
                  <select className="input" aria-label="Playback speed" title="Playback speed (< and >)" value={speed} onChange={e => changeSpeed(Number(e.target.value))}>
                    {SPEEDS.map(v => <option key={v} value={v}>{v}×</option>)}
                  </select>
                  <button className="btn sm" disabled={!!exactStep} onClick={() => void exactPreview(c)} data-testid="rv-exact-btn"
                          title="Render a few seconds around the playhead exactly like the export">{exactStep || 'Exact preview'}</button>
                  <span className="grow" />
                  <button className="btn sm ghost" onClick={() => timeline.current?.zoom(2)} aria-label="Zoom out (-)">−</button>
                  <button className="btn sm ghost" onClick={() => timeline.current?.zoom(0.5)} aria-label="Zoom in (+)">+</button>
                  <button className="btn sm ghost" onClick={() => timeline.current?.fit()} title="The whole clip (0)">Fit</button>
                </div>
                <Timeline ref={timeline} clip={c} dur={isLive ? getVideo()?.duration || c.end + 30 : meta.duration} video={getVideo} locked={kept}
                          onDrag={(start, end) => {
                            dragFrom.current ??= snapOf(c)
                            update(keyOf(c), y => ({ ...y, start, end, userTrim: true }), 'no')
                          }}
                          onDragEnd={which => {
                            const before = dragFrom.current
                            dragFrom.current = undefined
                            const x = clipsRef.current.find(y => keyOf(y) === keyOf(c))
                            if (!before || !x) return
                            setHist(h => push(h, keyOf(x), which === 's' ? 'move start' : 'move end', before, snapOf(x)))
                            save(x)
                            afterTrim(x)
                          }} />
                <div className="rv-trim" aria-label="Trim">
                  {(['s', 'e'] as const).map(w => (
                    <div key={w} className="rv-trim-row">
                      <span className="rv-trim-l">{w === 's' ? 'Start' : 'End'}</span>
                      <span className="tnum" data-testid={w === 's' ? 'rv-start' : 'rv-end'}>{fmtA(w === 's' ? c.start : c.end)}</span>
                      <span className="rv-trim-btns">
                        {([['-1', '−1s', '1 second earlier'], ['-f', '−1f', '1 frame earlier'], ['f', '+1f', '1 frame later'], ['1', '+1s', '1 second later'],
                          ['now', 'Set', `${w === 's' ? 'Start' : 'End'} where the playhead is (${w === 's' ? 'I' : 'O'})`]] as const).map(([v, l, t]) => (
                          <button key={v} className="btn sm" disabled={kept} title={t} aria-label={`${w === 's' ? 'Start' : 'End'}: ${t}`}
                                  onClick={() => nudge(w, v === 'now' || v === 'f' || v === '-f' ? v : Number(v))}>{l}</button>
                        ))}
                      </span>
                    </div>
                  ))}
                  <div className="row wrap muted small">
                    <span data-testid="rv-len">{(c.end - c.start).toFixed(2)} s{c.orig && (Math.abs(c.orig[0] - c.start) > 0.01 || Math.abs(c.orig[1] - c.end) > 0.01)
                      ? ` · found as ${(c.orig[1] - c.orig[0]).toFixed(1)} s` : ''}</span>
                    {!kept && c.orig && (Math.abs(c.orig[0] - c.start) > 0.01 || Math.abs(c.orig[1] - c.end) > 0.01) &&
                      <button className="link" onClick={() => restore(c)} title="Go back to the cut Clip Radar found">Restore original cut</button>}
                    <span className="grow" />
                    <label className="fld inline" title="Only this clip. With Even out volume on, this moves this clip's level up or down from the others.">Volume
                      <select className="input" value={c.gain} disabled={kept} data-testid="rv-gain"
                              onChange={e => update(keyOf(c), y => ({ ...y, gain: Number(e.target.value) }))}>
                        {([[-9, 'Much quieter'], [-6, 'Quieter'], [-3, 'A bit quieter'], [0, 'As is'], [3, 'A bit louder'], [6, 'Louder']] as const)
                          .map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                      </select>
                    </label>
                  </div>
                </div>
              </div>
              </div>
              {/* the inspector, on the right as in the previous UI: decide, then change what's needed, without scrolling past the player */}
              <aside className="rv-insp" aria-label="This clip" data-testid="rv-insp">
              <div className="row wrap rv-facts">
                  <b>{isGem(c) ? `Quiet gem #${c.id}` : `Clip #${c.id}`}</b>
                  <span className="muted">{fmtA(c.start)} to {fmtA(c.end)} ({(c.end - c.start).toFixed(1)}s)</span>
                  {c.quality > 0 && <span className={`qpill q${qClass(c.quality)}`}>Score {c.quality}</span>}
                  {st && st !== 'clean' && st !== 'off' && <span className={st === 'needs_review' || st === 'excluded' ? 'warn-tx' : 'muted'}>{CHECK_LABEL[st]}</span>}
                </div>
              <div className="rv-acts">
                  <div className="row">
                    <button className="btn ghost" onClick={() => step(-1)} aria-label="Previous clip (Up)">Previous</button>
                    <button className="btn ghost" onClick={() => step(1)} aria-label="Next clip (Down)">Next</button>
                  </div>
                  <div className="rv-decide">
                    <button className="btn" onClick={() => void skip()} disabled={kept && q?.status === 'done'} data-testid="rv-skip">
                      {kept ? 'Remove from queue' : c.state === 'skipped' ? 'Move back to review' : 'Skip'}
                    </button>
                    <button className="btn primary" onClick={() => void keep()} disabled={kept && q?.status !== 'error'} data-testid="rv-keep">{keepLabel}</button>
                  </div>
                </div>
              <div className="seg rv-tabs" role="tablist" aria-label="Clip, text or layout">
                {([['clip', 'Clip'], ['captions', 'Captions'], ['text', 'Text'], ['layout', 'Layout']] as const).map(([v, l]) => (
                  <button key={v} role="tab" aria-selected={tab === v} className={tab === v ? 'on' : ''} onClick={() => setTab(v)}>{l}</button>
                ))}
              </div>
              {tab === 'captions' && <CaptionsPanel clip={c} opts={opts} whisper={whisper} now={now}
                    onEdit={(i, text) => { if (kept) return; const p = editLine(c, i, text, opts); if (p) edit(c, text.trim() ? 'caption edit' : 'caption deleted', p) }}
                    onAdd={() => { const vid = getVideo(); if (!kept && vid) edit(c, 'caption added', addLine(c, vid.currentTime, opts)) }}
                    onReset={() => { edit(c, 'caption reset', resetLines(c)); toast('Captions are back to what Whisper heard.') }}
                    onSeek={t => { const vid = getVideo(); if (vid) { vid.pause(); vid.currentTime = clamp(t, c.start, c.end - 0.05) } }} />}
              {tab === 'text' && <TextPanel opts={opts} set={setOpts} capPresets={exp.data?.prefs.cap_presets ?? []} whisper={whisper}
                                            guides={guides} onGuides={v => { setGuides(v); setPref('guides', v) }} onRelisten={relisten}
                                            onSavePresets={async l => { await savePresets('cap_presets')(l).catch(e => toast(err(e), 'err')); await exp.refresh() }} />}
              {tab === 'layout' && <LayoutPanel opts={opts} set={setOpts} pid={c.pid} frameAt={c.start + (c.end - c.start) * 0.6}
                                                framePresets={exp.data?.prefs.frame_presets ?? []} trackNote={trackNote(tracks[trackKey(c, opts)])}
                                                onSavePresets={async l => { await savePresets('frame_presets')(l).catch(e => toast(err(e), 'err')); await exp.refresh() }} />}
              <div className="rv-details" data-testid="rv-details" hidden={tab !== 'clip'}>
                <ContentCheck clip={c} safety={safety} flaggedTab={filter === 'flagged'} proposal={proposal}
                              onAct={(a, args) => void checkAct(c, a, args)} onPropose={(fp, k) => void propose(c, fp, k)}
                              onPreview={() => tryFix(c)} onCancel={() => { stopTrying(); setProposal(null) }}
                              onApply={() => { const p = proposal; stopTrying(); setProposal(null); if (p) void checkAct(c, 'fix', { fp: p.fp, kind: p.kind }) }}
                              onSeek={t => { const vid = getVideo(); stopTrying(); if (vid) { vid.currentTime = clamp(t, c.start, c.end - 0.1); void vid.play().catch(() => {}) } }}
                              onEdit={() => {
                                const f = (c.safety?.flags ?? []).find(x => (x as { source?: string }).source === 'speech' && x.status === 'open') as { start?: number } | undefined
                                setTab('captions')
                                const vid = getVideo()
                                if (vid && f) { vid.pause(); vid.currentTime = clamp(c.start + (f.start ?? 0), c.start, c.end - 0.05) }
                                toast("Fix the words here, or trim the clip. Your video isn't changed, and Undo all edits brings the captions back.")
                              }}
                              onRemove={() => void removeClip(c)} />


                {c.reason && <p className="muted small">{c.reason}</p>}
                {kept && q?.status === 'error' && <p className="notice err" role="alert"><b>Render failed.</b>&nbsp;{q.error}</p>}
                {c.held && !kept && <p className="notice"><b>{c.held}.</b>&nbsp;Hands-free didn't render this one. Watch it, then keep or skip it.</p>}
                <label className="fld">Title
                  <span className="row">
                    <input className="input grow" value={c.title} disabled={kept} maxLength={120} data-testid="rv-title-input"
                           onChange={e => edit(c, 'title change', { title: e.target.value, userTitle: true }, 'title')} />
                    <button className="btn sm" disabled={kept || c.titles.length < 2} onClick={nextTitle} title="Another suggested title (T)">Another title</button>
                  </span>
                </label>
                <TitleChoices clip={c} name={meta.streamer_name} kept={kept} onName={isLive ? undefined : setStreamer}
                              onPick={(t, k) => edit(c, `title ${k}`, { title: t, userTitle: true })} />
                <label className="fld">Post caption
                  <textarea className="input" rows={3} value={postFor(c, hashtagsOn)} disabled={kept}
                            onChange={e => edit(c, 'post caption change', { userPost: true, postText: e.target.value }, 'post')} />
                </label>
                <div className="row wrap">
                  <label className="fld inline" title="Add hashtags to post captions (all clips)">
                    <input type="checkbox" checked={hashtagsOn} data-testid="rv-tags-on" onChange={async e => {
                      try { await api(TitlePrefs, '/api/titles', { hashtags: e.target.checked }); await titlesPoll.refresh() } catch (x) { toast(err(x), 'err') }
                    }} />Hashtags
                  </label>
                  <button className="btn sm ghost" onClick={() => navigator.clipboard.writeText(postFor(c, hashtagsOn)).then(() => toast('Post caption copied.'), () => toast(postFor(c, hashtagsOn)))}>Copy post caption</button>
                </div>

                <p className="muted small">Keys: Enter keep · X skip · ↑ ↓ clips · Space play · ← → 2 s · T another title · M mute · I / O start / end here · , . frame · [ ] clip edges · Ctrl+Z undo</p>
              </div>
              </aside>
            </>
          ) : (
            <div className="rv-done" data-testid="rv-done">
              <p>{clips.length ? `${plural(done, 'clip')} kept, ${counts.skipped} skipped.` : 'Nothing stood out in this video.'}</p>
              <div className="row wrap">
                {counts.skipped > 0 && <button className="btn" onClick={() => { setFilter('skipped'); select(visible(clips, 'skipped')[0]) }}>Review the skipped ones</button>}
                {!isLive && <button className="btn" onClick={suggestMore} data-testid="rv-more">Suggest 5 more</button>}
                {done > 0 && <a className="btn primary" href="#exports">Go to Exports</a>}
              </div>
            </div>
          )}
        </div>
      </div>
      <Overview clips={clips} curve={meta.curve} dur={meta.duration} cur={cur} free={free != null} span={span}
                {...(live ? { live: { recording: live.status === 'recording' } } : {})}
                playhead={() => { const v = getVideo(); return v && (free != null || c) ? v.currentTime + (c && free == null ? span(c)[0] - c.start : 0) : null }}
                onClip={x => { const f = filterFor(x, filter); if (f !== filter) setFilter(f); select(x) }}
                onPreview={t => (isLive ? toast("In live mode, use Clip last 30s to grab something that isn't highlighted.") : previewAt(t))} />
      {detOpen && <DetectDialog meta={meta} count={String(getPref('count2', '0'))} length={String(getPref('length2', '0'))} cw={Number(getPref('cw', 60))}
                                onClose={() => setDetOpen(false)}
                                onTry={async (d, w, n, l) => {
                                  try { return await api(Compared, '/api/repick', { pid, count: Number(n), length: Number(l), chat_weight: w, detect: d, dry_run: true }) }
                                  catch (e) { toast(err(e), 'err'); return null }
                                }}
                                onApply={async (d, w, n, l) => {
                                  setDetOpen(false)
                                  if (await repick(d, n, l, w)) toast('Detection settings applied. Your kept, skipped and edited clips stayed.')
                                }} />}
    </section>
  )
}
