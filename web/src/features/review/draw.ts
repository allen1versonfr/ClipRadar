import type { Clip, Mark, Safety } from '../../api/schemas'
import type { RenderOpts } from '../../prefs'
import { fontCss, fontOf, imageBox, markAnchor, textSize, wmUrl } from '../watermark/geometry'

// The clip preview, drawn the way the export renders it (engine.build_filter and build_ass), ported
// from the original page (src/ui/app.js: layoutDraw, drawTitle, captionAt, drawCaption, drawWatermark).
// Sizes are worked out at the export's own size and scaled, so the preview matches the file.

export const clamp = (v: number, a: number, b: number) => Math.min(b, Math.max(a, v))
const SHAPES: Record<string, [number, number]> = { '9:16': [1080, 1920], '1:1': [1080, 1080], '16:9': [1920, 1080] }
export const outSize = (o: RenderOpts): [number, number] => SHAPES[String(o.aspect)] ?? SHAPES['9:16']!
/** Cam + game needs a tall frame: a wide clip uses the whole frame. */
export const layoutOf = (o: RenderOpts) => (o.layout === 'split' && o.aspect === '16:9' ? 'full' : String(o.layout))

export interface Box { x: number; y: number; w: number; h: number }

/** Facecams in a clip: cam, then the others (cams), at most 4 (engine.MAX_CAMS). */
export const MAX_CAMS = 4
export const camRects = (o: RenderOpts): number[][] =>
  [o.cam as number[], ...((Array.isArray(o.cams) ? o.cams : []) as number[][])].slice(0, MAX_CAMS)
export type Hits = Record<string, Box | null>

const bg = typeof document !== 'undefined' ? document.createElement('canvas') : null

/** The picture in the chosen layout; returns where captions sit by default (y in canvas px). */
export function drawLayout(g: CanvasRenderingContext2D, src: CanvasImageSource, sw: number, sh: number, Wd: number, Ht: number,
  o: RenderOpts, follow: number | null, face: { y: number; h: number } | null): number {
  const z = Math.max(1, Number(o.game_zoom) || 1), lay = layoutOf(o), oh = outSize(o)[1]
  const gameCrop = (ow: number, ohh: number): [number, number, number, number] => {
    const a = ow / ohh, cw = Math.min(sw, sh * a) / z, ch = cw / a
    const fx = follow ?? Number(o.game_x)
    return [Math.min(Math.max(0, fx * sw - cw / 2), sw - cw), (sh - ch) / 2, cw, ch]
  }
  const rects = camRects(o)
  // one facecam filling a cell of the frame (scaled up to cover it, centred, as the render crops it)
  const camCell = (c: number[], dx: number, dy: number, dw: number, dh: number) => {
    const x = c[0]! * sw, y = c[1]! * sh, w = Math.max(4, c[2]! * sw), h = Math.max(4, c[3]! * sh)
    const s = Math.max(dw / w, dh / h), vw = dw / s, vh = dh / s
    g.drawImage(src, x + (w - vw) / 2, y + (h - vh) / 2, vw, vh, dx, dy, dw, dh)
  }
  if (lay === 'cam') {
    // several facecams: one above the other
    const ch = Ht / rects.length
    rects.forEach((c, i) => camCell(c, 0, i * ch, Wd, ch))
    return rects.length > 1 ? ch : Ht * 0.78
  }
  if (lay === 'split') {
    const th = Math.round(Ht * clamp(Number(o.cam_height), 0.2, 0.5))
    // several facecams: side by side above the gameplay
    const cw = Wd / rects.length
    rects.forEach((c, i) => camCell(c, i * cw, 0, cw, th))
    const [gx, gy, gw, gh] = gameCrop(Wd, Ht - th)
    g.drawImage(src, gx, gy, gw, gh, 0, th, Wd, Ht - th)
    return th
  }
  if (lay === 'full') {
    const [gx, gy, gw, gh] = gameCrop(Wd, Ht)
    g.drawImage(src, gx, gy, gw, gh, 0, 0, Wd, Ht)
    if (follow != null && face) {           // captions move off the face, like the render
      const top = (face.y - face.h * 0.7) * Ht, bot = (face.y + face.h * 0.9) * Ht, d = Ht * 0.7, m = Ht * 140 / oh
      if (top - m <= d && d <= bot + m) { const below = bot + Ht * 230 / oh; return below <= Ht * 0.86 ? below : Math.max(Ht * 260 / oh, top - Ht * 230 / oh) }
    }
    return Ht * 0.7
  }
  // a blurred, darkened copy of the picture filling the frame (Blur fit and Square blur)
  if (bg) {
    const b = bg.getContext('2d'), bw = Math.min(sw, sh * Wd / Ht), bh = bw * Ht / Wd
    bg.width = 36; bg.height = Math.max(8, Math.round(36 * Ht / Wd))
    b?.drawImage(src, (sw - bw) / 2, (sh - bh) / 2, bw, bh, 0, 0, bg.width, bg.height)
    g.imageSmoothingEnabled = true; g.imageSmoothingQuality = 'high'
    g.drawImage(bg, 0, 0, Wd, Ht)
  }
  g.fillStyle = 'rgba(0,0,0,.18)'; g.fillRect(0, 0, Wd, Ht)
  if (lay === 'square') {
    const s = Math.min(Wd, Ht), [gx, gy, gw, gh] = gameCrop(s, s)
    g.drawImage(src, gx, gy, gw, gh, (Wd - s) / 2, (Ht - s) / 2, s, s)
    return Math.min(Ht / 2 + s / 2 + Ht * 170 / oh, Ht * 0.8)
  }
  const fh = Wd * sh / sw
  if (z > 1) { const [gx, gy, gw, gh] = gameCrop(Wd, fh); g.drawImage(src, gx, gy, gw, gh, 0, (Ht - fh) / 2, Wd, fh) }
  else g.drawImage(src, 0, (Ht - fh) / 2, Wd, fh)
  return Math.min(Ht / 2 + fh / 2 + Ht * 170 / oh, Ht * 0.8)
}

/** How far Text tab › Size goes, percent (engine.MAX_TEXT_SIZE). */
export const MAX_TEXT_SIZE = { title: 300, caption: 250 }
export const TITLE_YELLOW = '#ffe600'
/** The black border of an Outline or Yellow title, at the export's size (engine.title_border). */
export function titleBorder(o: RenderOpts, titleFs: number): number {
  const k = clamp(Number(o.title_outline ?? 100) || 0, 0, 300) / 100
  return Math.round(Math.max(4, Math.floor(titleFs / 10)) * (o.title_style === 'yellow' ? 1.25 : 1) * k)
}

export function drawTitle(g: CanvasRenderingContext2D, text: string, Wd: number, Ht: number, o: RenderOpts): Box {
  const k = Wd / outSize(o)[0], st = o.title_style, size = clamp(Number(o.title_size), 50, MAX_TEXT_SIZE.title)
  const fs = 64 * size / 100 * fontOf(o.title_font).scale * k
  const pad = Math.max(8, Math.floor(64 * size / 100 * fontOf(o.title_font).scale / 4)) * k
  g.font = fontCss(o.title_font, fs); g.textAlign = 'center'; g.textBaseline = 'top'
  const x = clamp(Number(o.title_x), 0.3, 0.7) * Wd, top = clamp(Number(o.title_y), 0, 0.92) * Ht
  const wrapW = 2 * (Math.min(x, Wd - x) - 90 * k), lines: string[] = []
  let line = ''
  for (const w of text.trim().split(/\s+/)) {
    const t = line ? `${line} ${w}` : w
    if (g.measureText(t).width > wrapW && line) { lines.push(line); line = w } else line = t
  }
  if (line) lines.push(line)
  const lh = fs * 1.19, tw = Math.max(...lines.map(l => g.measureText(l).width))
  if (st === 'outline' || st === 'yellow') {
    const ob = titleBorder(o, Math.floor(fs / k)) * k
    g.lineJoin = 'round'; g.lineWidth = ob * 2; g.strokeStyle = '#000'
    g.fillStyle = st === 'yellow' ? TITLE_YELLOW : String(o.title_color || '#ffffff')
    lines.forEach((l, i) => { if (ob > 0) g.strokeText(l, x, top + i * lh); g.fillText(l, x, top + i * lh) })
    return { x: x - tw / 2 - ob, y: top - ob, w: tw + 2 * ob, h: lines.length * lh + ob }
  }
  const [fill, boxc] = st === 'dark' ? ['#fff', 'rgba(20,20,20,.9)'] : st === 'accent' ? ['#141414', String(o.caption_color)] : ['#141414', '#fff']
  const bw = tw + 2 * pad, bh = lines.length * lh + 2 * pad - (lh - fs)
  g.fillStyle = boxc; g.fillRect(x - bw / 2, top, bw, bh)
  g.fillStyle = fill; lines.forEach((l, i) => g.fillText(l, x, top + pad + i * lh))
  return { x: x - bw / 2, y: top, w: bw, h: bh }
}

// ---- captions: which words show when, as engine.caption_words / build_ass group them

type Word = Clip['words'][number] & { g?: unknown; edited?: boolean }
const FILLER = new Set(['um', 'uh', 'uhh', 'umm', 'erm', 'hmm', 'mm', 'uhm'])
const EMPH = new Set(['no', 'way', 'what', 'insane', 'crazy', 'wait', 'yes', 'never', 'impossible', 'clutch', 'holy',
  'omg', 'wtf', 'bro', 'stop', 'run', 'dead', 'help', 'why', 'how'])

export function captionWords(ws: Word[], o: RenderOpts): Word[] {
  const out: Word[] = []
  for (let w of ws) {
    const core = w.word.replace(/[^\w']/g, '').toLowerCase()
    if (o.caption_filler === false && FILLER.has(core)) continue
    if (o.caption_punct === false) { const t = w.word.replace(/[.,!?;:…"“”]+/g, '').trim(); if (!t) continue; w = { ...w, word: t } }
    out.push(w)
  }
  return out
}
export const emphasised = (w: string) => {
  const core = w.replace(/[^\w']/g, '')
  return EMPH.has(core.toLowerCase()) || w.endsWith('!') || (core.length > 2 && core === core.toUpperCase() && /[A-Z]/.test(core))
}
export interface Chunk { words: Word[]; start: number; end: number }
export function chunksFor(c: Pick<Clip, 'words' | 'start' | 'end'>, o: RenderOpts): Chunk[] {
  const ws = captionWords((c.words as Word[]).filter(w => w.start >= c.start - 0.2 && w.start < c.end), o)
  const chunks: Word[][] = []
  let ch: Word[] = []
  for (const w of ws) {
    const prev = ch.at(-1)
    // a line typed in the Captions tab stays together (g); otherwise break by length, pauses and sentence ends
    const brk = prev && ((w.g || prev.g) ? w.g !== prev.g
      : (ch.length >= Math.max(1, Number(o.words_per_line)) || w.start - prev.end > 0.6 || /[.?!]$/.test(prev.word)))
    if (brk) { chunks.push(ch); ch = [] }
    ch.push(w)
  }
  if (ch.length) chunks.push(ch)
  return chunks.map((k, i) => {
    const last = k.at(-1)!
    const end = i + 1 < chunks.length ? Math.min(Math.max(last.end, last.end + 0.3), chunks[i + 1]![0]!.start) : last.end + 0.4
    return { words: k, start: k[0]!.start, end: Math.min(end, c.end) }
  })
}

export const maskWord = (w: string) => w.replace(/^(\W*)(\w)(\w*)/, (_m, a: string, b: string, r: string) => a + b + '*'.repeat(r.length))
/** A replacement from Settings › Caption words, keeping the word's capitals. */
export function replaceWord(w: string, table: Record<string, string>): string {
  const core = w.replace(/^\W+|\W+$/g, ''), r = table[core.toLowerCase()]
  if (r === undefined || !core) return w
  const rr = core === core.toUpperCase() && core.length > 1 ? r.toUpperCase()
    : core[0] === core[0]!.toUpperCase() ? r.charAt(0).toUpperCase() + r.slice(1) : r
  return w.replace(core, rr)
}
type Flag = { source?: string; cat?: string; status?: string; start?: number; end?: number }
/** What will be masked: fixes made in Review, and automatic censoring when it's on. */
export function censorWord(c: Clip, w: Word, safety: Pick<Safety, 'enabled' | 'censor' | 'censor_categories'> | undefined): string {
  const abs = w.start + 0.01
  const fixes = ((c.check?.fixes ?? []) as Array<{ span?: [number, number] }>).filter(f => f.span)
  if (fixes.some(f => abs >= f.span![0] - 0.02 && abs <= f.span![1] + 0.02)) return maskWord(w.word)
  if (!safety?.enabled || !safety.censor || safety.censor === 'off' || !c.safety) return w.word
  const t = w.start - c.start + 0.01
  return (c.safety.flags as Flag[]).some(f => f.source === 'speech' && safety.censor_categories.includes(f.cat ?? '') && f.status !== 'accepted'
    && f.status !== 'dismissed' && t >= (f.start ?? 0) - 0.02 && t <= (f.end ?? 0) + 0.02) ? maskWord(w.word) : w.word
}

export interface Cap { texts: string[]; hi: number; t0: number; wt?: number; emph?: boolean[] }
export function captionAt(c: Clip, t: number, o: RenderOpts, table: Record<string, string>, safety?: Pick<Safety, 'enabled' | 'censor' | 'censor_categories'>): Cap | null {
  t -= (Number(o.caption_offset) || 0) / 1000          // captions moved earlier or later (Text › Timing)
  for (const ch of chunksFor(c, o)) {
    if (t >= ch.start && t < ch.end) {
      let hi = 0
      ch.words.forEach((w, j) => { if (w.start <= t) hi = j })
      return { texts: ch.words.map(w => { const x = replaceWord(censorWord(c, w, safety), table); return o.uppercase ? x.toUpperCase() : x }),
        hi, t0: ch.start, wt: ch.words[hi]!.start, emph: ch.words.map(w => !!o.caption_emph && emphasised(w.word)) }
    }
  }
  return null
}

export function capAnchor(capY: number, Wd: number, Ht: number, o: RenderOpts): [number, number] {
  const oh = outSize(o)[1]
  if (o.caption_pos === 'custom') return [clamp(Number(o.cap_x), 0.3, 0.7) * Wd, clamp(Number(o.cap_y) * Ht, Ht * 120 / oh, Ht - Ht * 120 / oh)]
  const y = ({ top: Ht * 0.22, middle: Ht * 0.5, bottom: Ht * 0.78 } as Record<string, number>)[String(o.caption_pos)] ?? capY
  return [Wd / 2, clamp(y, Ht * 260 / oh, Ht - Ht * 220 / oh)]
}

export function drawCaption(g: CanvasRenderingContext2D, cap: Cap, capY: number, Wd: number, Ht: number, t: number, o: RenderOpts): Box {
  const k = Wd / outSize(o)[0], st = o.caption_style, hi = String(o.caption_color)
  const [cx, cy] = capAnchor(capY, Wd, Ht, o)
  const chars = cap.texts.join(' ').length, fsBase = 86 * clamp(Number(o.caption_size), 50, MAX_TEXT_SIZE.caption) / 100 * fontOf(o.caption_font).scale
  const fs1080 = chars <= 16 ? fsBase : Math.max(fsBase * 0.75, fsBase * 18 / chars), fs = fs1080 * k
  g.font = fontCss(o.caption_font, fs); g.textAlign = 'left'; g.textBaseline = 'middle'
  const sp = g.measureText(' ').width, widths = cap.texts.map(w => g.measureText(w).width)
  const maxW = 2 * (Math.min(cx, Wd - cx) - 70 * k), lines: number[][] = [[]]
  let lw = 0
  cap.texts.forEach((_w, i) => {
    if (lw && lw + sp + widths[i]! > maxW) { lines.push([]); lw = 0 }
    lines.at(-1)!.push(i); lw += (lw ? sp : 0) + widths[i]!
  })
  const spacing = clamp(Number(o.caption_spacing) || 100, 100, 200)
  if (spacing > 100 && lines.length > 1 && cap.texts.length > 1) {
    // more line spacing: two balanced lines, split where the render splits them (engine._join_lines)
    const half = chars / 2
    let acc = 0, best = 1, bd = Infinity
    for (let i = 1; i < cap.texts.length; i++) { acc += cap.texts[i - 1]!.length + 1; if (Math.abs(acc - half) < bd) { bd = Math.abs(acc - half); best = i } }
    lines.length = 0
    lines.push([...Array(best).keys()], [...Array(cap.texts.length - best).keys()].map(i => i + best))
  }
  const lh = fs * 1.15 + (lines.length > 1 ? fs * (spacing - 100) / 100 : 0), y0 = cy - (lines.length - 1) * lh / 2
  const bord = Math.max(4, Math.floor(fs1080 / 12)) * k * clamp(Number(o.cap_outline ?? 100), 0, 250) / 100
  // the same animations as the render: how a group comes in, and how the word being said moves
  let motion = String(o.caption_motion || (o.caption_anim ? 'pop' : 'none'))
  if (st === 'clean' && motion === 'pop') motion = 'none'
  const dur = 0.12 * 100 / clamp(Number(o.caption_speed) || 100, 50, 200), age = t - cap.t0, wage = t - (cap.wt ?? cap.t0)
  const sc = motion === 'pop' && age >= 0 && age < dur ? 1.14 - 0.14 * age / dur : 1
  const inP = clamp(age / (2 * dur), 0, 1)
  const alpha = motion === 'fade' || motion === 'slide' ? inP : 1, dy = motion === 'slide' ? fs * 0.6 * (1 - inP) : 0
  const wordScale = () => {
    if (wage < 0 || wage >= dur) return 1
    if (motion === 'word') return 1.32 - 0.32 * wage / dur
    if (motion === 'bounce') return wage < dur / 2 ? 0.7 + 0.52 * wage / (dur / 2) : 1.22 - 0.22 * (wage - dur / 2) / (dur / 2)
    return 1
  }
  g.save(); g.globalAlpha *= alpha; g.translate(0, dy); g.translate(cx, cy); g.scale(sc, sc); g.translate(-cx, -cy)
  let minX = Infinity, maxX = -Infinity
  lines.forEach((idx, li) => {
    const lwid = idx.reduce((a, i) => a + widths[i]!, 0) + sp * (idx.length - 1)
    let x = cx - lwid / 2
    minX = Math.min(minX, x); maxX = Math.max(maxX, x + lwid)
    const y = y0 + li * lh
    for (const i of idx) {
      const on = i === cap.hi, word = cap.texts[i]!
      if (motion === 'reveal' && i > cap.hi) { x += widths[i]! + sp; continue }     // not said yet: its place is kept
      const ws = on && (motion === 'word' || motion === 'bounce') ? wordScale() : 1
      g.save()
      if (ws !== 1) { g.translate(x + widths[i]! / 2, y); g.scale(ws, ws); g.translate(-(x + widths[i]! / 2), -y) }
      const strong = !!cap.emph?.[i] && !on
      g.lineJoin = 'round'
      g.shadowColor = 'transparent'; g.shadowBlur = 0; g.shadowOffsetX = g.shadowOffsetY = 0
      if (st === 'clean') {
        const sh = clamp(Number(o.cap_shadow ?? 100), 0, 250) / 100
        g.shadowColor = sh ? 'rgba(0,0,0,.6)' : 'transparent'; g.shadowBlur = 6 * k * sh; g.shadowOffsetX = g.shadowOffsetY = 4 * k * sh
        g.fillStyle = strong ? hi : '#fff'; g.fillText(word, x, y)
      } else if (st === 'glow') {
        const s2 = on ? 1.12 : 1
        g.save(); g.translate(x + widths[i]! / 2, y); g.scale(s2, s2); g.translate(-(x + widths[i]! / 2), -y)
        g.shadowColor = hi; g.shadowBlur = Math.max(5, fs1080 / 8) * k * 1.6
        g.lineWidth = bord * 2; g.strokeStyle = hi; g.strokeText(word, x, y)
        g.shadowBlur = 0; g.fillStyle = '#fff'; g.fillText(word, x, y)
        g.restore()
      } else if (st === 'box' && on) {
        g.lineWidth = Math.max(8, Math.floor(fs1080 * 0.22)) * k * 2; g.strokeStyle = hi; g.strokeText(word, x, y)
        g.fillStyle = '#141414'; g.fillText(word, x, y)
      } else {
        g.lineWidth = bord * 2; g.strokeStyle = '#000'; g.strokeText(word, x, y)
        g.fillStyle = (st === 'pop' && on) || (st === 'karaoke' && i <= cap.hi) || (strong && st !== 'karaoke') ? hi : '#fff'
        g.fillText(word, x, y)
      }
      g.restore()
      x += widths[i]! + sp
    }
  })
  g.restore()
  return { x: minX - bord, y: y0 - lh / 2 - bord, w: maxX - minX + 2 * bord, h: lines.length * lh + 2 * bord }
}

// ---- watermark layers (Settings › Watermark), with each layer's box for dragging

const wmImages = new Map<string, HTMLImageElement>()
export function wmImage(path: string, onLoad: () => void): HTMLImageElement {
  const url = wmUrl(path)
  let im = wmImages.get(url)
  if (!im) { im = new Image(); im.onload = onLoad; im.src = url; wmImages.set(url, im) }
  return im
}
export function drawMarks(g: CanvasRenderingContext2D, marks: Mark[], Wd: number, Ht: number, t: number, dragging: number,
  onLoad: () => void): Array<Box | null> {
  return marks.map((m, i) => {
    if (i !== dragging && (t < m.start || (m.end && t > m.end))) return null
    let bw: number, bh: number, fs = 0, pad = 0, im: HTMLImageElement | undefined
    g.save()
    if (m.kind === 'image') {
      im = wmImage(m.image, onLoad)
      if (!im.complete || !im.naturalWidth) { g.restore(); return null }
      ;({ bw, bh } = imageBox(m, Wd, im.naturalWidth, im.naturalHeight))
    } else {
      ;({ fs, pad } = textSize(m, Wd))
      g.font = fontCss(m.font, fs)
      bw = g.measureText(m.text).width + 2 * pad; bh = fs * 1.2 + 2 * pad
    }
    const [x, y] = markAnchor(m, Wd, Ht, bw, bh)
    g.globalAlpha = clamp(m.opacity / 100, 0.05, 1)
    g.translate(x + bw / 2, y + bh / 2); g.rotate(m.rotation * Math.PI / 180); g.translate(-bw / 2, -bh / 2)
    if (im) g.drawImage(im, 0, 0, bw, bh)
    else {
      if (m.box) { g.fillStyle = m.box_color; g.fillRect(0, 0, bw, bh) }
      g.textBaseline = 'top'; g.textAlign = 'left'; g.fillStyle = m.color
      if (!m.box && m.outline) { g.strokeStyle = 'rgba(0,0,0,.9)'; g.lineWidth = Math.max(1, fs / 7); g.lineJoin = 'round'; g.strokeText(m.text, pad, pad + fs * 0.1) }
      g.fillText(m.text, pad, pad + fs * 0.1)
    }
    g.restore()
    return { x, y, w: bw, h: bh }
  })
}

/** What TikTok's own buttons and caption cover, so text can be kept clear of them. */
export function drawGuides(g: CanvasRenderingContext2D, Wd: number, Ht: number, snapX: boolean) {
  g.save()
  g.fillStyle = 'rgba(255,70,90,.13)'
  g.fillRect(Wd * 0.86, Ht * 0.36, Wd * 0.14, Ht * 0.5); g.fillRect(0, Ht * 0.86, Wd, Ht * 0.14); g.fillRect(0, 0, Wd, Ht * 0.06)
  g.fillStyle = 'rgba(255,255,255,.55)'; g.font = `500 ${Math.round(Wd / 40)}px Inter, "Segoe UI", sans-serif`; g.textAlign = 'center'; g.textBaseline = 'middle'
  g.fillText('Covered by TikTok', Wd / 2, Ht * 0.93)
  if (snapX) { g.strokeStyle = 'rgba(255,214,102,.9)'; g.lineWidth = 1; g.setLineDash([6, 5]); g.beginPath(); g.moveTo(Wd / 2, 0); g.lineTo(Wd / 2, Ht); g.stroke() }
  g.restore()
}
export function drawSelection(g: CanvasRenderingContext2D, r: Box | null | undefined, strong: boolean) {
  if (!r) return
  g.save(); g.strokeStyle = strong ? '#fff' : 'rgba(255,255,255,.7)'; g.lineWidth = 1.5; g.setLineDash(strong ? [] : [5, 4])
  g.strokeRect(r.x - 4, r.y - 4, r.w + 8, r.h + 8)
  if (strong) {
    g.fillStyle = '#fff'
    for (const [x, y] of [[r.x - 4, r.y - 4], [r.x + r.w + 4, r.y - 4], [r.x - 4, r.y + r.h + 4], [r.x + r.w + 4, r.y + r.h + 4]] as const) { g.beginPath(); g.arc(x, y, 4, 0, 7); g.fill() }
  }
  g.restore()
}

/** Where a dragged item lands (the original page's pointermove): shares of the frame, snapping to the middle. */
export function dragPlace(what: string, cx: number, top: number, cy: number, Wd: number, Ht: number, o: RenderOpts): { patch: Partial<RenderOpts> | Partial<Mark>; snapX: boolean } {
  let fx = cx / Wd
  const snapX = Math.abs(fx - 0.5) < 0.025
  if (snapX) fx = 0.5
  const r4 = (v: number) => Math.round(clamp(v, 0, 1) * 1e4) / 1e4
  if (what.startsWith('wm')) return { patch: { pos: 'free', x: r4(fx), y: r4(cy / Ht) } as Partial<Mark>, snapX }
  if (what === 'title') return { patch: { title_x: clamp(fx, 0.3, 0.7), title_y: clamp(top / Ht, 0, 0.92) }, snapX }
  const oh = outSize(o)[1]
  return { patch: { caption_pos: 'custom', cap_x: clamp(fx, 0.3, 0.7), cap_y: clamp(cy / Ht, 120 / oh, 1 - 120 / oh) }, snapX }
}
