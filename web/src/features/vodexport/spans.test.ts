import { describe, expect, it } from 'vitest'
import type { MusicSpan } from '../../api/schemas'
import { captionAt, heardAt, musicSummary, spanLabel } from './spans'

const S = (start: number, end: number): MusicSpan => ({ start, end, confidence: 0.9, speech: 0, suggest: 'mute' })

describe('music stretches', () => {
  it('says what the scan found and what happens to it', () => {
    const two = [S(60, 240), S(3600, 3660)]
    expect(musicSummary([], [])).toBe('No music found.')
    expect(musicSummary(two, ['mute', 'mute'])).toBe('2 music stretches, 4:00 in all. All muted.')
    expect(musicSummary(two, ['voice', 'voice'])).toBe('2 music stretches, 4:00 in all. All erased.')
    expect(musicSummary(two, ['voice', 'keep'])).toBe('2 music stretches, 4:00 in all. 1 erased, 1 kept.')
    expect(musicSummary([S(0, 30)], ['keep'])).toBe('1 music stretch, 0:30 in all. Kept.')
    expect(spanLabel(S(3730, 3940))).toBe('1:02:10 to 1:05:40 (3:30)')
  })
})

describe('the preview caption', () => {
  const words = [0, 1, 2, 3, 4, 5].map(i => ({ start: 10 + i * 0.5, end: 10.4 + i * 0.5, word: `w${i}` }))
  it('shows the group of words being said, broken the way the export breaks them', () => {
    expect(captionAt(words, 10.2, 3).map(w => w.word)).toEqual(['w0', 'w1', 'w2'])
    expect(captionAt(words, 11.6, 3).map(w => w.word)).toEqual(['w3', 'w4', 'w5'])
    expect(captionAt(words, 9, 3)).toEqual([])
    expect(captionAt(words, 20, 3)).toEqual([])
  })
  it('knows what has been listened to', () => {
    expect(heardAt([[0, 60]], 30)).toBe(true)
    expect(heardAt([[0, 60]], 61)).toBe(false)
  })
})
