import { describe, expect, it } from 'vitest'
import { musicSummary, spanLabel } from './spans'

const S = (start: number, end: number) => ({ start, end, confidence: 0.8 })

describe('copyright scan wording', () => {
  it('labels a stretch with its times and length', () => {
    expect(spanLabel(S(3730, 3940))).toBe('1:02:10 to 1:05:40 (3:30)')
  })
  it('says how much music there is and what happens to it', () => {
    const spans = [S(0, 60), S(100, 130)]
    expect(musicSummary(spans, new Set())).toBe('2 music stretches, 1:30 in all. All muted.')
    expect(musicSummary(spans, new Set([1]))).toBe('2 music stretches, 1:30 in all. 1 muted, 1 kept.')
    expect(musicSummary(spans, new Set([0, 1]))).toBe('2 music stretches, 1:30 in all. All kept.')
    expect(musicSummary([S(0, 10)], new Set())).toBe('1 music stretch, 0:10 in all. Muted.')
    expect(musicSummary([], new Set())).toBe('No music found.')
  })
})
