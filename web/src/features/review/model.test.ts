import { describe, expect, it } from 'vitest'
import { Clip } from '../../api/schemas'
import { RENDER_DEFAULTS } from '../../prefs'
import { checkStatus, fromYtView, gate, keepBody, merge, nextTodo, postFor, prep, qClass, queueFor, snapshot, visible, ytOpts, ytView } from './model'

const clip = (over: object) => Clip.parse({ id: 1, pid: 'p', start: 10, end: 30, title: 'Chat lost it', ...over })

describe('score grades', () => {
  it('80+ green, 70+ yellow, under 50 red, 50 to 69 plain', () => {
    expect([100, 80, 79, 70, 69, 50, 49, 1].map(qClass)).toEqual(['top', 'top', 'good', 'good', 'fair', 'fair', 'low', 'low'])
  })
})

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
    // restoring the cut turns your trim off; until that's saved, the server's older cut mustn't come back
    expect(merge(clip({ userTrim: false, start: 5, end: 25 }), clip({ start: 12, end: 28 }), true)).toMatchObject({ start: 5, end: 25 })
  })

  it('after a keep or skip, the next clip to review of the same kind, wrapping round', () => {
    const cs = [clip({ id: 1 }), clip({ id: 2, state: 'skipped' }), clip({ id: 3, kind: 'gem' }), clip({ id: 4 })]
    expect(nextTodo(cs, cs[0]!)?.id).toBe(4)
    expect(nextTodo(cs, cs[3]!)?.id).toBe(1)
    expect(nextTodo([cs[0]!], cs[0]!)).toBeUndefined()
  })

  it('5.1: YouTube too asks for a YouTube edit; the clip follows its TikTok render', () => {
    const c = clip({})
    expect(keepBody(c, 'post', RENDER_DEFAULTS, true, true)).toMatchObject({ youtube: true })
    expect('youtube' in keepBody(c, 'post', RENDER_DEFAULTS)).toBe(false)
    const yt = { pid: c.pid, clip_id: c.id, platform: 'youtube', id: 'y' }, tt = { pid: c.pid, clip_id: c.id, platform: 'tiktok', id: 't' }
    expect(queueFor(c, [yt, tt] as never)?.id).toBe('t')
    expect(queueFor(c, [yt] as never)?.id).toBe('y')
  })
})

describe('the YouTube edit (5.4)', () => {
  const base = Clip.parse({ id: 1, pid: 'p', start: 10, end: 20, title: 'TikTok title', postText: '', gain: 0,
    words: [{ start: 11, end: 11.5, word: 'hey' }] })
  it('starts as the TikTok edit, and follows it until changed', () => {
    const v = ytView(base)
    expect(v).toMatchObject({ start: 10, end: 20, title: 'TikTok title', userTrim: false, userTitle: false, postText: '' })
    expect(ytView({ ...base, title: 'New', start: 12 })).toMatchObject({ title: 'New', start: 12 })
  })
  it('an edit made to it stays its own, and the TikTok edit is untouched', () => {
    const c = fromYtView(base, { ...ytView(base), start: 8, end: 25, userTrim: true, title: 'YT', userTitle: true, gain: 3 })
    expect(c).toMatchObject({ start: 10, end: 20, title: 'TikTok title', gain: 0 })
    expect(ytView(c)).toMatchObject({ start: 8, end: 25, title: 'YT', gain: 3 })
    expect(ytView({ ...c, start: 0, title: 'Other' })).toMatchObject({ start: 8, title: 'YT' })
  })
  it('what belongs to the moment (kept, the content check) goes to the moment, and a round trip changes nothing', () => {
    const c = fromYtView(base, { ...ytView(base), state: 'kept' })
    expect(c.state).toBe('kept')
    expect(ytView(fromYtView(c, ytView(c)))).toEqual(ytView(c))
    expect(c.yt).toMatchObject({ userTrim: false, userTitle: false })
  })
  it('its captions can be fixed on their own', () => {
    const fixed = [{ start: 11, end: 11.5, word: 'hello' }]
    const c = fromYtView(base, { ...ytView(base), words: fixed })
    expect(c.words).toEqual(base.words)
    expect(ytView(c).words).toEqual(fixed)
    expect(snapshot(c).yt?.words_edit).toEqual([{ start: 11, end: 11.5, word: 'hello', g: undefined, edited: false }])
  })
  it('Keep sends both edits, each as made', () => {
    const c = fromYtView(base, { ...ytView(base), end: 30, userTrim: true, postText: 'desc', userPost: true })
    const b = keepBody(c, 'post', { aspect: '9:16' } as never, true, { opts: { aspect: '16:9' } as never }) as Record<string, any>
    expect(b.clip).toMatchObject({ start: 10, end: 20 })
    expect(b.youtube.clip).toMatchObject({ start: 10, end: 30 })
    expect(b.youtube.text).toEqual({ description: 'desc' })
  })
  it('its settings are YouTube-shaped, with what was changed for it over them', () => {
    const o = ytOpts({ aspect: '9:16', layout: 'split', track: 'auto', punch: 'beat', caption_size: 90 } as never, { caption_size: 60 }, false)
    expect(o).toMatchObject({ aspect: '16:9', layout: 'blur', track: 'off', punch: 'off', caption_size: 60 })
    expect(ytOpts({} as never, { layout: 'full', aspect: '9:16' } as never, false)).toMatchObject({ layout: 'full', aspect: '16:9' })
  })
})
