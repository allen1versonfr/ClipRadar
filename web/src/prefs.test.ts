import { afterEach, describe, expect, it } from 'vitest'
import { getPref, RENDER_DEFAULTS, renderOpts, setPref } from './prefs'

afterEach(() => localStorage.clear())

describe('preferences', () => {
  it('use the vc_ keys and JSON format every version has saved', () => {
    setPref('cw', 55)
    expect(localStorage.getItem('vc_cw')).toBe('55')
    localStorage.setItem('vc_src', JSON.stringify('https://twitch.tv/videos/1'))
    expect(getPref('src', '')).toBe('https://twitch.tv/videos/1')
  })
  it('fall back when nothing or something unreadable is stored', () => {
    expect(getPref('nope', 7)).toBe(7)
    localStorage.setItem('vc_bad', '{not json')
    expect(getPref('bad', 'x')).toBe('x')
  })
  it('render options are the defaults with your saved choices on top', () => {
    localStorage.setItem('vc_opts', JSON.stringify({ layout: 'blur', caption_color: '#ffffff' }))
    const o = renderOpts()
    expect(o.layout).toBe('blur')
    expect(o.caption_color).toBe('#ffffff')
    expect(o.words_per_line).toBe(3)
  })
  it("don't change by accident (the server renders what it's sent)", () => {
    // the original page's DEFAULTS when it was removed (4.43.0): change these only on purpose
    expect(RENDER_DEFAULTS).toEqual({ layout: 'split', cam: [0, 0, 0.25, 0.25], cams: [], cam_height: 0.33, game_x: 0.5, game_zoom: 1.0,
      captions: true, caption_pos: 'auto', caption_color: '#ffe600', caption_size: 100, words_per_line: 3, uppercase: true,
      caption_style: 'pop', caption_font: 'classic', caption_anim: true, caption_motion: 'pop', caption_speed: 100,
      caption_emph: false, caption_filler: true, caption_punct: true, caption_spacing: 100, caption_offset: 0, cap_x: 0.5, cap_y: 0.7,
      title_style: 'box', title_font: 'classic', title_size: 100, title_color: '#ffffff', title_outline: 100, title_x: 0.5, title_y: 0.05, title_fit: true, caption_fit: true,
      whisper_model: 'small', language: '', hw_encode: true, aspect: '9:16', cap_outline: 100, cap_shadow: 100,
      track: 'auto', track_target: 'auto', focus_y: 0.4 })
  })
})
