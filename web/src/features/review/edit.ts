import type { Clip } from '../../api/schemas'

// Trimming and undo, as in the original page's editor (src/ui/app.js: setBounds, edSnap, rulerStep,
// pushEdit / undoRedo), without the screen.

export const MIN_LEN = 1                                  // shortest clip, seconds
export const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))

/** New bounds kept inside the video and at least MIN_LEN long; null when nothing changes. */
export function bounds(c: Pick<Clip, 'start' | 'end'>, start: number, end: number, dur: number): { start: number; end: number } | null {
  const s = clamp(start, 0, Math.max(0, dur - MIN_LEN))
  const e = clamp(end, s + MIN_LEN, dur)
  if (Math.abs(s - c.start) < 1e-4 && Math.abs(e - c.end) < 1e-4) return null
  return { start: +s.toFixed(3), end: +e.toFixed(3) }
}

/** A dragged edge sticks to a word's edge or the playhead when it's within 6 px (Alt: no snapping). */
export function snap(t: number, words: Clip['words'], playhead: number, secPerPx: number, off = false): number {
  if (off) return t
  let best = t, bd = 6 * secPerPx
  for (const v of [playhead, ...words.flatMap(w => [w.start - 0.05, w.end + 0.05])]) {
    const d = Math.abs(v - t)
    if (d < bd) { bd = d; best = v }
  }
  return best
}

/** Ruler tick spacing: about one label every 90 px. */
export function rulerStep(span: number, width: number): number {
  const want = span / (width / 90)
  return [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800].find(s => s >= want) ?? 3600
}

/** The time a frame step lands on (a quarter frame in, so the browser shows that frame). */
export const frameStep = (t: number, fps: number, dir: 1 | -1) => Math.max(0, Math.round(t * fps + dir) / fps + 0.25 / fps)

/** 1:23:45.20, stream time with hundredths */
export function fmtA(t: number): string {
  t = Math.max(0, t || 0)
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60
  const ss = (s < 10 ? '0' : '') + s.toFixed(2)
  return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`
}

// ---- undo / redo: every edit to a clip's cut, title and post caption

export const EDIT_KEYS = ['start', 'end', 'userTrim', 'title', 'userTitle', 'postText', 'userPost'] as const
export type Snap = Pick<Clip, (typeof EDIT_KEYS)[number]>
export const snapOf = (c: Clip): Snap => ({ start: c.start, end: c.end, userTrim: !!c.userTrim, title: c.title,
  userTitle: !!c.userTitle, postText: c.postText, userPost: !!c.userPost })
const same = (a: Snap, b: Snap) => EDIT_KEYS.every(k => a[k] === b[k])

export interface Edit { key: string; label: string; before: Snap; after: Snap; group?: string | undefined; t: number }
export interface History { undo: Edit[]; redo: Edit[] }

/** Record an edit. Edits in the same group to the same clip within 1.5 s are one step (typing, nudging). */
export function push(h: History, key: string, label: string, before: Snap, after: Snap, group?: string, now = Date.now()): History {
  if (same(before, after)) return h
  const last = h.undo.at(-1)
  const undo = group && last && last.group === group && last.key === key && now - last.t < 1500
    ? [...h.undo.slice(0, -1), { ...last, after, t: now }]
    : [...h.undo, { key, label, before, after, group, t: now }].slice(-200)
  return { undo, redo: [] }
}
