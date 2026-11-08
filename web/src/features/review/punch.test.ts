import { describe, expect, it } from 'vitest'
import { renderOpts } from '../../prefs'
import { outSize, titleTop, titleTopPx } from './draw'
import { PUNCH_MAX, punchEnvelope, punchFocus, punchZoom } from './punch'

// Known values from the render, worked out in Python (4.47.1): ZOOM evaluates the z expression engine.punch_filter
// gives zoompan, at each time; TITLE is engine.title_top for each case. Regenerate them there if engine.py changes.

const ZOOM: Array<{ punch: string; beats: number[]; at: Array<[number, number]> }> = [
  {"punch": "strong", "beats": [3.0], "at": [[0, 1.0], [2.0, 1.0], [2.82, 1.0], [2.9, 1.058381344], [2.91, 1.07], [3.0, 1.14], [3.5, 1.14], [4.1, 1.14], [4.5, 1.055102041], [4.8, 1.0], [4.9, 1.0], [5.0, 1.0], [6.4, 1.0], [7.0, 1.0], [7.25, 1.0], [8.6, 1.0], [9.0, 1.0], [3.4, 1.14], [20.0, 1.0]]},
  {"punch": "subtle", "beats": [2.5, 7.25], "at": [[0, 1.0], [2.0, 1.0], [2.82, 1.08], [2.9, 1.08], [2.91, 1.08], [3.0, 1.08], [3.5, 1.08], [4.1, 1.015860058], [4.5, 1.0], [4.8, 1.0], [4.9, 1.0], [5.0, 1.0], [6.4, 1.0], [7.0, 1.0], [7.25, 1.08], [8.6, 1.056676385], [9.0, 1.001166181], [3.4, 1.08], [20.0, 1.0]]},
  {"punch": "strong", "beats": [2.0, 3.5], "at": [[0, 1.0], [2.0, 1.14], [2.82, 1.14], [2.9, 1.14], [2.91, 1.14], [3.0, 1.14], [3.5, 1.14], [4.1, 1.14], [4.5, 1.14], [4.8, 1.112244898], [4.9, 1.084897959], [5.0, 1.055102041], [6.4, 1.0], [7.0, 1.0], [7.25, 1.0], [8.6, 1.0], [9.0, 1.0], [3.4, 1.084897959], [20.0, 1.0]]},
  {"punch": "subtle", "beats": [1.6, 6.0, 10.4, 15.0, 20.0], "at": [[0, 1.0], [2.0, 1.08], [2.82, 1.073753003], [2.9, 1.064139942], [2.91, 1.06272], [3.0, 1.04851312], [3.5, 1.0], [4.1, 1.0], [4.5, 1.0], [4.8, 1.0], [4.9, 1.0], [5.0, 1.0], [6.4, 1.08], [7.0, 1.08], [7.25, 1.070553936], [8.6, 1.0], [9.0, 1.0], [3.4, 1.0], [20.0, 1.0]]},
  {"punch": "off", "beats": [3.0], "at": [[0, 1.0], [2.0, 1.0], [2.82, 1.0], [2.9, 1.0], [2.91, 1.0], [3.0, 1.0], [3.5, 1.0], [4.1, 1.0], [4.5, 1.0], [4.8, 1.0], [4.9, 1.0], [5.0, 1.0], [6.4, 1.0], [7.0, 1.0], [7.25, 1.0], [8.6, 1.0], [9.0, 1.0], [3.4, 1.0], [20.0, 1.0]]},
  {"punch": "strong", "beats": [], "at": [[0, 1.0], [2.0, 1.0], [2.82, 1.0], [2.9, 1.0], [2.91, 1.0], [3.0, 1.0], [3.5, 1.0], [4.1, 1.0], [4.5, 1.0], [4.8, 1.0], [4.9, 1.0], [5.0, 1.0], [6.4, 1.0], [7.0, 1.0], [7.25, 1.0], [8.6, 1.0], [9.0, 1.0], [3.4, 1.0], [20.0, 1.0]]},
]

const TITLE: Array<{ title: string; title_y: number; title_font: string; fs: number; wrap: number; pad: number; ty: number;
  face: { face_y: number | null; face_h: number | null } | null; cap: number; want: number }> = [
  {"title": "He did not just do that", "title_y": 0.05, "title_font": "classic", "fs": 80, "wrap": 820, "pad": 20, "ty": 116, "face": null, "cap": 1497.6000000000001, "want": 116},
  {"title": "He did not just do that", "title_y": 0.05, "title_font": "classic", "fs": 80, "wrap": 820, "pad": 20, "ty": 116, "face": {"face_y": 0.5, "face_h": 0.15}, "cap": 1497.6000000000001, "want": 116},
  {"title": "He did not just do that", "title_y": 0.05, "title_font": "classic", "fs": 80, "wrap": 820, "pad": 20, "ty": 116, "face": {"face_y": 0.12, "face_h": 0.1}, "cap": 1497.6000000000001, "want": 369},
  {"title": "Short one", "title_y": 0.05, "title_font": "classic", "fs": 80, "wrap": 820, "pad": 20, "ty": 116, "face": {"face_y": 0.21, "face_h": 0.1}, "cap": 1497.6000000000001, "want": 74},
  {"title": "A much longer title that wraps onto three whole lines of text here for sure", "title_y": 0.05, "title_font": "classic", "fs": 80, "wrap": 820, "pad": 20, "ty": 116, "face": {"face_y": 0.3, "face_h": 0.12}, "cap": 1497.6000000000001, "want": 738},
  {"title": "He did not just do that", "title_y": 0.1, "title_font": "classic", "fs": 80, "wrap": 820, "pad": 20, "ty": 116, "face": {"face_y": 0.12, "face_h": 0.1}, "cap": 1497.6000000000001, "want": 116},
  {"title": "He did not just do that", "title_y": 0.05, "title_font": "classic", "fs": 80, "wrap": 820, "pad": 20, "ty": 116, "face": {"face_y": 0.45, "face_h": 0.8}, "cap": 1497.6000000000001, "want": 116},
  {"title": "He did not just do that", "title_y": 0.05, "title_font": "classic", "fs": 80, "wrap": 820, "pad": 20, "ty": 116, "face": {"face_y": 0.12, "face_h": 0.1}, "cap": 600, "want": 116},
  {"title": "WAIT WHAT 100", "title_y": 0.05, "title_font": "anton", "fs": 96, "wrap": 700, "pad": 24, "ty": 116, "face": {"face_y": 0.2, "face_h": 0.14}, "cap": 1497.6000000000001, "want": 569},
  {"title": "no face numbers", "title_y": 0.05, "title_font": "classic", "fs": 80, "wrap": 820, "pad": 20, "ty": 116, "face": {"face_y": null, "face_h": 0.1}, "cap": 1497.6000000000001, "want": 116},
  {"title": "out of frame", "title_y": 0.05, "title_font": "classic", "fs": 80, "wrap": 820, "pad": 20, "ty": 116, "face": {"face_y": 1.2, "face_h": 0.1}, "cap": 1497.6000000000001, "want": 116},
  {"title": "Outline title here", "title_y": 0.05, "title_font": "classic", "fs": 80, "wrap": 820, "pad": 20, "ty": 96, "face": {"face_y": 0.13, "face_h": 0.09}, "cap": 1497.6000000000001, "want": 377},
  {"title": "Huge font that wraps", "title_y": 0.05, "title_font": "nope", "fs": 150, "wrap": 500, "pad": 37, "ty": 116, "face": {"face_y": 0.25, "face_h": 0.1}, "cap": 1497.6000000000001, "want": 619},
]

describe('punch-in zoom (engine.punch_filter)', () => {
  for (const z of ZOOM) {
    it(`${z.punch} on ${JSON.stringify(z.beats)}`, () => {
      for (const [t, want] of z.at) expect(punchZoom(z.punch, z.beats, t)).toBeCloseTo(want, 9)
    })
  }
  it('the envelope rises, holds and falls', () => {
    expect(punchEnvelope([3], 3 - 0.18)).toBe(0)
    expect(punchEnvelope([3], 3)).toBe(1)
    expect(punchEnvelope([3], 3 + 1.1)).toBe(1)
    expect(punchEnvelope([3], 3 + 1.1 + 0.7)).toBe(0)
    expect(punchEnvelope([3], 3 + 1.1 + 0.35)).toBeCloseTo(0.5, 9)
  })
  it('off, unknown or no beats: no zoom', () => {
    expect(punchZoom('off', [3], 3)).toBe(1)
    expect(punchZoom(undefined, [3], 3)).toBe(1)
    expect(punchZoom('huge', [3], 3)).toBe(1)
    expect(punchZoom('strong', null, 3)).toBe(1)
    expect(punchZoom('strong', [], 3)).toBe(1)
  })
  it(`at most ${PUNCH_MAX} beats`, () => expect(punchZoom('strong', [2, 6, 10, 14, 18], 18.5)).toBe(1))
  it('where it pushes in: the face on Whole frame, 0.4 on a facecam, the middle on Blur fit and Square blur', () => {
    expect(punchFocus('full', 0.3, 1920)).toBe(0.3)
    expect(punchFocus('full', 576, 1920)).toBeCloseTo(0.3, 9)
    expect(punchFocus('full', null, 1920)).toBe(0.4)
    expect(punchFocus('cam', 0.3, 1920)).toBe(0.4)
    expect(punchFocus('split', 0.3, 1920)).toBe(0.4)
    expect(punchFocus('blur', 0.3, 1920)).toBe(0.5)
    expect(punchFocus('square', null, 1920)).toBe(0.5)
  })
})

describe('a title kept off the face (engine.title_top)', () => {
  for (const c of TITLE) {
    it(`${c.title} / ${JSON.stringify(c.face)}${c.title_y !== 0.05 ? ' (dragged)' : ''}`, () => {
      expect(titleTop(c.title, { title_y: c.title_y, title_font: c.title_font }, 1920, c.fs, c.wrap, c.pad, c.ty, c.face, c.cap)).toBe(c.want)
    })
  }
  it('the preview draws it there, and leaves a title without a face alone', () => {
    const o = { ...renderOpts(), layout: 'full', aspect: '9:16', title_y: 0.05, title_style: 'box' }
    const [W, H] = outSize(o), Wd = W / 2, Ht = H / 2, face = { face_y: 0.12, face_h: 0.1 }
    expect(titleTopPx('He did not just do that', Wd, Ht, o, null, Ht * 0.7)).toBeCloseTo(0.05 * Ht, 9)
    const moved = titleTopPx('He did not just do that', Wd, Ht, o, face, Ht * 0.7)
    expect(moved).toBeGreaterThan((0.12 + 0.1 * 0.6) * Ht)
    expect(titleTopPx('He did not just do that', Wd, Ht, { ...o, title_y: 0.1 }, face, Ht * 0.7)).toBeCloseTo(0.1 * Ht, 9)
  })
})
