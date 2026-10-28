import { useEffect, useRef, type PointerEvent } from 'react'
import type { Clip, Mark, Safety } from '../../api/schemas'
import type { RenderOpts } from '../../prefs'
import { type Box, captionAt, chunksFor, dragPlace, drawCaption, drawGuides, drawLayout, drawMarks, drawSelection,
  drawTitle, type Hits, layoutOf, outSize } from './draw'

export interface Track { path: Array<[number, number]>; face_y?: number | null | undefined; face_h?: number | null | undefined }

/** The clip as it will render (9:16, 1:1 or 16:9): the layout, title, captions and watermark on the
 *  playing video. Drag the title, the captions or a watermark layer to move it; click to play or pause. */
export function Preview({ clip, video, thumb, opts, marks, safety, replace, track, guides, onOpts, onMark, onToggle }: {
  clip: Clip; video: () => HTMLVideoElement | null; thumb: string; opts: RenderOpts; marks: Mark[]
  safety: Pick<Safety, 'enabled' | 'censor' | 'censor_categories'> | undefined; replace: Record<string, string>
  track: Track | null; guides: boolean
  onOpts: (patch: Partial<RenderOpts>, save: boolean) => void
  onMark: (i: number, patch: Partial<Mark>, save: boolean) => void
  onToggle: () => void
}) {
  const cv = useRef<HTMLCanvasElement>(null)
  const frame = useRef<HTMLImageElement>(null)
  const hits = useRef<Hits>({})
  const drag = useRef<{ what: string; ox: number; oy: number; sx: number; sy: number; moved: boolean; snapX: boolean }>(undefined)
  const hover = useRef<string | null>(null)
  const inside = useRef(false)
  const p = useRef({ clip, opts, marks, safety, replace, track, guides })
  p.current = { clip, opts, marks, safety, replace, track, guides }
  const [ow, oh] = outSize(opts)
  const scale = 960 / Math.max(ow, oh), W = Math.round(ow * scale / 2) * 2, H = Math.round(oh * scale / 2) * 2

  useEffect(() => {
    let raf = 0
    const draw = () => {
      raf = requestAnimationFrame(draw)
      const el = cv.current, g = el?.getContext('2d')
      if (!el || !g) return
      const { clip: c, opts: o, marks: ms, safety: sf, replace: rp, track: tr, guides: gd } = p.current
      const Wd = el.width, Ht = el.height, v = video(), img = frame.current
      g.fillStyle = '#000'; g.fillRect(0, 0, Wd, Ht)
      hits.current = {}
      let src: CanvasImageSource | null = null, sw = 0, sh = 0
      if (v && v.readyState >= 2 && v.videoWidth) { src = v; sw = v.videoWidth; sh = v.videoHeight }
      else if (img?.complete && img.naturalWidth) { src = img; sw = img.naturalWidth; sh = img.naturalHeight }
      if (!src) return
      const t = v?.currentTime ?? c.start
      // following the face: where the crop is now, along the path Clip Radar worked out
      let follow: number | null = null
      if (tr?.path.length && layoutOf(o) === 'full' && o.track !== 'off') {
        const at = t - c.start, pa = tr.path
        follow = at <= pa[0]![0] ? pa[0]![1] : pa.at(-1)![1]
        for (let i = 1; i < pa.length; i++) if (at < pa[i]![0]) { const [t0, a] = pa[i - 1]!, [t1, b] = pa[i]!; follow = a + (b - a) * (at - t0) / (t1 - t0); break }
      }
      const face = follow != null && tr?.face_y != null ? { y: tr.face_y, h: tr.face_h ?? 0 } : null
      const capY = drawLayout(g, src, sw, sh, Wd, Ht, o, follow, face)
      if (drag.current || gd) drawGuides(g, Wd, Ht, !!drag.current?.snapX)
      if (c.title.trim()) hits.current.title = drawTitle(g, c.title, Wd, Ht, o)
      if (o.captions) {
        const cap = captionAt(c, t, o, rp, sf)
        if (cap) hits.current.cap = drawCaption(g, cap, capY, Wd, Ht, t, o)
        else if (inside.current || drag.current?.what === 'cap') {
          // nothing being said right now: a stand-in, so the captions can still be placed
          g.save(); g.globalAlpha = 0.55
          const first = chunksFor(c, o)[0]
          hits.current.cap = drawCaption(g, { texts: first ? first.words.map(w => (o.uppercase ? w.word.toUpperCase() : w.word)) : ['YOUR', 'CAPTIONS'], hi: 0, t0: -9 }, capY, Wd, Ht, 0, o)
          g.restore()
        }
      }
      const dw = drag.current?.what.startsWith('wm') ? Number(drag.current.what.slice(2)) : -1
      drawMarks(g, ms, Wd, Ht, Math.max(0, t - c.start), dw, () => {}).forEach((b, i) => { hits.current[`wm${i}`] = b })
      const sel = drag.current ? drag.current.what : hover.current
      if (sel) drawSelection(g, hits.current[sel], !!drag.current)
    }
    raf = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(raf)
  }, [video])

  const pt = (e: PointerEvent) => {
    const r = cv.current!.getBoundingClientRect()
    return [(e.clientX - r.left) * cv.current!.width / r.width, (e.clientY - r.top) * cv.current!.height / r.height] as const
  }
  function hitAt(x: number, y: number): string | null {
    const inBox = (r: Box | null | undefined) => !!r && x >= r.x - 10 && x <= r.x + r.w + 10 && y >= r.y - 10 && y <= r.y + r.h + 10
    for (let i = p.current.marks.length - 1; i >= 0; i--) if (inBox(hits.current[`wm${i}`])) return `wm${i}`    // drawn last: on top
    if (inBox(hits.current.title)) return 'title'
    if (inBox(hits.current.cap)) return 'cap'
    return null
  }
  function down(e: PointerEvent) {
    if (e.button !== 0) return
    const [x, y] = pt(e), what = hitAt(x, y)
    if (!what) { drag.current = { what: '', ox: 0, oy: 0, sx: x, sy: y, moved: false, snapX: false }; return }
    const r = hits.current[what]!
    drag.current = { what, ox: x - (r.x + r.w / 2), oy: what === 'title' ? y - r.y : y - (r.y + r.h / 2), sx: x, sy: y, moved: false, snapX: false }
    if (what === 'cap' && p.current.opts.caption_pos !== 'custom') onOpts({ cap_x: 0.5, cap_y: (r.y + r.h / 2) / cv.current!.height }, false)
    cv.current!.setPointerCapture?.(e.pointerId)
  }
  function move(e: PointerEvent) {
    const [x, y] = pt(e), d = drag.current, el = cv.current!
    if (!d) {
      const h = hitAt(x, y)
      el.style.cursor = h ? 'move' : 'pointer'
      hover.current = h
      return
    }
    if (!d.what) return
    if (!d.moved && Math.hypot(x - d.sx, y - d.sy) < 4) return
    d.moved = true
    const { patch, snapX } = dragPlace(d.what, x - d.ox, y - d.oy, y - d.oy, el.width, el.height, p.current.opts)
    d.snapX = snapX
    if (d.what.startsWith('wm')) onMark(Number(d.what.slice(2)), patch as Partial<Mark>, false)
    else onOpts(patch as Partial<RenderOpts>, false)
  }
  function up() {
    const d = drag.current
    drag.current = undefined
    if (!d) return
    if (!d.moved) { onToggle(); return }
    if (d.what.startsWith('wm')) onMark(Number(d.what.slice(2)), {}, true)       // saved where you left it
    else onOpts({}, true)
  }
  return (
    <div className="rv-phone" style={{ aspectRatio: `${ow} / ${oh}` }}>
      <canvas ref={cv} width={W} height={H} data-testid="rv-preview" aria-label="Clip preview, as it will render. Drag the title, captions or watermark to move them."
              onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up}
              onPointerEnter={() => { inside.current = true }} onPointerLeave={() => { inside.current = false; hover.current = null }} />
      <img ref={frame} src={thumb} alt="" hidden />
    </div>
  )
}
