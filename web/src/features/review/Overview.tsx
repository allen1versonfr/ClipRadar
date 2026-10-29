import { useEffect, useRef, useState } from 'react'
import type { Clip } from '../../api/schemas'
import { fmt, isGem, keyOf } from './model'
import { clipAt, heatLevel, markers } from './overview'

const accent = (el: Element) => getComputedStyle(el).getPropertyValue('--accent').trim() || '#2fe36f'
const rgba = (hex: string, a: number) => { const n = parseInt(hex.slice(1), 16); return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})` }

/** The whole stream under the player: how hyped each moment was, and where the clips are. Click a clip to
 *  open it, anywhere else to preview that moment (the original page's timeline dock). */
export function Overview({ clips, curve, dur, cur, free, playhead, onClip, onPreview }: {
  clips: Clip[]; curve: number[]; dur: number; cur: string | undefined; free: boolean
  playhead: () => number | null; onClip: (c: Clip) => void; onPreview: (t: number) => void
}) {
  const ref = useRef<HTMLCanvasElement>(null)
  const [hover, setHover] = useState<{ x: number; text: string } | null>(null)
  const args = useRef({ clips, curve, dur, cur, free, playhead })
  args.current = { clips, curve, dur, cur, free, playhead }
  const dirty = useRef(true)
  dirty.current = true                               // what's drawn changed: drawn on the next frame

  useEffect(() => {
    let raf = 0, last = -1
    const draw = () => {
      const el = ref.current
      if (!el) return
      const { clips, curve, dur, cur, free, playhead } = args.current
      const ph = playhead()
      const Wd = el.clientWidth, Ht = el.clientHeight
      if (!Wd || !Ht) return
      if (!dirty.current && (ph ?? -1) === last) return
      dirty.current = false; last = ph ?? -1
      const dpr = window.devicePixelRatio || 1
      if (el.width !== Wd * dpr || el.height !== Ht * dpr) { el.width = Wd * dpr; el.height = Ht * dpr }
      const g = el.getContext('2d')
      if (!g) return
      g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, Wd, Ht)
      const D = Math.max(1, dur), n = curve.length, base = Ht - 14, acc = accent(el)
      for (const c of clips) {                         // clip ranges
        const a = c.start / D * Wd, b = Math.max(a + 2, c.end / D * Wd), on = keyOf(c) === cur && !free
        g.fillStyle = on ? rgba(acc, 0.18) : c.state === 'kept' ? rgba(acc, 0.07) : c.state === 'skipped' ? 'rgba(255,255,255,.025)'
          : isGem(c) ? 'rgba(201,167,255,.10)' : 'rgba(255,255,255,.06)'
        g.fillRect(a, 0, b - a, base)
        if (on) { g.fillStyle = acc; g.fillRect(a, 0, b - a, 2) }
      }
      const hi = Math.max(3, ...curve), bw = Wd / Math.max(1, n)  // the signal: the hottest moments in accent
      for (let i = 0; i < n; i++) {
        const v = Math.max(0, curve[i]! / hi), h = Math.max(1, v * (base - 8)), lv = heatLevel(v)
        g.fillStyle = lv === 2 ? acc : lv === 1 ? '#5a6274' : '#363a44'
        g.fillRect(i * bw, base - h, Math.max(1, bw - (bw > 3 ? 1 : 0)), h)
      }
      g.font = '600 10.5px Inter, system-ui, sans-serif'; g.textAlign = 'center'
      for (const { c, x, label } of markers(clips, cur, D, Wd)) {
        g.fillStyle = keyOf(c) === cur && !free ? '#e9eaed' : c.state === 'kept' ? acc : c.state === 'skipped' ? '#4a4d56' : isGem(c) ? '#c9a7ff' : '#a3a6ae'
        g.fillText(label, x, Ht - 2)
      }
      if (ph != null) { g.fillStyle = '#e9eaed'; g.fillRect(Math.round(ph / D * Wd) - 1, 0, 2, base) }
    }
    let t = 0
    // the playhead moves about four times a second; anything else is drawn on the next frame
    const tick = (now: number) => { raf = requestAnimationFrame(tick); if (dirty.current || now - t > 250) { t = now; draw() } }
    raf = requestAnimationFrame(tick)
    const ro = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => { dirty.current = true })
    if (ref.current) ro?.observe(ref.current)
    return () => { cancelAnimationFrame(raf); ro?.disconnect() }
  }, [])
  const at = (e: React.MouseEvent) => {
    const r = e.currentTarget.getBoundingClientRect()
    return { x: e.clientX - r.left, t: (e.clientX - r.left) / Math.max(1, r.width) * dur }
  }
  return (
    <div className="rv-overview" data-testid="rv-overview">
      <div className="rv-ov-ruler tnum muted small"><span>0:00</span><span>Click a highlighted range to review it, or anywhere else to preview.</span><span>{fmt(dur)}</span></div>
      <canvas ref={ref} aria-label="Hype across the stream" data-testid="rv-heat"
              onClick={e => { const { t } = at(e), hit = clipAt(clips, t); if (hit) onClip(hit); else onPreview(t) }}
              onMouseMove={e => { const { x, t } = at(e), hit = clipAt(clips, t); setHover({ x, text: hit ? `${fmt(t)}  ${hit.title || 'clip'}` : fmt(t) }) }}
              onMouseLeave={() => setHover(null)} />
      {hover && <div className="rv-ov-tag tnum" style={{ left: hover.x }}>{hover.text}</div>}
    </div>
  )
}
