import { describe, expect, it } from 'vitest'
import { addCut, editLen, gainToVolume, kept, linesFrom, move, pastCut, playlist, step, type Segment } from './model'

const seg = (p: Partial<Segment>): Segment => ({ id: 'a', name: '', start: 0, end: 60, cuts: [], gain_db: 0, on: true, lines: null, ...p })

describe('kept', () => {
  it('is the whole segment with no cuts', () => expect(kept(seg({ start: 10, end: 40 }))).toEqual([[10, 40]]))
  it('leaves the cuts out, in order, whatever order they were made in', () =>
    expect(kept(seg({ cuts: [[40, 50], [10, 20]] }))).toEqual([[0, 10], [20, 40], [50, 60]]))
  it('drops what a cut leaves too short to see', () => expect(kept(seg({ cuts: [[0.2, 60]] }))).toEqual([]))
  it('overlapping cuts count once', () => expect(kept(seg({ cuts: [[10, 30], [20, 40]] }))).toEqual([[0, 10], [40, 60]]))
})

describe('the edit', () => {
  const segs = [seg({ id: 'a', start: 100, end: 160, cuts: [[110, 120]] }), seg({ id: 'b', start: 10, end: 40, on: false }), seg({ id: 'c', start: 500, end: 530 })]
  it('is as long as the ticked segments, cuts left out', () => expect(editLen(segs)).toBe(50 + 30))
  it('plays the ticked segments in their order, not the video\'s', () =>
    expect(playlist(segs)).toEqual([{ seg: 'a', a: 100, b: 110 }, { seg: 'a', a: 120, b: 160 }, { seg: 'c', a: 500, b: 530 }]))
  it('jumps to the next part at the end of one, and stops after the last', () => {
    const list = playlist(segs)
    expect(step(list, 0, 105)).toEqual({ i: 0 })
    expect(step(list, 0, 110)).toEqual({ i: 1, seek: 120 })
    expect(step(list, 2, 530)).toEqual({ i: 2, stop: true })
  })
})

describe('editing', () => {
  it('moves a segment', () => expect(move(['a', 'b', 'c'], 2, 0)).toEqual(['c', 'a', 'b']))
  it('leaves the order alone for a move off the end', () => expect(move(['a', 'b'], 1, 2)).toEqual(['a', 'b']))
  it('joins a cut with one it touches, and keeps it inside the segment', () => {
    const s = addCut(addCut(seg({ start: 10, end: 50 }), 20, 30), 28, 60)
    expect(s.cuts).toEqual([[20, 50]])
  })
  it('skips a cut while a segment plays', () => {
    expect(pastCut(seg({ cuts: [[10, 20]] }), 12)).toBe(20)
    expect(pastCut(seg({ cuts: [[10, 20]] }), 25)).toBeNull()
  })
  it('turns a segment down in the player, never up', () => {
    expect(gainToVolume(0)).toBe(1)
    expect(gainToVolume(6)).toBe(1)
    expect(gainToVolume(-6)).toBeCloseTo(0.501, 2)
  })
})

describe('caption lines', () => {
  const words = [{ start: 1, end: 1.4, word: 'no' }, { start: 1.5, end: 1.9, word: 'way' }, { start: 2, end: 2.3, word: 'he' }, { start: 5, end: 5.5, word: 'did' }]
  it('groups the words heard in the segment, each line shown until the next (the last a moment past its last word)', () =>
    expect(linesFrom(words, 2, 0, 10)).toEqual([{ start: 1, end: 2, text: 'no way' }, { start: 2, end: 6.1, text: 'he did' }]))
  it('leaves out words outside the segment', () => expect(linesFrom(words, 3, 1.8, 4).map(l => l.text)).toEqual(['he']))
})
