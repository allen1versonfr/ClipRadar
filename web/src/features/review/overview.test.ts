import { describe, expect, it } from 'vitest'
import { Clip } from '../../api/schemas'
import { clipAt, filterFor, heatLevel, markers } from './overview'
import { trimNote } from './StreamTools'

const clip = (id: number, start: number, extra: Record<string, unknown> = {}) => Clip.parse({ id, pid: 'p', start, end: start + 10, ...extra })

describe('stream overview', () => {
  const clips = [clip(1, 100), clip(2, 105), clip(3, 400), clip(1, 300, { kind: 'gem' })]
  it('finds the clip at a moment', () => {
    expect(clipAt(clips, 104)?.id).toBe(1)
    expect(clipAt(clips, 200)).toBeUndefined()
  })
  it('numbers hype clips and gems apart, the open one first, none crowded', () => {
    // 1000 px for 1000 s: clips 1 and 2 are 5 px apart, so only one gets a number
    expect(markers(clips, undefined, 1000, 1000).map(m => m.label)).toEqual(['1', '3', 'G1'])
    expect(markers(clips, 'p:2', 1000, 1000).map(m => m.label)).toEqual(['2', '3', 'G1'])
  })
  it('opens a clip in the list it belongs to', () => {
    expect(filterFor(clips[3]!, 'todo')).toBe('gems')
    expect(filterFor(clips[0]!, 'gems')).toBe('todo')
    expect(filterFor({ ...clips[0]!, state: 'kept' }, 'todo')).toBe('all')
    expect(filterFor({ ...clips[0]!, state: 'skipped' }, 'skipped')).toBe('skipped')
  })
  it('shades the hottest moments', () => expect([0.2, 0.4, 0.9].map(heatLevel)).toEqual([0, 1, 2]))
  it('says what ranking learned from your trims, once there are enough', () => {
    expect(trimNote({ n: 4, start: -2, end: 0 })).toBe('')
    expect(trimNote({ n: 8, start: -2, end: 1 })).toBe('Clips start 2 s earlier, end 1 s later')
  })
})
