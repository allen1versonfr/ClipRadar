// The Segments screen's sums, kept apart from the screen so they can be tested. kept() must match
// segments.kept() in src/segments.py: the length shown here is the length the export checks.

export interface Line { start: number; end: number; text: string }
export interface Segment {
  id: string; name: string; start: number; end: number; cuts: number[][]; gain_db: number; on: boolean; lines: Line[] | null
}
export interface Range { seg: string; a: number; b: number }
interface Word { start: number; end: number; word: string }

export const MIN_LEN = 0.5

/** The stretches of a segment that play: start to end with the cut stretches taken out. */
export function kept(s: Pick<Segment, 'start' | 'end' | 'cuts'>): Array<[number, number]> {
  const out: Array<[number, number]> = []
  let at = s.start
  for (const [a, b] of [...s.cuts].sort((x, y) => x[0]! - y[0]!) as Array<[number, number]>) {
    if (b <= at) continue
    if (a > at) out.push([at, Math.min(a, s.end)])
    at = Math.max(at, b)
    if (at >= s.end) break
  }
  if (at < s.end) out.push([at, s.end])
  return out.filter(([a, b]) => b - a >= MIN_LEN)
}

export const segLen = (s: Segment) => kept(s).reduce((t, [a, b]) => t + b - a, 0)
/** How long the export would be: the ticked segments, cuts left out. */
export const editLen = (segs: Segment[]) => segs.filter(s => s.on).reduce((t, s) => t + segLen(s), 0)

/** Everything that plays in the export, in order, for "Play the edit". */
export function playlist(segs: Segment[]): Range[] {
  return segs.filter(s => s.on).flatMap(s => kept(s).map(([a, b]) => ({ seg: s.id, a, b })))
}

/** Where to go from second t while range i of the list plays: stay, jump to the next range, or stop at the end. */
export function step(list: Range[], i: number, t: number): { i: number; seek?: number; stop?: boolean } {
  const r = list[i]
  if (!r) return { i, stop: true }
  if (t < r.b - 0.04) return { i }
  return i + 1 < list.length ? { i: i + 1, seek: list[i + 1]!.a } : { i, stop: true }
}

/** Inside a cut while a single segment plays: where the cut ends, to skip it. */
export function pastCut(s: Pick<Segment, 'cuts'>, t: number): number | null {
  const c = s.cuts.find(([a, b]) => t >= a! && t < b! - 0.04)
  return c ? c[1]! : null
}

/** arr with the item at `from` moved to `to`. */
export function move<T>(arr: T[], from: number, to: number): T[] {
  if (from === to || from < 0 || to < 0 || from >= arr.length || to >= arr.length) return arr
  const out = [...arr]
  const [x] = out.splice(from, 1)
  out.splice(to, 0, x!)
  return out
}

/** The segment with [a, b] cut out of it, joined with any cut it touches. */
export function addCut(s: Segment, a: number, b: number): Segment {
  const [x, y] = [Math.max(s.start, Math.min(a, b)), Math.min(s.end, Math.max(a, b))]
  if (y - x < 0.1) return s
  const all = [...s.cuts, [x, y]].sort((p, q) => p[0]! - q[0]!)
  const merged: number[][] = []
  for (const c of all) {
    const last = merged.at(-1)
    if (last && c[0]! <= last[1]!) last[1] = Math.max(last[1]!, c[1]!)
    else merged.push([c[0]!, c[1]!])
  }
  return { ...s, cuts: merged }
}

/** The captions heard in a segment as lines to edit: `per` words a line, each shown until the next begins. */
export function linesFrom(words: Word[], per: number, a: number, b: number): Line[] {
  const n = Math.max(1, Math.round(per))
  const inside = words.filter(w => w.start >= a && w.start < b)
  const out: Line[] = []
  for (let i = 0; i < inside.length; i += n) {
    const g = inside.slice(i, i + n)
    const next = inside[i + n]
    out.push({ start: g[0]!.start, end: Math.min(g.at(-1)!.end + 0.6, next ? next.start : b), text: g.map(w => w.word).join(' ') })
  }
  return out
}

/** The caption line on screen at second t. */
export const lineAt = (lines: Line[], t: number) => lines.find(l => t >= l.start && t < l.end)

/** A player can only turn the sound down; louder than the original is heard in the export. */
export const gainToVolume = (db: number) => Math.min(1, 10 ** (db / 20))
