import { describe, expect, it } from 'vitest'
import { type Box, dragEdge, hitBox } from './VodTimeline'

const B = (id: string, start: number, end: number, lane: 0 | 1 = 0, edit = true): Box => ({ id, start, end, lane, kind: 'voice', edit })

describe('Export VOD and Segments timeline', () => {
  it('finds the edge under the pointer, within 7 px', () => {
    const boxes = [B('a', 10, 20), B('b', 30, 40)]
    expect(hitBox(boxes, 10.05, 0, 0.01)).toMatchObject({ box: { id: 'a' }, part: 's' })
    expect(hitBox(boxes, 19.95, -1, 0.01)).toMatchObject({ box: { id: 'a' }, part: 'e' })
    expect(hitBox(boxes, 15, 0, 0.01)).toMatchObject({ box: { id: 'a' }, part: 'body' })
    expect(hitBox(boxes, 15, 1, 0.01)).toBeNull()                         // the other row
    expect(hitBox(boxes, 25, 0, 0.01)).toBeNull()
  })
  it("a stretch that can't be moved has no edges to drag, but can still be picked", () => {
    expect(hitBox([B('a', 10, 20, 0, false)], 10, 0, 0.01)).toMatchObject({ part: 'body' })
  })
  it('the selected stretch wins where two meet', () => {
    const boxes = [B('a', 10, 20), B('b', 20, 30)]
    expect(hitBox(boxes, 20, 0, 0.01, 'b')).toMatchObject({ box: { id: 'b' }, part: 's' })
    expect(hitBox(boxes, 20, 0, 0.01, 'a')).toMatchObject({ box: { id: 'a' }, part: 'e' })
  })
  it('an edge stays inside the video and never crosses the other edge', () => {
    expect(dragEdge({ start: 10, end: 20 }, 's', -5, 100)).toEqual({ start: 0, end: 20 })
    expect(dragEdge({ start: 10, end: 20 }, 's', 25, 100)).toEqual({ start: 19.5, end: 20 })
    expect(dragEdge({ start: 10, end: 20 }, 'e', 150, 100)).toEqual({ start: 10, end: 100 })
    expect(dragEdge({ start: 10, end: 20 }, 'e', 3, 100)).toEqual({ start: 10, end: 10.5 })
  })
})
