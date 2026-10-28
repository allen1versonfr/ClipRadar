import { useEffect, useRef, useState, type PointerEvent } from 'react'
import type { Mark } from '../../api/schemas'
import { dragTo, fontCss, imageBox, markAnchor, textSize, wmUrl } from './geometry'

const W = 360, H = 640          // drawn at a third of 1080 × 1920; every size scales with the width
const images = new Map<string, HTMLImageElement>()

type Box = { x: number; y: number; w: number; h: number }

/** The layers on a 9:16 frame, where the export puts them. Drag one to place it by hand. */
export function WatermarkPreview({ marks, sel, onSelect, onMove, onDrop }: {
  marks: Mark[]; sel: number
  onSelect: (i: number) => void
  onMove: (i: number, at: Pick<Mark, 'pos' | 'x' | 'y'>) => void
  onDrop: (i: number) => void
}) {
  const cv = useRef<HTMLCanvasElement>(null)
  const boxes = useRef<Box[]>([])
  const drag = useRef<{ i: number; ox: number; oy: number; moved: boolean }>(undefined)
  const [loaded, setLoaded] = useState(0)

  useEffect(() => {
    const g = cv.current?.getContext('2d')
    if (!g) return
    g.clearRect(0, 0, W, H)
    g.fillStyle = '#26282d'
    g.fillRect(0, 0, W, H)
    g.fillStyle = 'rgba(255,255,255,.04)'        // TikTok's top bar and caption area, roughly
    g.fillRect(0, 0, W, H * 0.13)
    g.fillRect(0, H * 0.8, W, H * 0.2)
    boxes.current = marks.map((m, i) => {
      g.save()
      let bw: number, bh: number, im: HTMLImageElement | undefined, fs = 0, pad = 0
      if (m.kind === 'image') {
        const url = wmUrl(m.image)
        im = images.get(url)
        if (!im) {
          im = new Image()
          im.onload = () => setLoaded(n => n + 1)
          im.src = url
          images.set(url, im)
        }
        if (!im.complete || !im.naturalWidth) { g.restore(); return { x: 0, y: 0, w: 0, h: 0 } }
        ;({ bw, bh } = imageBox(m, W, im.naturalWidth, im.naturalHeight))
      } else {
        ;({ fs, pad } = textSize(m, W))
        g.font = fontCss(m.font, fs)
        bw = g.measureText(m.text).width + 2 * pad
        bh = fs * 1.2 + 2 * pad
      }
      const [x, y] = markAnchor(m, W, H, bw, bh)
      g.globalAlpha = Math.max(0.05, Math.min(1, m.opacity / 100))
      g.translate(x + bw / 2, y + bh / 2)
      g.rotate(m.rotation * Math.PI / 180)
      g.translate(-bw / 2, -bh / 2)
      if (im) g.drawImage(im, 0, 0, bw, bh)
      else {
        if (m.box) { g.fillStyle = m.box_color; g.fillRect(0, 0, bw, bh) }
        g.textBaseline = 'top'
        g.fillStyle = m.color
        if (!m.box && m.outline) {
          g.strokeStyle = 'rgba(0,0,0,.9)'
          g.lineWidth = Math.max(1, fs / 7)
          g.lineJoin = 'round'
          g.strokeText(m.text, pad, pad + fs * 0.1)
        }
        g.fillText(m.text, pad, pad + fs * 0.1)
      }
      g.restore()
      if (i === sel) {
        g.save()
        g.strokeStyle = getComputedStyle(cv.current!).getPropertyValue('--accent') || '#2fe36f'
        g.setLineDash([5, 4])
        g.lineWidth = 1.5
        g.strokeRect(x - 3, y - 3, bw + 6, bh + 6)
        g.restore()
      }
      return { x, y, w: bw, h: bh }
    })
  }, [marks, sel, loaded])

  useEffect(() => { void document.fonts?.ready.then(() => setLoaded(n => n + 1)) }, [])

  const pt = (e: PointerEvent) => {
    const r = cv.current!.getBoundingClientRect()
    return [(e.clientX - r.left) * W / r.width, (e.clientY - r.top) * H / r.height] as const
  }
  function down(e: PointerEvent) {
    const [x, y] = pt(e)
    for (let i = boxes.current.length - 1; i >= 0; i--) {     // drawn last: on top
      const b = boxes.current[i]!
      if (b.w && x >= b.x && x <= b.x + b.w && y >= b.y && y <= b.y + b.h) {
        onSelect(i)
        drag.current = { i, ox: x - (b.x + b.w / 2), oy: y - (b.y + b.h / 2), moved: false }
        cv.current!.setPointerCapture?.(e.pointerId)
        return
      }
    }
  }
  function move(e: PointerEvent) {
    const d = drag.current
    if (!d) return
    const [x, y] = pt(e)
    d.moved = true
    onMove(d.i, dragTo(x - d.ox, y - d.oy, W, H))
  }
  function up() {
    const d = drag.current
    drag.current = undefined
    if (d?.moved) onDrop(d.i)
  }
  return (
    <canvas ref={cv} className="wm-preview" width={W} height={H} data-testid="wm-preview"
            role="img" aria-label="Where the watermark goes on a 9:16 clip. Drag a layer to move it."
            onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up} />
  )
}
