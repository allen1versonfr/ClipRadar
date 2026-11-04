import { describe, expect, it } from 'vitest'
import { Clip } from '../../api/schemas'
import { RENDER_DEFAULTS, type RenderOpts } from '../../prefs'
import { camRects, capAnchor, captionAt, captionSize, chunksFor, dragPlace, drawLayout, fitText, layoutOf, maskWord, outSize, replaceWord, textWidth, titleSize } from './draw'

const o = (over: object = {}) => ({ ...RENDER_DEFAULTS, ...over }) as RenderOpts
const words = 'so um what was that! no way'.split(' ').map((w, i) => ({ start: 10 + i * 0.4, end: 10.3 + i * 0.4, word: w }))
const clip = Clip.parse({ id: 1, pid: 'p', start: 10, end: 20, words })

describe('captions, as the render groups them', () => {
  it('a line breaks at the words-per-line count and after a sentence end', () => {
    expect(chunksFor(clip, o()).map(c => c.words.map(w => w.word).join(' '))).toEqual(['so um what', 'was that!', 'no way'])
    expect(chunksFor(clip, o({ caption_filler: false, words_per_line: 2 })).map(c => c.words.length)).toEqual([2, 2, 2])
  })
  it('lines are balanced and never end on a word that leads into the next (the same cases as the render, 4.41)', () => {
    const cases: Array<[string, number, string[]]> = [
      ['so I went to the store yesterday.', 3, ['so I went', 'to the store', 'yesterday.']],
      ['this is the biggest burger I have ever seen', 3, ['this is', 'the biggest burger', 'I have', 'ever seen']],
      ['I had him, I had him one shot and then I walk off the cliff', 3, ['I had him,', 'I had him', 'one shot and', 'then I walk', 'off the cliff']],
      ['give me the remote right now please', 4, ['give me the remote', 'right now please']],
      ['we got him no way', 3, ['we got him', 'no way']],
    ]
    for (const [text, m, want] of cases) {
      const ws = text.split(' ').map((w, i) => ({ start: 10 + i * 0.3, end: 10.25 + i * 0.3, word: w }))
      const c = Clip.parse({ id: 1, pid: 'p', start: 10, end: 99, words: ws })
      expect(chunksFor(c, o({ words_per_line: m, caption_filler: true, caption_punct: true })).map(r => r.words.map(w => w.word).join(' '))).toEqual(want)
    }
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

describe('several facecams', () => {
  const calls = () => {
    const out: number[][] = []
    const g = { drawImage: (...a: unknown[]) => { out.push((a.slice(5) as number[]).map(Math.round)) } } as unknown as CanvasRenderingContext2D
    return { g, out }
  }
  const src = {} as CanvasImageSource
  it('the first is cam, then cams, at most 4', () => {
    expect(camRects(o())).toEqual([[0, 0, 0.25, 0.25]])
    const four = [[0.5, 0, 0.2, 0.2], [0, 0.5, 0.2, 0.2], [0.5, 0.5, 0.2, 0.2], [0.8, 0.8, 0.1, 0.1]]
    expect(camRects(o({ cams: four }))).toHaveLength(4)
  })
  it('Cam + game: side by side above the gameplay, as the render stacks them', () => {
    const { g, out } = calls()
    const capY = drawLayout(g, src, 1920, 1080, 540, 960, o({ cams: [[0.75, 0, 0.25, 0.25]] }), null, null)
    expect(out.slice(0, 2)).toEqual([[0, 0, 270, 317], [270, 0, 270, 317]])
    expect(out[2]![1]).toBe(317)                        // the game under them
    expect(capY).toBe(317)
  })
  it('Cam only: one above the other, captions on the first seam', () => {
    const { g, out } = calls()
    const capY = drawLayout(g, src, 1920, 1080, 540, 960, o({ layout: 'cam', cams: [[0.75, 0, 0.25, 0.25], [0, 0.75, 0.25, 0.25]] }), null, null)
    expect(out).toEqual([[0, 0, 540, 320], [0, 320, 540, 320], [0, 640, 540, 320]])
    expect(capY).toBe(320)
  })
})

describe('Text › Auto size, as the render sizes it (engine.fit_text)', () => {
  const longTitle = "I can't believe chat made me do this at three in the morning on a Tuesday with no sleep at all, again"
  it('picks the same sizes as the render (test_captions_anim.py checks these numbers too)', () => {
    expect(fitText('No way', 64, 900, 'classic', 1.3, 3)).toBe(83)
    expect(fitText(longTitle, 64, 900, 'classic', 1.3, 3)).toBe(40)
    expect(fitText('UNBELIEVABLEEEEE', 192, 900, 'luckiest', 1.3, 3)).toBe(122)
    expect(fitText('ABSOLUTELY UNBELIEVABLE', 154, 940, 'classic', 1, 2)).toBe(107)
  })
  it('a short title grows, a long one shrinks, and off is Text › Size', () => {
    expect(titleSize('No way', o())).toBe(83)
    expect(titleSize(longTitle, o())).toBe(40)
    expect(titleSize(longTitle, o({ title_fit: false }))).toBe(64)
  })
  it('a caption only ever shrinks, until its longest word fits', () => {
    expect(captionSize(['NO', 'WAY'], o({ caption_size: 250 }), 940)).toBe(215)
    expect(captionSize(['ABSOLUTELY', 'UNBELIEVABLE'], o({ caption_size: 250 }), 940)).toBeLessThan(155)
    expect(textWidth('UNBELIEVABLE', captionSize(['ABSOLUTELY', 'UNBELIEVABLE'], o({ caption_size: 250 }), 940), 'classic')).toBeLessThanOrEqual(940)
    expect(captionSize(['ABSOLUTELY', 'UNBELIEVABLE'], o({ caption_size: 250, caption_fit: false }), 940)).toBeCloseTo(215 * 18 / 23)      // the long-caption shrink alone
  })
})
