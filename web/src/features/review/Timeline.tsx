import { canvasScale } from '../../shell'
import { forwardRef, useEffect, useImperativeHandle, useRef, type PointerEvent, type WheelEvent } from 'react'
import { api } from '../../api/client'
import { type Clip, Strip } from '../../api/schemas'
import { clamp, fmtA, MIN_LEN, rulerStep, snap } from './edit'
import { fmt, keyOf } from './model'

export interface TimelineHandle { zoom: (f: number) => void; fit: () => void }

const H = 92
const LANE = { wave: [16, 42], words: [45, 62], ev: [65, 89] } as const
const EVENT_COLOURS: Record<string, string> = { laugh: '#4fd1ff', scream: '#ff9f43', cheer: '#c9a7ff' }   // kept apart from the green accent

/** The editing timeline (the original page's drawEditor): stream time, loudness, the words heard,
 *  laughter / screams / cheers and chat, with the clip framed. Drag an edge to trim (it snaps to words
 *  and the playhead; Alt doesn't), click to move the playhead, wheel to pan, Ctrl+wheel to zoom. */
export const Timeline = forwardRef<TimelineHandle, {
  clip: Clip; dur: number; video: () => HTMLVideoElement | null; locked: boolean
  onDrag: (start: number, end: number) => void
  onDragEnd: (which: 's' | 'e') => void
}>(function Timeline({ clip, dur, video, locked, onDrag, onDragEnd }, ref) {
  const cv = useRef<HTMLCanvasElement>(null)
  const view = useRef({ a: 0, b: 60, key: '' })
  const data = useRef<Strip & { pid: string }>(undefined)
  const fetching = useRef(false)
  const drag = useRef<{ which: 's' | 'e' | 'seek'; moved: boolean }>(undefined)
  const hover = useRef<'s' | 'e' | null>(null)
  const props = useRef({ clip, dur, locked, onDrag })
  props.current = { clip, dur, locked, onDrag }

  async function fetchStrip() {
    const { clip: c, dur: d } = props.current, { a, b } = view.current, span = b - a, have = data.current
    if (fetching.current || (have && have.pid === c.pid && have.a <= a && have.b >= Math.min(b, d - 0.5))) return
    const fa = Math.max(0, a - span), fb = b + span
    fetching.current = true
    try { data.current = { ...(await api(Strip, `/api/strip/${c.pid}?a=${fa.toFixed(2)}&b=${fb.toFixed(2)}`)), pid: c.pid } }
    catch { data.current = { pid: c.pid, a: fa, b: fb, hz: 10, wave: [], events: {}, chat: [], start: Math.floor(fa) } }
    fetching.current = false
  }
  function fit() {
    const { clip: c, dur: d } = props.current
    const pad = Math.max(2, (c.end - c.start) * 0.15)
    const a = Math.max(0, c.start - pad), b = Math.max(Math.min(d, c.end + pad), Math.min(d, a + 2))
    view.current = { a, b, key: keyOf(c) }
    void fetchStrip()
  }
  function zoom(f: number, at?: number) {
    const { a, b } = view.current, span = b - a, d = props.current.dur
    const t = video()?.currentTime ?? (a + b) / 2
    const c = at ?? (t >= a && t <= b ? t : (a + b) / 2)
    const ns = clamp(span * f, 1.5, Math.min(d, 1800))
    const na = clamp(c - ns * ((c - a) / span), 0, Math.max(0, d - ns))
    view.current = { ...view.current, a: na, b: na + ns }
    void fetchStrip()
  }
  useImperativeHandle(ref, () => ({ zoom: f => zoom(f), fit }))

  useEffect(() => {                                      // drawn every frame while on screen: the playhead moves
    let raf = 0
    const draw = () => {
      raf = requestAnimationFrame(draw)
      const el = cv.current, g = el?.getContext('2d')
      if (!el || !g) return
      const { clip: c, locked: lk } = props.current
      if (view.current.key !== keyOf(c)) fit()
      const dpr = canvasScale(), w = Math.max(100, el.clientWidth)
      if (el.width !== Math.round(w * dpr)) { el.width = Math.round(w * dpr); el.height = Math.round(H * dpr) }
      g.setTransform(dpr, 0, 0, dpr, 0, 0)
      const { a, b } = view.current, span = b - a
      el.dataset.a = a.toFixed(3); el.dataset.b = b.toFixed(3)          // the stretch shown, for tests
      const x = (t: number) => (t - a) / span * w
      const vid = video()
      const now = vid?.currentTime ?? c.start
      // follow the playhead while playing
      if (vid && !vid.paused && !drag.current && (now > b - span * 0.02 || now < a)) {
        const na = clamp(now - span * 0.1, 0, Math.max(0, props.current.dur - span))
        view.current = { ...view.current, a: na, b: na + span }
        void fetchStrip()
      }
      g.fillStyle = '#0e0f11'; g.fillRect(0, 0, w, H)
      const st = rulerStep(span, w)                      // ruler
      g.font = '10px Inter, sans-serif'; g.textBaseline = 'top'; g.fillStyle = '#90939c'; g.strokeStyle = '#26272c'; g.lineWidth = 1
      for (let t = Math.ceil(a / st) * st; t <= b; t += st) {
        const px = Math.round(x(t)) + 0.5
        g.beginPath(); g.moveTo(px, 10); g.lineTo(px, H); g.stroke()
        g.fillText(st < 1 ? fmtA(t).replace(/0$/, '') : fmt(t), px + 3, 1)
      }
      const d = data.current
      if (d && d.pid === c.pid && d.wave.length) {       // loudness
        const [y0, y1] = LANE.wave, mid = (y0 + y1) / 2, amp = (y1 - y0) / 2, hz = d.hz || 10, base = d.hz === 1 ? d.start : d.a
        g.fillStyle = '#3a3d46'
        for (let px = 0; px < w; px++) {
          const ta = a + px / w * span, tb = a + (px + 1) / w * span
          const i0 = Math.floor((ta - base) * hz), i1 = Math.max(i0 + 1, Math.ceil((tb - base) * hz))
          let m = -90
          for (let i = Math.max(0, i0); i < Math.min(d.wave.length, i1); i++) m = Math.max(m, d.wave[i]!)
          if (m <= -89) continue
          const v = clamp((m + 60) / 60, 0.02, 1) * amp
          g.fillRect(px, mid - v, 1, v * 2)
        }
      }
      const [wy0, wy1] = LANE.words                      // the words heard
      g.font = '10.5px Inter, sans-serif'; g.textBaseline = 'middle'
      for (const wd of c.words) {
        if (wd.end < a || wd.start > b) continue
        const x0 = x(wd.start), x1 = Math.max(x0 + 2, x(wd.end))
        g.fillStyle = '#25324a'; g.fillRect(x0, wy0, x1 - x0 - 1, wy1 - wy0)
        if (x1 - x0 > 18) {
          g.save(); g.beginPath(); g.rect(x0 + 2, wy0, x1 - x0 - 4, wy1 - wy0); g.clip()
          g.fillStyle = '#c6d4f0'; g.fillText(wd.word.trim(), x0 + 3, (wy0 + wy1) / 2); g.restore()
        }
      }
      const [ey0, ey1] = LANE.ev                         // laughter, screams, cheers; chat
      if (d && d.pid === c.pid) {
        for (const [k, arr] of Object.entries(d.events)) {
          g.fillStyle = EVENT_COLOURS[k] ?? '#888'
          arr.forEach((v, i) => {
            if (v < 0.12) return
            const x0 = x(d.start + i), x1 = x(d.start + i + 1)
            if (x1 < 0 || x0 > w) return
            g.globalAlpha = clamp(v, 0.2, 0.9); g.fillRect(x0, ey0 + 3, Math.max(1, x1 - x0), ey1 - ey0 - 6)
          })
        }
        g.globalAlpha = 1
        if (d.chat.length) {
          g.strokeStyle = getComputedStyle(el).getPropertyValue('--accent').trim() || '#2fe36f'; g.lineWidth = 1.2; g.beginPath()
          d.chat.forEach((v, i) => { const px = x(d.start + i + 0.5), py = ey1 - 2 - clamp(v, 0, 8) / 8 * (ey1 - ey0 - 4); if (i) g.lineTo(px, py); else g.moveTo(px, py) })
          g.stroke()
        }
      }
      // outside the clip dimmed, the clip framed, the cut Clip Radar found dashed
      const xs = x(c.start), xe = x(c.end)
      g.fillStyle = 'rgba(14,15,17,.62)'; g.fillRect(0, 12, Math.max(0, xs), H); g.fillRect(xe, 12, Math.max(0, w - xe), H)
      const orig = c.orig
      if (orig && (Math.abs(orig[0] - c.start) > 0.01 || Math.abs(orig[1] - c.end) > 0.01)) {
        g.setLineDash([3, 3]); g.strokeStyle = '#90939c'
        for (const t of orig) { const px = Math.round(x(t)) + 0.5; g.beginPath(); g.moveTo(px, 14); g.lineTo(px, H); g.stroke() }
        g.setLineDash([])
      }
      const acc = lk ? '#90939c' : getComputedStyle(el).getPropertyValue('--accent').trim() || '#2fe36f'
      g.strokeStyle = acc; g.lineWidth = 1.5; g.strokeRect(xs, 13, xe - xs, H - 14)
      for (const [px, which] of [[xs, 's'], [xe, 'e']] as const) {
        const hot = hover.current === which || drag.current?.which === which
        g.fillStyle = hot ? '#9cc0ff' : acc; g.fillRect(px - (which === 's' ? 0 : 6), 13, 6, H - 14)
        if (hot) {
          const label = fmtA(which === 's' ? c.start : c.end)
          g.font = '600 10.5px Inter, sans-serif'
          const tw = g.measureText(label).width + 8, lx = clamp(px - tw / 2, 0, w - tw)
          g.fillStyle = '#e9eaed'; g.fillRect(lx, 14, tw, 14); g.fillStyle = '#0e0f11'; g.textBaseline = 'middle'; g.fillText(label, lx + 4, 21)
        }
      }
      const ph = x(now)                                  // the playhead
      if (ph >= 0 && ph <= w) {
        g.fillStyle = '#fff'; g.fillRect(Math.round(ph) - 1, 10, 2, H - 10)
        g.beginPath(); g.moveTo(ph - 5, 10); g.lineTo(ph + 5, 10); g.lineTo(ph, 16); g.fill()
      }
    }
    raf = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(raf)
  }, [video])

  const pos = (e: PointerEvent | WheelEvent) => {
    const r = cv.current!.getBoundingClientRect(), { a, b } = view.current
    return { px: e.clientX - r.left, w: r.width, t: a + (e.clientX - r.left) / r.width * (b - a) }
  }
  const edgeAt = (px: number, w: number): 's' | 'e' | null => {
    const { a, b } = view.current, c = props.current.clip
    const xs = (c.start - a) / (b - a) * w, xe = (c.end - a) / (b - a) * w
    if (Math.abs(px - xs) <= 8 && Math.abs(px - xs) <= Math.abs(px - xe)) return 's'
    return Math.abs(px - xe) <= 8 ? 'e' : null
  }
  function down(e: PointerEvent) {
    if (e.button !== 0) return
    const { px, w, t } = pos(e), which = props.current.locked ? null : edgeAt(px, w), vid = video()
    if (which) { drag.current = { which, moved: false }; vid?.pause() }
    else {
      drag.current = { which: 'seek', moved: false }
      if (vid) vid.currentTime = clamp(t, 0, props.current.dur)
    }
    cv.current!.setPointerCapture?.(e.pointerId)
  }
  function move(e: PointerEvent) {
    const { px, w, t: raw } = pos(e), dr = drag.current, { clip: c, dur: d } = props.current, vid = video()
    if (!dr) {
      hover.current = props.current.locked ? null : edgeAt(px, w)
      cv.current!.style.cursor = hover.current ? 'ew-resize' : 'text'
      return
    }
    const t = clamp(raw, 0, d)
    if (dr.which === 'seek') { if (vid) vid.currentTime = t; return }
    const v = snap(t, c.words, vid?.currentTime ?? t, (view.current.b - view.current.a) / w, e.altKey)
    dr.moved = true
    if (dr.which === 's') { const s = +clamp(v, 0, c.end - MIN_LEN).toFixed(3); props.current.onDrag(s, c.end); if (vid) vid.currentTime = s }
    else { const en = +clamp(v, c.start + MIN_LEN, d).toFixed(3); props.current.onDrag(c.start, en); if (vid) vid.currentTime = Math.max(c.start, en - 0.04) }
  }
  function up() {
    const dr = drag.current
    drag.current = undefined
    if (dr && dr.which !== 'seek' && dr.moved) onDragEnd(dr.which)
  }
  function wheel(e: WheelEvent) {
    const { w, t } = pos(e), { a, b } = view.current, span = b - a, d = props.current.dur
    if (e.ctrlKey || e.metaKey) return zoom(e.deltaY > 0 ? 1.25 : 0.8, t)
    const dt = (Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY) / w * span
    const na = clamp(a + dt, 0, Math.max(0, d - span))
    view.current = { ...view.current, a: na, b: na + span }
    void fetchStrip()
  }
  useEffect(() => {                                      // a wheel listener that can stop the page scrolling
    const el = cv.current
    if (!el) return
    const stop = (e: globalThis.WheelEvent) => e.preventDefault()
    el.addEventListener('wheel', stop, { passive: false })
    return () => el.removeEventListener('wheel', stop)
  }, [])
  return (
    <canvas ref={cv} className="rv-timeline" height={H} data-testid="rv-timeline"
            role="img" aria-label="Timeline: drag the clip's edges to trim, click to move the playhead. The Start and End buttons do the same."
            onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up}
            onPointerLeave={() => { hover.current = null }} onWheel={wheel} />
  )
})
