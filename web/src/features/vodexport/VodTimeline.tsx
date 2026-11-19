import { canvasScale } from '../../shell'
import { forwardRef, useEffect, useImperativeHandle, useRef, type PointerEvent, type WheelEvent } from 'react'
import { api } from '../../api/client'
import { Strip } from '../../api/schemas'
import { clamp, fmtA, rulerStep } from '../review/edit'
import { fmt } from '../review/model'
import { glide, wheelPixels, zoomFactor } from '../review/Timeline'

/** A stretch drawn on the timeline: a music stretch, a segment, a cut, the part picked to send to Segments. */
export interface Box {
  id: string; start: number; end: number
  lane: 0 | 1                          // 0: the main row (music, segments), 1: the row under it (cuts, the picked part)
  kind: 'voice' | 'mute' | 'keep' | 'seg' | 'off' | 'cut' | 'pick'
  label?: string
  edit?: boolean                       // its edges can be dragged
}
export interface Tick { t: number; label?: string }
export interface VodTimelineHandle { zoom: (f: number) => void; show: (a: number, b: number) => void; fit: () => void }

const H = 104
const ROW = { ruler: 14, wave: [17, 41], lane0: [45, 75], lane1: [79, 101] } as const
const MIN_BOX = 0.5                     // seconds: a stretch can't be dragged shorter than this
const EDGE_PX = 7
const reduceMotion = () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches

/** The edge or body of a box under the pointer: the selected box first, then the one drawn on top. */
export function hitBox(boxes: Box[], t: number, lane: 0 | 1 | -1, secPerPx: number, sel?: string):
  { box: Box; part: 's' | 'e' | 'body' } | null {
  const near = EDGE_PX * secPerPx
  const order = [...boxes].sort((a, b) => Number(b.id === sel) - Number(a.id === sel))
  for (const b of order) {
    if (!b.edit) continue
    if (Math.abs(t - b.start) <= near && Math.abs(t - b.start) <= Math.abs(t - b.end)) return { box: b, part: 's' }
    if (Math.abs(t - b.end) <= near) return { box: b, part: 'e' }
  }
  for (const b of order) if (b.lane === lane && t >= b.start && t <= b.end) return { box: b, part: 'body' }
  return null
}

/** A dragged edge: inside the video, never past the other edge. */
export function dragEdge(b: Pick<Box, 'start' | 'end'>, part: 's' | 'e', t: number, dur: number): { start: number; end: number } {
  if (part === 's') return { start: +clamp(t, 0, b.end - MIN_BOX).toFixed(2), end: b.end }
  return { start: b.start, end: +clamp(t, b.start + MIN_BOX, dur).toFixed(2) }
}

/** Export VOD's and Segments' timeline, the way Review's works: the stream's time with its loudness, the stretches
 *  on it, wheel to scroll, Ctrl+wheel to zoom, and it follows the playhead while the video plays. Click to move the
 *  playhead, click a stretch to pick it, drag an edge to move it, Shift+drag to mark a new one. */
export const VodTimeline = forwardRef<VodTimelineHandle, {
  pid: string; dur: number; video: () => HTMLVideoElement | null
  boxes: Box[]; ticks?: Tick[]; sel?: string | undefined
  label: string                                         // what the timeline shows, for screen readers
  onSeek?: (t: number) => void
  onPick?: (id: string) => void
  onBox?: (id: string, start: number, end: number) => void
  onBoxDone?: (id: string) => void
  onCreate?: (start: number, end: number) => void
}>(function VodTimeline(props, ref) {
  const cv = useRef<HTMLCanvasElement>(null)
  const view = useRef({ a: 0, b: Math.min(props.dur || 300, 300) })
  const goal = useRef({ ...view.current })
  const data = useRef<(Strip & { pid: string }) | undefined>(undefined)
  const fetching = useRef(false)
  const tried = useRef('')
  const drag = useRef<{ kind: 'seek' | 'edge' | 'new'; id?: string; part?: 's' | 'e'; from?: number; to?: number; moved: boolean }>(undefined)
  const hover = useRef<{ id: string; part: 's' | 'e' } | null>(null)
  const p = useRef(props)
  p.current = props

  function go(a: number, b: number, now = false) {
    const d = Math.max(1, p.current.dur), span = clamp(b - a, 2, d)
    const na = clamp(a, 0, Math.max(0, d - span))
    goal.current = { a: na, b: na + span }
    if (now || reduceMotion()) view.current = { ...goal.current }
    void fetchStrip()
  }
  async function fetchStrip() {
    const a = Math.min(view.current.a, goal.current.a), b = Math.max(view.current.b, goal.current.b)
    const { pid, dur } = p.current, have = data.current
    if (fetching.current || (have && have.pid === pid && have.a <= a && have.b >= Math.min(b, dur - 0.5))) return
    const margin = Math.max(0, Math.min(b - a, (3600 - (b - a)) / 2))
    const fa = Math.max(0, a - margin), fb = Math.max(b + margin, fa + 1)
    fetching.current = true
    try { data.current = { ...(await api(Strip, `/api/strip/${pid}?a=${fa.toFixed(2)}&b=${fb.toFixed(2)}`)), pid } }
    catch { data.current = { pid, a: fa, b: fb, hz: 1, wave: [], events: {}, chat: [], music: [], start: Math.floor(fa) } }
    fetching.current = false
  }
  function zoom(f: number, at?: number) {
    const { a, b } = view.current, span = b - a, gs = goal.current.b - goal.current.a
    const t = p.current.video()?.currentTime ?? (a + b) / 2
    const c = at ?? (t >= a && t <= b ? t : (a + b) / 2)
    const ns = clamp(gs * f, 2, Math.max(2, p.current.dur))
    go(c - ns * ((c - a) / span), c - ns * ((c - a) / span) + ns)
  }
  useImperativeHandle(ref, () => ({
    zoom: f => zoom(f),
    show: (a, b) => { const pad = Math.max(1, (b - a) * 0.1); go(a - pad, b + pad) },
    fit: () => go(0, p.current.dur),
  }))
  useEffect(() => { go(0, Math.min(props.dur || 300, 300), true) }, [props.pid])     // a new video: its first five minutes

  useEffect(() => {
    let raf = 0, last = performance.now()
    const css = { accent: '', warn: '', danger: '' }
    const draw = (stamp: number) => {
      raf = requestAnimationFrame(draw)
      const dt = Math.min(100, stamp - last)
      last = stamp
      const el = cv.current, g = el?.getContext('2d')
      if (!el || !g) return
      const { boxes, ticks = [], sel, dur, pid } = p.current
      const dpr = canvasScale(), w = Math.max(100, el.clientWidth)
      if (el.width !== Math.round(w * dpr)) { el.width = Math.round(w * dpr); el.height = Math.round(H * dpr) }
      g.setTransform(dpr, 0, 0, dpr, 0, 0)
      if (view.current.a !== goal.current.a || view.current.b !== goal.current.b) view.current = glide(view.current, goal.current, dt, w)
      if (!css.accent) {
        const st = getComputedStyle(el)
        css.accent = st.getPropertyValue('--accent').trim() || '#2fe36f'
        css.warn = st.getPropertyValue('--warn').trim() || '#ffb020'
        css.danger = st.getPropertyValue('--danger').trim() || '#ff5c5c'
      }
      const { a, b } = view.current, span = b - a
      el.dataset.a = a.toFixed(2); el.dataset.b = b.toFixed(2)          // the stretch shown, for tests
      const x = (t: number) => (t - a) / span * w
      const vid = p.current.video()
      const now = vid?.currentTime ?? 0
      // follow the playhead while it plays, a page at a time; a jump far off screen isn't glided
      const gl = goal.current, gs = gl.b - gl.a
      if (vid && !vid.paused && !drag.current && (now > gl.b - gs * 0.02 || now < gl.a)) go(now - gs * 0.1, now + gs * 0.9, now < a || now > b + span * 0.5)
      g.fillStyle = '#0e0f11'; g.fillRect(0, 0, w, H)
      const step = rulerStep(span, w)
      g.font = '10px Inter, sans-serif'; g.textBaseline = 'top'; g.fillStyle = '#90939c'; g.strokeStyle = '#26272c'; g.lineWidth = 1
      for (let t = Math.ceil(a / step) * step; t <= b; t += step) {
        const px = Math.round(x(t)) + 0.5
        g.beginPath(); g.moveTo(px, ROW.ruler - 4); g.lineTo(px, H); g.stroke()
        g.fillText(step < 1 ? fmtA(t).replace(/0$/, '') : fmt(t), px + 3, 1)
      }
      const have = data.current, want = `${pid}:${Math.round(a)}:${Math.round(b)}`
      if (!fetching.current && tried.current !== want && (!have || have.pid !== pid || have.a > a + 0.5 || have.b < Math.min(b, dur - 0.5))) {
        tried.current = want                                           // once per stretch shown: a short answer isn't asked for again
        void fetchStrip()
      }
      const d = data.current
      if (d && d.pid === pid && d.music.length) {                      // how much it sounded like music (the scan)
        const [y0, y1] = ROW.wave
        g.fillStyle = css.accent; g.globalAlpha = 0.28
        d.music.forEach((v, i) => {
          if (v < 0.15) return
          const x0 = x(d.start + i), x1 = x(d.start + i + 1)
          if (x1 < 0 || x0 > w) return
          const hgt = v * (y1 - y0)
          g.fillRect(x0, y1 - hgt, Math.max(1, x1 - x0), hgt)
        })
        g.globalAlpha = 1
      }
      if (d && d.pid === pid && d.wave.length && span <= 3600) {      // loudness
        const [y0, y1] = ROW.wave, mid = (y0 + y1) / 2, amp = (y1 - y0) / 2, hz = d.hz || 1, base = d.hz === 1 ? d.start : d.a
        g.fillStyle = '#3a3d46'
        for (let px = 0; px < w; px++) {
          const i0 = Math.floor((a + px / w * span - base) * hz), i1 = Math.max(i0 + 1, Math.ceil((a + (px + 1) / w * span - base) * hz))
          let m = -90
          for (let i = Math.max(0, i0); i < Math.min(d.wave.length, i1); i++) m = Math.max(m, d.wave[i]!)
          if (m <= -89) continue
          const v = clamp((m + 60) / 60, 0.02, 1) * amp
          g.fillRect(px, mid - v, 1, v * 2)
        }
      }
      for (const lane of [0, 1] as const) {                          // the stretches, the selected one on top
        const [y0, y1] = lane ? ROW.lane1 : ROW.lane0
        for (const bx of [...boxes].filter(q => q.lane === lane).sort((q, r) => Number(q.id === sel) - Number(r.id === sel))) {
          if (bx.end < a || bx.start > b) continue
          const x0 = x(bx.start), x1 = Math.max(x0 + 2, x(bx.end)), on = bx.id === sel
          const fill = { voice: css.accent, mute: css.warn, keep: '', seg: '#3a3d46', off: '', cut: css.danger, pick: '#e9eaed' }[bx.kind]
          g.globalAlpha = bx.kind === 'pick' ? 0.14 : bx.kind === 'cut' ? 0.35 : bx.kind === 'seg' ? 1 : 0.5
          if (fill) { g.fillStyle = fill; g.fillRect(x0, y0, x1 - x0, y1 - y0) }
          g.globalAlpha = 1
          g.strokeStyle = on ? css.accent : bx.kind === 'pick' ? '#e9eaed' : bx.kind === 'keep' || bx.kind === 'off' ? '#90939c' : fill || '#90939c'
          g.lineWidth = on ? 2 : 1
          if (bx.kind === 'off' || bx.kind === 'keep') g.setLineDash([4, 3])
          g.strokeRect(x0 + 0.5, y0 + 0.5, x1 - x0 - 1, y1 - y0 - 1)
          g.setLineDash([])
          if (bx.label && x1 - x0 > 30) {
            g.save(); g.beginPath(); g.rect(x0 + 3, y0, x1 - x0 - 6, y1 - y0); g.clip()
            g.font = '11px Inter, sans-serif'; g.textBaseline = 'middle'; g.fillStyle = '#e9eaed'
            g.fillText(bx.label, Math.max(x0, 0) + 5, (y0 + y1) / 2); g.restore()
          }
          if (bx.edit) for (const [px, part] of [[x0, 's'], [x1, 'e']] as const) {     // grips
            const hot = (hover.current?.id === bx.id && hover.current.part === part) || (drag.current?.id === bx.id && drag.current.part === part)
            if (!on && !hot) continue
            g.fillStyle = hot ? '#9cc0ff' : css.accent
            g.fillRect(px - (part === 's' ? 0 : 5), y0, 5, y1 - y0)
            if (hot) {
              const lbl = fmtA(part === 's' ? bx.start : bx.end)
              g.font = '600 10.5px Inter, sans-serif'
              const tw = g.measureText(lbl).width + 8, lx = clamp(px - tw / 2, 0, w - tw)
              g.fillStyle = '#e9eaed'; g.fillRect(lx, ROW.wave[0], tw, 14); g.fillStyle = '#0e0f11'; g.textBaseline = 'middle'; g.fillText(lbl, lx + 4, ROW.wave[0] + 7)
            }
          }
        }
      }
      g.fillStyle = css.danger                                         // flagged words
      for (const tk of ticks) if (tk.t >= a && tk.t <= b) g.fillRect(Math.round(x(tk.t)) - 1, ROW.wave[0], 2, ROW.lane0[1] - ROW.wave[0])
      const dr = drag.current
      if (dr?.kind === 'new' && dr.from != null && dr.to != null) {   // the stretch being marked
        const x0 = x(Math.min(dr.from, dr.to)), x1 = x(Math.max(dr.from, dr.to))
        g.fillStyle = 'rgba(233,234,237,.18)'; g.fillRect(x0, ROW.lane0[0], x1 - x0, ROW.lane0[1] - ROW.lane0[0])
        g.strokeStyle = '#e9eaed'; g.lineWidth = 1; g.strokeRect(x0 + 0.5, ROW.lane0[0] + 0.5, x1 - x0 - 1, ROW.lane0[1] - ROW.lane0[0] - 1)
      }
      const ph = x(now)                                                // the playhead
      if (ph >= 0 && ph <= w) {
        g.fillStyle = '#fff'; g.fillRect(Math.round(ph) - 1, ROW.ruler - 4, 2, H)
        g.beginPath(); g.moveTo(ph - 5, ROW.ruler - 4); g.lineTo(ph + 5, ROW.ruler - 4); g.lineTo(ph, ROW.ruler + 2); g.fill()
      }
      if (dur > 0) {                                                    // where this is in the whole video
        g.fillStyle = '#26272c'; g.fillRect(0, H - 2, w, 2)
        g.fillStyle = '#90939c'; g.fillRect(a / dur * w, H - 2, Math.max(2, span / dur * w), 2)
      }
    }
    raf = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(raf)
  }, [])

  const pos = (e: PointerEvent | WheelEvent) => {
    const r = cv.current!.getBoundingClientRect(), { a, b } = view.current
    const y = e.clientY - r.top
    const lane: 0 | 1 | -1 = y >= ROW.lane0[0] && y <= ROW.lane0[1] ? 0 : y >= ROW.lane1[0] ? 1 : -1
    return { w: r.width, t: clamp(a + (e.clientX - r.left) / r.width * (b - a), 0, p.current.dur), lane, spp: (b - a) / r.width }
  }
  const seek = (t: number) => {
    const vid = p.current.video()
    if (vid) vid.currentTime = t
    p.current.onSeek?.(t)
  }
  function down(e: PointerEvent) {
    if (e.button !== 0) return
    const { t, lane, spp } = pos(e)
    cv.current!.setPointerCapture?.(e.pointerId)
    if (e.shiftKey && p.current.onCreate) { drag.current = { kind: 'new', from: t, to: t, moved: false }; return }
    const hit = hitBox(p.current.boxes, t, lane, spp, p.current.sel)
    if (hit && hit.part !== 'body') {
      p.current.video()?.pause()
      drag.current = { kind: 'edge', id: hit.box.id, part: hit.part, moved: false }
      if (hit.box.id !== p.current.sel) p.current.onPick?.(hit.box.id)
      return
    }
    if (hit) p.current.onPick?.(hit.box.id)
    drag.current = { kind: 'seek', moved: false }
    seek(t)
  }
  function move(e: PointerEvent) {
    const { t, lane, spp } = pos(e), dr = drag.current
    if (!dr) {
      const hit = hitBox(p.current.boxes, t, lane, spp, p.current.sel)
      hover.current = hit && hit.part !== 'body' ? { id: hit.box.id, part: hit.part } : null
      cv.current!.style.cursor = hover.current ? 'ew-resize' : e.shiftKey && p.current.onCreate ? 'crosshair' : 'text'
      return
    }
    dr.moved = true
    if (dr.kind === 'seek') return seek(t)
    if (dr.kind === 'new') { dr.to = t; return }
    const bx = p.current.boxes.find(q => q.id === dr.id)
    if (!bx || !dr.part) return
    const nb = dragEdge(bx, dr.part, t, p.current.dur)
    p.current.onBox?.(bx.id, nb.start, nb.end)
    seek(dr.part === 's' ? nb.start : Math.max(nb.start, nb.end - 0.05))
  }
  function up() {
    const dr = drag.current
    drag.current = undefined
    if (!dr) return
    if (dr.kind === 'new' && dr.from != null && dr.to != null && Math.abs(dr.to - dr.from) >= MIN_BOX) {
      p.current.onCreate?.(+Math.min(dr.from, dr.to).toFixed(2), +Math.max(dr.from, dr.to).toFixed(2))
    }
    if (dr.kind === 'edge' && dr.moved && dr.id) p.current.onBoxDone?.(dr.id)
  }
  function wheel(e: WheelEvent) {
    const { w, t } = pos(e), mv = wheelPixels(e, w)
    if (e.ctrlKey || e.metaKey) return zoom(zoomFactor(mv.y), t)
    const { a, b } = goal.current, span = b - a
    const na = a + (Math.abs(mv.x) > Math.abs(mv.y) ? mv.x : mv.y) / w * span
    go(na, na + span)
  }
  useEffect(() => {                                      // a wheel listener that can stop the page scrolling
    const el = cv.current
    if (!el) return
    const stop = (e: globalThis.WheelEvent) => e.preventDefault()
    el.addEventListener('wheel', stop, { passive: false })
    return () => el.removeEventListener('wheel', stop)
  }, [])
  return (
    <canvas ref={cv} className="vodx-timeline" height={H} data-testid="vodx-timeline" role="img"
            aria-label={`${props.label}. Scroll to move along it, Ctrl and scroll to zoom. The buttons below do the same.`}
            onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up}
            onPointerLeave={() => { hover.current = null }} onWheel={wheel} />
  )
})

/** The zoom buttons under a timeline (for the keyboard, and for anyone without a wheel). */
export function ZoomButtons({ tl, onFit }: { tl: { current: VodTimelineHandle | null }; onFit?: () => void }) {
  return (
    <span className="row vodx-zoom">
      <button className="btn sm ghost" aria-label="Zoom in" onClick={() => tl.current?.zoom(0.5)}>+</button>
      <button className="btn sm ghost" aria-label="Zoom out" onClick={() => tl.current?.zoom(2)}>−</button>
      <button className="btn sm ghost" onClick={() => (onFit ? onFit() : tl.current?.fit())}>Whole video</button>
    </span>
  )
}
