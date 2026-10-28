import type { Mark } from '../../api/schemas'

// Where a watermark layer goes and how big it is: the same numbers as engine.mark_anchor and
// _mark_dialogue (and the original page's wmBox/wmAnchor), so a preview matches the export.

/** The caption fonts, as the renderer has them (watermarks.FONTS). */
export const FONTS: Record<string, { label: string; css: string; w: number; scale: number }> = {
  classic: { label: 'Classic', css: "'Arial Black', 'Segoe UI Black', 'Segoe UI', sans-serif", w: 900, scale: 1 },
  montserrat: { label: 'Montserrat', css: "'VC Montserrat'", w: 400, scale: 1 },
  poppins: { label: 'Poppins', css: "'VC Poppins'", w: 400, scale: 1 },
  inter: { label: 'Inter', css: "'VC Inter'", w: 400, scale: 1.05 },
  anton: { label: 'Anton', css: "'VC Anton'", w: 400, scale: 1.3 },
  bebas: { label: 'Bebas', css: "'VC Bebas'", w: 400, scale: 1.4 },
  bangers: { label: 'Bangers', css: "'VC Bangers'", w: 400, scale: 1.25 },
  luckiest: { label: 'Luckiest', css: "'VC Luckiest'", w: 400, scale: 1 },
}
export const fontOf = (k: string) => FONTS[k] ?? FONTS.classic!
export const fontCss = (k: string, px: number) => { const f = fontOf(k); return `${f.w} ${Math.round(px)}px ${f.css}` }

export const POS_NAMES: Record<Mark['pos'], string> = { tl: 'Top left', tr: 'Top right', bl: 'Bottom left', br: 'Bottom right', c: 'Centre', free: 'Anywhere' }

/** Text size in pixels, and the box padding, for a frame `wd` wide. */
export function textSize(m: Mark, wd: number) {
  const fs = 44 * m.size / 100 * fontOf(m.font).scale * wd / 1080
  return { fs, pad: m.box ? Math.max(6, fs / 4) : 0 }
}
/** An image layer is a fifth of the frame wide at 100 %. */
export function imageBox(m: Mark, wd: number, natW: number, natH: number) {
  const bw = wd * 0.2 * m.size / 100
  return { bw, bh: bw * natH / natW }
}
/** The layer's top-left corner, kept inside the frame. Corners sit clear of TikTok's bars (13 % and 80 %). */
export function markAnchor(m: Mark, wd: number, ht: number, bw: number, bh: number): [number, number] {
  const mg = Math.max(0, Math.min(200, m.margin)) * wd / 1080
  let x: number, y: number
  if (m.pos === 'free') { x = m.x * wd - bw / 2; y = m.y * ht - bh / 2 }
  else if (m.pos === 'c') { x = (wd - bw) / 2; y = (ht - bh) / 2 }
  else { x = m.pos[1] === 'l' ? mg : wd - bw - mg; y = m.pos[0] === 't' ? ht * 0.13 : ht * 0.8 - bh }
  return [Math.max(0, Math.min(x, wd - bw)), Math.max(0, Math.min(y, ht - bh))]
}
/** Where a layer dragged so its centre is at (cx, cy) ends up: placed by hand, in shares of the frame,
 *  snapping to the middle across. */
export function dragTo(cx: number, cy: number, wd: number, ht: number): Pick<Mark, 'pos' | 'x' | 'y'> {
  let fx = cx / wd
  if (Math.abs(fx - 0.5) < 0.025) fx = 0.5
  const r = (v: number) => Math.round(Math.max(0, Math.min(1, v)) * 1e4) / 1e4
  return { pos: 'free', x: r(fx), y: r(cy / ht) }
}
/** The image's address for the page (the server serves Clip Radar's own copies by name). */
export const wmUrl = (path: string) => '/wmimg/' + encodeURIComponent(path.split(/[\\/]/).pop() ?? '')
