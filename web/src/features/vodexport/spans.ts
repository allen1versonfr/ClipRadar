import type { MusicSpan } from '../../api/schemas'
import { fmtTime } from '../exports/renderStatus'
import { plural } from '../../format'

/** "1:02:10 to 1:05:40 (3:30)" */
export const spanLabel = (s: MusicSpan) => `${fmtTime(s.start)} to ${fmtTime(s.end)} (${fmtTime(s.end - s.start)})`

/** What the copyright scan found and what will happen to it: "4 music stretches, 12:30 in all. 3 muted, 1 kept." */
export function musicSummary(spans: MusicSpan[], kept: Set<number>): string {
  if (!spans.length) return 'No music found.'
  const total = spans.reduce((t, s) => t + s.end - s.start, 0)
  const muted = spans.length - spans.filter((_, i) => kept.has(i)).length
  const what = muted === spans.length ? (spans.length === 1 ? 'Muted.' : 'All muted.')
    : muted === 0 ? (spans.length === 1 ? 'Kept.' : 'All kept.') : `${muted} muted, ${spans.length - muted} kept.`
  return `${plural(spans.length, 'music stretch', 'music stretches')}, ${fmtTime(total)} in all. ${what}`
}
