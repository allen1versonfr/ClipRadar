import { describe, expect, it } from 'vitest'
import { Clip } from '../../api/schemas'
import { RENDER_DEFAULTS, type RenderOpts } from '../../prefs'
import { addLine, editLine, resetLines } from './captions'
import { chunksFor } from './draw'
import { snapshot } from './model'

const o = { ...RENDER_DEFAULTS } as RenderOpts
const heard = ['we', 'got', 'him', 'no', 'way'].map((w, i) => ({ start: 10 + i * 0.5, end: 10.4 + i * 0.5, word: w }))
const clip = Clip.parse({ id: 1, pid: 'p', start: 10, end: 20, words: heard })
const lines = (c: Partial<Clip>) => chunksFor({ ...clip, ...c } as Clip, o).map(r => r.words.map(w => w.word).join(' '))

describe('fixing captions', () => {
  it('a fixed word keeps its time; the original transcript is kept for Undo all edits', () => {
    const p = editLine(clip, 0, 'we got them', o)!
    expect(lines(p)).toEqual(['we got them', 'no way'])
    expect(p.words!.find(w => w.word === 'them')).toMatchObject({ start: 11, edited: true })
    expect(p.wordsEdited).toBe(true)
    expect(p.wordsOrig!.map(w => w.word)).toEqual(['we', 'got', 'him', 'no', 'way'])
    const back = resetLines({ ...clip, ...p } as Clip)
    expect(back.words!.map(w => w.word)).toEqual(['we', 'got', 'him', 'no', 'way'])
    expect(back.wordsEdited).toBe(false)
  })
  it('more or fewer words are spread over the line without running into the next; empty deletes the line', () => {
    const p = editLine(clip, 0, 'we really got him there', o)!
    const five = p.words!.filter(w => w.start < 11.5)
    expect(five.map(w => w.word)).toEqual(['we', 'really', 'got', 'him', 'there'])
    expect(five.at(-1)!.end).toBeLessThan(11.5)
    expect(lines(editLine(clip, 1, '', o)!)).toEqual(['we got him'])
  })
  it('a new caption goes at the playhead, after the line being said', () => {
    const p = addLine(clip, 15, o)
    expect(p.words!.filter(w => w.word === 'New' || w.word === 'caption').map(w => w.start)).toEqual([15, 15.6])
    expect(addLine(clip, 10.2, o).words!.find(w => w.word === "New")!.start).toBeCloseTo(11.5)       // where the next line starts
  })
  it('fixed captions are saved with the clip', () => {
    const c = { ...clip, ...editLine(clip, 0, 'we got them', o)! } as Clip
    expect(snapshot(c).words_edit!.map(w => w.word)).toEqual(['we', 'got', 'them', 'no', 'way'])
    expect(snapshot(clip).words_edit).toBeUndefined()
  })
})
