import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { getPref, RENDER_DEFAULTS, renderOpts, setPref } from './prefs'

afterEach(() => localStorage.clear())

describe('preferences', () => {
  it('use the same keys and format as the original page', () => {
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
  it("match the original page's DEFAULTS exactly (the server renders what it's sent)", () => {
    const appJs = readFileSync(resolve(process.cwd(), '..', 'src', 'ui', 'app.js'), 'utf8')     // tests run in web/
    const m = /const DEFAULTS = (\{[\s\S]*?\});/.exec(appJs)
    expect(m, 'DEFAULTS not found in src/ui/app.js').toBeTruthy()
    const original: unknown = new Function(`return (${m![1]})`)()
    expect(RENDER_DEFAULTS).toEqual(original)
  })
})
