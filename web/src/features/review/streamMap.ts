import type { Clip } from '../../api/schemas'
import { type Filter, isGem, keyOf } from './model'

// Review's stream overview: the hype across the whole stream, with the clips on it (the original
// page's drawHeat and its click), without the canvas.

/** Where a clip is in the stream (a live moment's times are in its own file, offset by where it starts). */
export type Span = (c: Clip) => [number, number]
export const ownSpan: Span = c => [c.start, c.end]

/** The clip at a moment of the stream, if one covers it. */
export const clipAt = (clips: Clip[], t: number, span: Span = ownSpan) => clips.find(c => { const [a, b] = span(c); return t >= a && t <= b })

/** The list a clip picked on the overview shows up in: yours unless it isn't in it. */
export function filterFor(c: Clip, filter: Filter): Filter {
  if (isGem(c)) return 'gems'
  if (filter === 'gems' || (c.state !== 'todo' && filter === 'todo')) return c.state === 'todo' ? 'todo' : 'all'
  return filter
}

/** The numbers under the clips: hype clips 1, 2, 3…, gems G1, G2…; the one open first, none closer than 14 px. */
export function markers(clips: Clip[], cur: string | undefined, dur: number, width: number, span: Span = ownSpan, live = false): Array<{ c: Clip; x: number; label: string }> {
  const gems = clips.filter(isGem), hype = clips.filter(c => !isGem(c))
  const placed: number[] = [], out: Array<{ c: Clip; x: number; label: string }> = []
  clips.map((c, i) => ({ c, i, x: (span(c)[0] + span(c)[1]) / 2 / Math.max(1, dur) * width }))
    .sort((a, b) => Number(keyOf(b.c) === cur) - Number(keyOf(a.c) === cur) || a.i - b.i)
    .forEach(({ c, x }) => {
      if (placed.some(px => Math.abs(px - x) < 14)) return
      placed.push(x)
      out.push({ c, x, label: live ? String(clips.indexOf(c) + 1) : isGem(c) ? `G${gems.indexOf(c) + 1}` : String(hype.indexOf(c) + 1) })
    })
  return out
}

/** A signal bar's shade: the hottest moments in the accent colour. */
export const heatLevel = (v: number) => (v > 0.55 ? 2 : v > 0.3 ? 1 : 0)
