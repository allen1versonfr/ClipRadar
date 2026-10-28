import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../../api/client'
import { type Clip, ClipsUpdate, Ok, Project, Queue, type QueueItem, Safety, Sources, TitlePrefs } from '../../api/schemas'
import { useConfirm } from '../../components/Confirm'
import { useToast } from '../../components/Toasts'
import { plural } from '../../format'
import { usePoll } from '../../hooks/usePoll'
import { renderOpts } from '../../prefs'
import { CHECK_LABEL, checkStatus, type Filter, fmt, gate, isGem, keepBody, keyOf, merge, nextTodo, openFlags, postFor,
  prep, qClass, queueFor, snapshot, visible } from './model'
import { Player, type PlayerHandle } from './Player'

const err = (e: unknown) => (e instanceof Error ? e.message : String(e))
const save = (c: Clip) => { api(Ok, '/api/clip_state', snapshot(c)).catch(() => {}) }
const feedback = (c: Clip, action: string, extra: object = {}) => { api(Ok, '/api/feedback', { pid: c.pid, clip: c.id, action, ...extra }).catch(() => {}) }

/** Review: #review lists the videos open now; #review/<project> reviews one. */
export function ReviewView({ pid }: { pid: string | undefined }) {
  return pid ? <Workspace key={pid} pid={pid} /> : <Picker />
}

function Picker() {
  const src = usePoll(useCallback(() => api(Sources, '/api/sources'), []), 5000)
  const ready = (src.data?.items ?? []).filter(x => x.type === 'vod' && x.status === 'done' && x.pid)
  return (
    <section className="rv-pick" data-testid="review-picker">
      <h1>Review</h1>
      {ready.length ? (
        <ul className="rv-open">
          {ready.map(x => <li key={x.id}><a className="btn" href={`#review/${x.pid}`}>{x.title || x.source}</a></li>)}
        </ul>
      ) : <p className="muted">No video is open. Find clips in a video on <a href="#home">Home</a>, then review them here.</p>}
    </section>
  )
}

function Workspace({ pid }: { pid: string }) {
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
  const clipsRef = useRef(clips)
  clipsRef.current = clips
  const saveT = useRef(new Map<string, ReturnType<typeof setTimeout>>())      // typing: saved after a pause, per clip

  const safety = usePoll(useCallback(() => api(Safety, '/api/safety'), []), 60_000).data
  const titles = usePoll(useCallback(() => api(TitlePrefs, '/api/titles'), []), 60_000).data
  const hashtagsOn = titles?.hashtags !== false
  // the render queue: a keep or remove since a request left means its answer is out of date
  const epoch = useRef(0)
  const queue = usePoll(useCallback(async () => ({ e: epoch.current, q: await api(Queue, '/api/queue') }), []), 2000)
  const items: QueueItem[] = queue.data?.q.items ?? []

  const update = useCallback((k: string, f: (c: Clip) => Clip, persist: 'now' | 'soon' | 'no' = 'now') => {
    setClips(cs => cs.map(c => {
      if (keyOf(c) !== k) return c
      const n = f(c)
      if (persist === 'now') save(n)
      else if (persist === 'soon') { clearTimeout(saveT.current.get(k)); saveT.current.set(k, setTimeout(() => save(n), 600)) }
      return n
    }))
  }, [])

  useEffect(() => {                                  // open the video, and start writing titles
    api(Project, `/api/project/${pid}`).then(p => {
      const cs = p.clips.concat(p.gems).map(prep)
      setMeta(p)
      setClips(cs)
      const first = cs.find(c => c.state === 'todo' && !isGem(c))
      setCur(first && keyOf(first))
      if (p.restored) toast(`Picked up where you left off: ${plural(p.review.kept, 'clip')} kept, ${p.review.skipped} skipped, ${p.review.todo} to review.`)
      else if (p.pick_note) toast(p.pick_note)
      const o = renderOpts()
      api(Ok, '/api/listen', { pid, order: cs.map(c => c.id), model: o.whisper_model, language: o.language })
        .then(() => setListening(true)).catch(() => {})
    }).catch(e => setFailed(err(e)))
  }, [pid, toast])

  useEffect(() => {                                  // titles, captions and checks arrive in the background
    if (!listening) return
    const t = setInterval(async () => {
      if (document.hidden) return
      try {
        const r = await api(ClipsUpdate, `/api/clips/${pid}`)
        const by = new Map(r.clips.concat(r.gems).map(s => [keyOf(s), s]))
        setClips(cs => cs.map(c => { const s = by.get(keyOf(c)); return s ? merge(c, s) : c }))
        setListening(r.listening)
      } catch { /* the next tick tries again */ }
    }, 1200)
    return () => clearInterval(t)
  }, [listening, pid])

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

  const list = useMemo(() => visible(clips, filter), [clips, filter])
  const c = clips.find(x => keyOf(x) === cur)
  const q = c && queueFor(c, items)
  const counts = useMemo(() => {
    const hype = clips.filter(x => !isGem(x))
    return { todo: hype.filter(x => x.state === 'todo').length, skipped: hype.filter(x => x.state === 'skipped').length,
      all: hype.length, gems: clips.length - hype.length }
  }, [clips])

  function select(x: Clip | undefined) { setCur(x && keyOf(x)) }
  function step(dir: 1 | -1) {
    const i = list.findIndex(x => keyOf(x) === cur)
    select(i < 0 ? list[0] : list[i + dir] ?? list[i])
  }
  function after(x: Clip) { select(nextTodo(clipsRef.current, x)) }

  async function queueKeep(x: Clip, quiet = false, sound = true): Promise<boolean> {
    epoch.current++
    try {
      await api(Ok, '/api/queue/add', keepBody(x, postFor(x, hashtagsOn), renderOpts(), sound))
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
    update(keyOf(c), y => ({ ...y, title: y.titles[(i + 1) % y.titles.length]!, userTitle: true }))
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

  useEffect(() => {                                  // the original page's Review keys
    function onKey(e: KeyboardEvent) {
      if (document.querySelector('[role=alertdialog]') || (e.target as HTMLElement).closest?.('input,select,textarea')) return
      if (e.ctrlKey || e.metaKey || e.altKey) return
      const k = e.key
      if (k === ' ') { e.preventDefault(); player.current?.toggle() }
      else if (k === 'ArrowDown' || k === 'j') { e.preventDefault(); step(1) }
      else if (k === 'ArrowUp' || k === 'k') { e.preventDefault(); step(-1) }
      else if (k === 'Enter' && !(e.target as HTMLElement).closest?.('button,a')) { e.preventDefault(); void keep() }
      else if (k === 'x' || k === 'Delete') void skip()
      else if (k === 'ArrowLeft') player.current?.seekBy(-2)
      else if (k === 'ArrowRight') player.current?.seekBy(2)
      else if (k === 't') nextTitle()
      else if (k === 'm') player.current?.toggleMute()
    }
    addEventListener('keydown', onKey)
    return () => removeEventListener('keydown', onKey)
  })

  if (failed) return (
    <section className="rv-pick"><h1>Review</h1><p className="banner err" role="alert">{failed}</p>
      <p className="muted">Open the video again from <a href="#home">Home › Recent</a>: your keeps, skips and edits come back with it.</p></section>
  )
  if (!meta) return <section className="rv-pick"><h1>Review</h1><p className="muted">Opening…</p></section>

  const st = c && checkStatus(c, safety?.enabled !== false)
  const kept = c?.state === 'kept'
  const keepLabel = !c ? '' : kept ? (q?.status === 'error' ? 'Retry render' : q?.status === 'done' ? 'Rendered' : 'In render queue') : 'Keep and render'
  const done = clips.filter(x => x.state === 'kept').length
  return (
    <section className="review" aria-labelledby="rv-title" data-testid="review">
      <header className="rv-head">
        <h1 id="rv-title" className="ellipsis" title={meta.title}>{meta.title}</h1>
        <span className="muted small">
          {fmt(meta.duration)} long · {!meta.has_chat ? 'No chat, ranked by audio' : meta.chat_stats.chatters
            ? `${meta.chat_stats.chatters.toLocaleString()} chatters` : `${meta.chat_count.toLocaleString()} chat messages`}
          {meta.detect_text && ` · ${meta.detect_text}`}
        </span>
        {listening && clips.some(x => !x.listened) && <span className="muted small" data-testid="rv-listening">Writing titles ({clips.filter(x => x.listened).length} of {clips.length})</span>}
      </header>

      <div className="rv-grid">
        <aside className="rv-list" aria-label="Clips">
          <div className="seg" role="tablist" aria-label="Which clips">
            {([['todo', 'To review', counts.todo], ['skipped', 'Skipped', counts.skipped], ['all', 'All', counts.all],
              ...(counts.gems ? [['gems', 'Gems', counts.gems]] : [])] as Array<[Filter, string, number]>).map(([f, l, n]) => (
              <button key={f} role="tab" aria-selected={filter === f} className={filter === f ? 'on' : ''} onClick={() => setFilter(f)}>
                {l} <span className="tnum muted">{n}</span>
              </button>
            ))}
          </div>
          {filter === 'todo' && counts.todo > 0 && (
            <div className="rv-batch" data-testid="rv-batch">
              <label className="fld inline">Keep the top
                <select className="input" value={batchN} onChange={e => setBatchN(e.target.value)} data-testid="rv-batch-count">
                  {['3', '5', '10'].map(n => <option key={n} value={n}>{n}</option>)}<option value="0">all</option>
                </select>
              </label>
              <button className="btn sm" disabled={busy} onClick={() => void batchKeep()} data-testid="rv-batch-keep">Keep and render</button>
              <label className="fld inline">Skip
                <select className="input" value={under} onChange={e => setUnder(e.target.value)} data-testid="rv-skip-score">
                  <option value="0">all left</option>{['40', '50', '60'].map(n => <option key={n} value={n}>scored under {n}</option>)}
                </select>
              </label>
              <button className="btn sm ghost" onClick={() => void batchSkip()} data-testid="rv-batch-skip">Skip</button>
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
              : filter === 'skipped' ? 'No skipped clips.' : 'No clips here.'}</p>
          )}
        </aside>

        <div className="rv-main">
          {c ? (
            <>
              <Player ref={player} src={c.vod || meta.vod} pid={pid} start={c.start} end={c.end} />
              <div className="rv-details" data-testid="rv-details">
                <div className="row wrap rv-facts">
                  <b>{isGem(c) ? `Quiet gem #${c.id}` : `Clip #${c.id}`}</b>
                  <span className="muted">{fmt(c.start)} to {fmt(c.end)} ({(c.end - c.start).toFixed(1)}s)</span>
                  {c.quality > 0 && <span className={`qpill q${qClass(c.quality)}`}>Score {c.quality}</span>}
                  {st && st !== 'clean' && st !== 'off' && <span className={st === 'needs_review' || st === 'excluded' ? 'warn-tx' : 'muted'}>{CHECK_LABEL[st]}</span>}
                </div>
                <div className="row wrap rv-acts">
                  <button className="btn ghost" onClick={() => step(-1)} aria-label="Previous clip (Up)">Previous</button>
                  <button className="btn ghost" onClick={() => step(1)} aria-label="Next clip (Down)">Next</button>
                  <span className="grow" />
                  <button className="btn" onClick={() => void skip()} disabled={kept && q?.status === 'done'} data-testid="rv-skip">
                    {kept ? 'Remove from queue' : c.state === 'skipped' ? 'Move back to review' : 'Skip'}
                  </button>
                  <button className="btn primary" onClick={() => void keep()} disabled={kept && q?.status !== 'error'} data-testid="rv-keep">{keepLabel}</button>
                </div>
                {c.reason && <p className="muted small">{c.reason}</p>}
                {kept && q?.status === 'error' && <p className="notice err" role="alert"><b>Render failed.</b>&nbsp;{q.error}</p>}
                {c.held && !kept && <p className="notice"><b>{c.held}.</b>&nbsp;Hands-free didn't render this one. Watch it, then keep or skip it.</p>}
                <label className="fld">Title
                  <span className="row">
                    <input className="input grow" value={c.title} disabled={kept} maxLength={120} data-testid="rv-title-input"
                           onChange={e => update(keyOf(c), y => ({ ...y, title: e.target.value, userTitle: true }), 'soon')} />
                    <button className="btn sm" disabled={kept || c.titles.length < 2} onClick={nextTitle} title="Another suggested title (T)">Another title</button>
                  </span>
                </label>
                <label className="fld">Post caption
                  <textarea className="input" rows={3} value={postFor(c, hashtagsOn)} disabled={kept}
                            onChange={e => update(keyOf(c), y => ({ ...y, userPost: true, postText: e.target.value }), 'soon')} />
                </label>
                <div className="row wrap">
                  <button className="btn sm ghost" onClick={() => navigator.clipboard.writeText(postFor(c, hashtagsOn)).then(() => toast('Post caption copied.'), () => toast(postFor(c, hashtagsOn)))}>Copy post caption</button>
                  <a className="btn sm ghost" href={`/legacy/#open=${pid}/${c.id}`} title="Trim, captions, text style and layout are still in the current UI">Edit in the current UI</a>
                </div>

                <p className="muted small">Keys: Enter keep · X skip · ↑ ↓ clips · Space play · ← → 2 s · T another title · M mute</p>
              </div>
            </>
          ) : (
            <div className="rv-done" data-testid="rv-done">
              <p>{clips.length ? `${plural(done, 'clip')} kept, ${counts.skipped} skipped.` : 'Nothing stood out in this video.'}</p>
              <div className="row wrap">
                {counts.skipped > 0 && <button className="btn" onClick={() => { setFilter('skipped'); select(visible(clips, 'skipped')[0]) }}>Review the skipped ones</button>}
                {done > 0 && <a className="btn primary" href="#exports">Go to Exports</a>}
              </div>
            </div>
          )}
        </div>
      </div>
    </section>
  )
}
