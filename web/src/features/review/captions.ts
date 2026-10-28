import type { Clip } from '../../api/schemas'
import type { RenderOpts } from '../../prefs'
import { chunksFor, clamp } from './draw'

// Review › Captions: fixing what Whisper heard (the original page's applyCapEdit, capAdd, capReset).
// A line you type keeps its words together (g), and is marked edited.

type Word = Clip['words'][number] & { g?: number; edited?: boolean }
let group = Date.now() % 1e6

/** Words to edit: the first edit keeps the original transcript, so Undo all edits can bring it back. */
function editable(c: Clip): Pick<Clip, 'words' | 'wordsEdited' | 'wordsOrig'> {
  return c.wordsEdited ? { words: c.words, wordsEdited: true, wordsOrig: c.wordsOrig }
    : { words: c.words.map(w => ({ ...w })), wordsEdited: true, wordsOrig: c.words.map(w => ({ ...w })) }
}

/** Line i says `text` now (empty deletes it). Same number of words: each keeps its time; otherwise the
 *  new words are spread over the line's own time, without running into the next line. */
export function editLine(c: Clip, i: number, text: string, o: RenderOpts): Partial<Clip> | null {
  const row = chunksFor(c, o)[i]
  if (!row) return null
  const e = editable(c), g = ++group
  const toks = text.trim().split(/\s+/).filter(Boolean)
  // the line's words, found in the words being edited (the rows were made from c.words)
  const same = (a: Word, b: Word) => a.start === b.start && a.end === b.end      // by time: the row's words may be copies
  const inRow = (w: Word) => row.words.some(r => same(r as Word, w))
  let fresh: Word[]
  if (toks.length === row.words.length) fresh = (row.words as Word[]).map((w, j) => ({ ...w, word: toks[j]!, g, edited: w.edited || toks[j] !== w.word }))
  else {
    const last = row.words.at(-1)!
    const after = (e.words as Word[]).find(w => w.start > last.start && !inRow(w))
    const a = row.words[0]!.start
    let b = Math.max(last.end, row.end)
    if (after) b = Math.min(b, after.start - 0.01)
    b = Math.max(b, a + 0.1)
    const d = (b - a) / Math.max(1, toks.length)
    fresh = toks.map((t, j) => ({ start: +(a + j * d).toFixed(3), end: +(a + (j + 1) * d - 0.01).toFixed(3), word: t, g, edited: true }))
  }
  return { ...e, words: (e.words as Word[]).filter(w => !inRow(w)).concat(fresh).sort((x, y) => x.start - y.start) }
}

/** "New caption" at the playhead (after the line being said, if any), up to the next word. */
export function addLine(c: Clip, at: number, o: RenderOpts): Partial<Clip> {
  const e = editable(c), g = ++group
  let t = clamp(at, c.start, c.end - 1)
  const inRow = chunksFor(c, o).find(r => t >= r.start && t < r.end)
  if (inRow) t = Math.min(inRow.end, c.end - 0.4)
  const next = e.words.find(w => w.start > t + 0.01)
  const len = Math.max(0.4, Math.min(1.2, (next ? next.start - 0.01 : c.end) - t))
  const nw: Word[] = [{ start: +t.toFixed(3), end: +(t + len / 2).toFixed(3), word: 'New', g, edited: true },
    { start: +(t + len / 2).toFixed(3), end: +(t + len).toFixed(3), word: 'caption', g, edited: true }]
  return { ...e, words: e.words.concat(nw).sort((x, y) => x.start - y.start) }
}

/** Undo all edits: back to what Whisper heard. */
export const resetLines = (c: Clip): Partial<Clip> => ({ words: c.wordsOrig ?? c.words, wordsEdited: false, wordsOrig: undefined })

export const fmtT = (t: number) => { t = Math.max(0, t); const m = Math.floor(t / 60), s = t - m * 60; return `${m}:${s < 10 ? '0' : ''}${s.toFixed(1)}` }
