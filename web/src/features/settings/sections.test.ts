import { describe, expect, it } from 'vitest'
import { matches, SECTIONS } from './sections'

describe('settings sections', () => {
  const by = (id: string) => SECTIONS.find(s => s.id === id)!
  it('every section has a place, and ids are unique', () => {
    expect(new Set(SECTIONS.map(s => s.id)).size).toBe(SECTIONS.length)
  })
  it('a search matches the title, the words people use, or what the section says; every word typed counts', () => {
    expect(matches(by('watermark'), 'logo')).toBe(true)
    expect(matches(by('watermark'), 'Watermark')).toBe(true)
    expect(matches(by('tiktok'), 'press post')).toBe(true)
    expect(matches(by('tiktok'), 'press discord')).toBe(false)
    expect(matches(by('storage'), 'anything', 'Delete anything older')).toBe(true)
    expect(matches(by('updates'), '  ')).toBe(true)
  })
})
