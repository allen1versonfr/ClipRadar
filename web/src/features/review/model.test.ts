import { describe, expect, it } from 'vitest'
import { Clip } from '../../api/schemas'
import { RENDER_DEFAULTS } from '../../prefs'
import { checkStatus, gate, keepBody, merge, nextTodo, postFor, prep, visible } from './model'

const clip = (over: object) => Clip.parse({ id: 1, pid: 'p', start: 10, end: 30, title: 'Chat lost it', ...over })

describe('Review rules', () => {
  it('lists clips by tab; gems only under Gems', () => {
    const cs = [clip({ id: 1 }), clip({ id: 2, state: 'skipped' }), clip({ id: 3, kind: 'gem' }), clip({ id: 4, state: 'kept' })]
    expect(visible(cs, 'todo').map(c => c.id)).toEqual([1])
    expect(visible(cs, 'skipped').map(c => c.id)).toEqual([2])
    expect(visible(cs, 'all').map(c => c.id)).toEqual([1, 2, 4])
    expect(visible(cs, 'gems').map(c => c.id)).toEqual([3])
  })

  it('the content check decides whether a clip renders now, after a warning, or not yet', () => {
    const flagged = clip({ safety: { status: 'needs_review', flags: [{ label: 'Swearing', text: 'x', status: 'open' }] } })
    expect(checkStatus(flagged, true)).toBe('needs_review')
    expect(gate(flagged, { enabled: true, export_flagged: 'ask' })).toBe('ask')
    expect(gate(flagged, { enabled: true, export_flagged: 'block' })).toBe('blocked')
    expect(gate(flagged, { enabled: true, export_flagged: 'allow' })).toBe('ok')
    expect(gate(flagged, { enabled: false, export_flagged: 'block' })).toBe('ok')
    expect(gate(clip({ check: { excluded: true } }), undefined)).toBe('excluded')
    expect(checkStatus(clip({}), false)).toBe('off')
  })

  it('the post caption is yours once you write one; tags follow the Hashtags switch', () => {
    const c = clip({ description: 'The dog walked in', hashtags: ['#dog', '#twitch'] })
    expect(postFor(c, true)).toBe('The dog walked in\n\n#dog #twitch')
    expect(postFor(c, false)).toBe('The dog walked in')
    expect(postFor({ ...c, userPost: true, postText: 'mine' }, true)).toBe('mine')
    expect(postFor(clip({ title: '', emoji: '😂', post: 'x\n\n#a #b' }), true)).toBe('Wait for it... 😂\n\n#a #b')
  })

  it('keeping sends your caption fixes, inside the clip', () => {
    const c = prep(clip({ wordsEdited: true, words: [{ start: 11, end: 12, word: 'heard' }],
      words_edit: [{ start: 5, end: 6, word: 'before' }, { start: 11, end: 12, word: 'fixed' }] }))
    const b = keepBody(c, 'post', RENDER_DEFAULTS)
    expect(b.clip.words).toEqual([{ start: 11, end: 12, word: 'fixed' }])
    expect(keepBody(clip({}), 'post', RENDER_DEFAULTS).clip.words).toBeUndefined()
    expect(b.opts).toBe(RENDER_DEFAULTS)
  })

  it('titles arrive in the background, but never over yours, and the cut only moves if you did not trim it', () => {
    const s = clip({ title: 'Heard title', start: 9.5, end: 31, listened: true })
    expect(merge(clip({}), s)).toMatchObject({ title: 'Heard title', start: 9.5, listened: true })
    expect(merge(clip({ title: 'Mine', userTitle: true, userTrim: true }), s)).toMatchObject({ title: 'Mine', start: 10, end: 30 })
    expect(merge(clip({ state: 'kept' }), s)).toMatchObject({ start: 10 })
  })

  it('after a keep or skip, the next clip to review of the same kind, wrapping round', () => {
    const cs = [clip({ id: 1 }), clip({ id: 2, state: 'skipped' }), clip({ id: 3, kind: 'gem' }), clip({ id: 4 })]
    expect(nextTodo(cs, cs[0]!)?.id).toBe(4)
    expect(nextTodo(cs, cs[3]!)?.id).toBe(1)
    expect(nextTodo([cs[0]!], cs[0]!)).toBeUndefined()
  })
})
