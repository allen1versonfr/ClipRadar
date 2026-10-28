import { describe, expect, it } from 'vitest'
import { Clip } from '../../api/schemas'
import { RENDER_DEFAULTS, type RenderOpts } from '../../prefs'
import { capAnchor, captionAt, chunksFor, dragPlace, layoutOf, maskWord, outSize, replaceWord } from './draw'

const o = (over: object = {}) => ({ ...RENDER_DEFAULTS, ...over }) as RenderOpts
const words = 'so um what was that! no way'.split(' ').map((w, i) => ({ start: 10 + i * 0.4, end: 10.3 + i * 0.4, word: w }))
const clip = Clip.parse({ id: 1, pid: 'p', start: 10, end: 20, words })

describe('captions, as the render groups them', () => {
  it('a line breaks at the words-per-line count and after a sentence end', () => {
    expect(chunksFor(clip, o()).map(c => c.words.map(w => w.word).join(' '))).toEqual(['so um what', 'was that!', 'no way'])
    expect(chunksFor(clip, o({ caption_filler: false, words_per_line: 2 })).map(c => c.words.length)).toEqual([2, 2, 2])
  })
  it('the word being said is the highlighted one, in capitals, with replacements and timing offset', () => {
    const cap = captionAt(clip, 10.45, o(), { um: 'uh' })!
    expect(cap.texts).toEqual(['SO', 'UH', 'WHAT'])
    expect(cap.hi).toBe(1)
    expect(captionAt(clip, 10.45, o({ caption_offset: 200 }), {})!.hi).toBe(0)
    expect(captionAt(clip, 10.45, o({ caption_offset: 500 }), {})).toBeNull()        // moved later: nothing said yet
    expect(captionAt(clip, 30, o(), {})).toBeNull()
  })
  it('replacements keep capitals; masking keeps the first letter', () => {
    expect(replaceWord('Gonna!', { gonna: 'going to' })).toBe('Going to!')
    expect(replaceWord('GONNA', { gonna: 'going to' })).toBe('GOING TO')
    expect(maskWord('"damn"')).toBe('"d***"')
  })
})

describe('layout and placing', () => {
  it('shapes, and Cam + game on a wide frame uses the whole frame', () => {
    expect(outSize(o({ aspect: '1:1' }))).toEqual([1080, 1080])
    expect(layoutOf(o({ layout: 'split', aspect: '16:9' }))).toBe('full')
    expect(layoutOf(o({ layout: 'split', aspect: '9:16' }))).toBe('split')
  })
  it('captions sit clear of the edges; a drag places them by hand, snapping to the middle', () => {
    expect(capAnchor(100, 540, 960, o())).toEqual([270, 130])
    expect(capAnchor(500, 540, 960, o({ caption_pos: 'top' }))).toEqual([270, 960 * 0.22])
    expect(dragPlace('cap', 275, 0, 480, 540, 960, o())).toEqual({ patch: { caption_pos: 'custom', cap_x: 0.5, cap_y: 0.5 }, snapX: true })
    expect(dragPlace('title', 540, 960, 0, 540, 960, o()).patch).toEqual({ title_x: 0.7, title_y: 0.92 })
    expect(dragPlace('wm0', 54, 96, 96, 540, 960, o()).patch).toEqual({ pos: 'free', x: 0.1, y: 0.1 })
  })
})
