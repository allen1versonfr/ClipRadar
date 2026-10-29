import { describe, expect, it } from 'vitest'
import { Clip } from '../../api/schemas'
import { applied, fixSpans, flaggedList, previewPlan } from './checks'

const clip = (over: object) => Clip.parse({ id: 1, pid: 'p', start: 10, end: 30, ...over })
const flag = (status: string, level = 'low') => ({ safety: { status, level, open: status === 'needs_review' ? 1 : 0, flags: [{ fp: 'a', label: 'Swearing', text: 'x', status: 'open' }] } })

describe('content check', () => {
  it('the Flagged tab: needing review first, most serious first, by status when filtered', () => {
    const cs = [clip({ id: 1, ...flag('dismissed') }), clip({ id: 2, ...flag('needs_review', 'low') }), clip({ id: 3, ...flag('needs_review', 'high') }), clip({ id: 4 })]
    expect(flaggedList(cs, 'all', true).map(c => c.id)).toEqual([3, 2, 1])
    expect(flaggedList(cs, 'dismissed', true).map(c => c.id)).toEqual([1])
  })
  it('muted and bleeped stretches, with the one being tried', () => {
    const c = clip({ check: { fixes: [{ id: 'f', fp: 'a', kind: 'bleep', span: [12, 13] }, { id: 'g', fp: 'b', kind: 'mask_title' }] } })
    expect(fixSpans(c)).toEqual([[12, 13, 'bleep']])
    expect(fixSpans(c, { span: [20, 21], kind: 'mute' })).toEqual([[12, 13, 'bleep'], [20, 21, 'mute']])
  })
  it("the server's answer takes over the cut, title, caption and state it changed", () => {
    const c = clip({ title: 'Watch this' })
    expect(applied(c, { safety: { flags: [] }, title: 'W**** this', userTitle: true, state: 'todo' })).toMatchObject({ title: 'W**** this', userTitle: true })
    expect(applied(c, { start: 10, end: 25, userTrim: true })).toMatchObject({ end: 25, userTrim: true })
    expect(applied(c, { start: 10, end: 30 })).not.toHaveProperty('end')
  })
  it('trying a fix plays the part it changes', () => {
    const c = clip({})
    expect(previewPlan(c, 'bleep', {}, [15, 16])).toEqual({ from: 13, to: 18 })
    expect(previewPlan(c, 'cut_end', { start: 10, end: 20 })).toEqual({ from: 16, to: 20 })
    expect(previewPlan(c, 'mask_title', { title: 'W**** it' })).toMatchObject({ from: 10, to: 14, title: 'W**** it' })
  })
})
