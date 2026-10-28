import { describe, expect, it } from 'vitest'
import { bounds, fmtA, frameStep, push, rulerStep, snap, type Snap } from './edit'

const s = (over: Partial<Snap> = {}): Snap => ({ start: 10, end: 30, userTrim: false, title: 'T', userTitle: false, postText: '', userPost: false, words: [], wordsEdited: false, wordsOrig: undefined, ...over })

describe('trimming', () => {
  it('keeps a clip inside the video and at least a second long', () => {
    expect(bounds({ start: 10, end: 30 }, 12, 30, 100)).toEqual({ start: 12, end: 30 })
    expect(bounds({ start: 10, end: 30 }, -5, 30, 100)).toEqual({ start: 0, end: 30 })
    expect(bounds({ start: 10, end: 30 }, 29.8, 30, 100)).toEqual({ start: 29.8, end: 30.8 })
    expect(bounds({ start: 10, end: 30 }, 10, 140, 100)).toEqual({ start: 10, end: 100 })
    expect(bounds({ start: 10, end: 30 }, 10, 30, 100)).toBeNull()
  })
  it('a dragged edge snaps to a word or the playhead within 6 px, not with Alt', () => {
    const words = [{ start: 12, end: 12.4, word: 'hi' }]
    expect(snap(11.97, words, 50, 0.01)).toBeCloseTo(11.95)
    expect(snap(12.5, words, 50, 0.01)).toBeCloseTo(12.45)
    expect(snap(49.98, words, 50, 0.01)).toBe(50)
    expect(snap(11.97, words, 50, 0.01, true)).toBe(11.97)
    expect(snap(20, words, 50, 0.01)).toBe(20)
  })
  it('frames, the ruler and stream time', () => {
    expect(frameStep(1, 30, 1)).toBeCloseTo(31 / 30 + 0.25 / 30)
    expect(frameStep(0, 30, -1)).toBe(0)
    expect(rulerStep(60, 900)).toBe(10)
    expect(fmtA(3723.456)).toBe('1:02:03.46')
    expect(fmtA(5.5)).toBe('0:05.50')
  })
})

describe('undo history', () => {
  it('one step per edit; typing and nudging close together are one step; a new edit clears redo', () => {
    let h = push({ undo: [], redo: [] }, 'p:1', 'move start', s(), s({ start: 11 }), 'trim-s', 1000)
    h = push(h, 'p:1', 'move start', s({ start: 11 }), s({ start: 12 }), 'trim-s', 1500)
    expect(h.undo).toHaveLength(1)
    expect(h.undo[0]!.before.start).toBe(10)
    expect(h.undo[0]!.after.start).toBe(12)
    h = push(h, 'p:1', 'move start', s({ start: 12 }), s({ start: 13 }), 'trim-s', 4000)
    expect(h.undo).toHaveLength(2)
    expect(push(h, 'p:1', 'same', s(), s())).toBe(h)
    expect(push({ undo: h.undo, redo: [h.undo[0]!] }, 'p:2', 'title', s(), s({ title: 'U' })).redo).toEqual([])
  })
})
