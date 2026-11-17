import type { MusicSpan } from '../../api/schemas'
import { fmtTime } from '../exports/renderStatus'
import { plural } from '../../format'

/** What happens to a stretch of music: its voices kept and the music taken out, all of it silenced, or left alone. */
export type MusicAction = 'voice' | 'mute' | 'keep'
export const ACTIONS: Array<[MusicAction, string]> = [['voice', 'Remove music'], ['mute', 'Mute all'], ['keep', 'Keep']]

/** "1:02:10 to 1:05:40 (3:30)" */
export const spanLabel = (s: Pick<MusicSpan, 'start' | 'end'>) => `${fmtTime(s.start)} to ${fmtTime(s.end)} (${fmtTime(s.end - s.start)})`

/** What the copyright scan found and what will happen to it:
 *  "4 music stretches, 12:30 in all. 2 with music removed, 1 muted, 1 kept." */
export function musicSummary(spans: MusicSpan[], acts: MusicAction[]): string {
  if (!spans.length) return 'No music found.'
  const total = spans.reduce((t, s) => t + s.end - s.start, 0)
  const n = (a: MusicAction) => acts.filter(x => x === a).length
  const all = (a: MusicAction) => n(a) === spans.length
  const what = all('voice') ? (spans.length === 1 ? 'Music removed, voices kept.' : 'All with music removed, voices kept.')
    : all('mute') ? (spans.length === 1 ? 'Muted.' : 'All muted.')
    : all('keep') ? (spans.length === 1 ? 'Kept.' : 'All kept.')
    : [n('voice') ? `${n('voice')} with music removed` : '', n('mute') ? `${n('mute')} muted` : '', n('keep') ? `${n('keep')} kept` : '']
      .filter(Boolean).join(', ') + '.'
  return `${plural(spans.length, 'music stretch', 'music stretches')}, ${fmtTime(total)} in all. ${what}`
}

export interface Word { start: number; end: number; word: string }

/** The caption on screen at time t: the group of `per` words it falls in, the way the export breaks them into lines. */
export function captionAt(words: Word[], t: number, per: number): Word[] {
  const n = Math.max(1, Math.round(per))
  for (let i = 0; i < words.length; i += n) {
    const group = words.slice(i, i + n)
    const next = words[i + n]
    const end = Math.min(group.at(-1)!.end + 0.6, next ? next.start : Infinity)
    if (t >= group[0]!.start && t < end) return group
    if (group[0]!.start > t) break
  }
  return []
}

/** Whether second t has been listened to already. */
export const heardAt = (covered: number[][], t: number) => covered.some(([a, b]) => t >= a! && t < b!)
