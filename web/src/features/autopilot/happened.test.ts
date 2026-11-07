import { describe, expect, it } from 'vitest'
import { happened } from './happened'

const e = (t: number, step: string, outcome: string, why?: string) => ({ t, event: 'render_done', step, outcome, ms: 0, ...(why ? { why } : {}) })

describe('What happened', () => {
  it('says nothing when there is nothing', () => {
    expect(happened(undefined)).toEqual([])
    expect(happened([])).toEqual([])
  })

  it('leaves out bookkeeping that ran as usual and switches that were off', () => {
    const lines = happened([e(1, 'latency_title', 'ran'), e(2, 'ready_sound', 'ran'), e(3, 'discord', 'skipped'),
      e(4, 'cleanup_vods', 'ran'), e(5, 'latency_render', 'ran')])
    expect(lines).toEqual([])
  })

  it('newest last, whatever order the records came in', () => {
    const lines = happened([e(30, 'discord', 'ran'), e(10, 'title_moment', 'ran'), e(20, 'hands_free_render', 'ran')])
    expect(lines.map(l => l.text)).toEqual(['Sent to be captioned and titled', 'Queued to render on its own (hands-free)',
      "Sent to the campaign's Discord"])
    expect(lines.every(l => l.tone === '' && /\d/.test(l.at))).toBe(true)
  })

  it('shows what was held back and what failed, with why', () => {
    const lines = happened([
      e(1, 'hands_free_render', 'held', 'Held for review: Hate slur (xx)'),
      e(2, 'discord', 'held', 'Kai clipping wants clips of at most 40 s; this one is 60 s. Trim it, or it may not count.'),
      e(3, 'cleanup_vods', 'failed', 'OSError: disk full'),
      e(4, 'discord', 'skipped', 'shutting down'),
      e(5, 'smarter_title', 'ran'),
    ])
    expect(lines.map(l => [l.text, l.tone])).toEqual([
      ['Not rendered on its own: Held for review: Hate slur (xx)', 'warn'],
      ['Not sent to Discord on its own: Kai clipping wants clips of at most 40 s; this one is 60 s. Trim it, or it may not count', 'warn'],
      ['Storage cleanup failed: OSError: disk full', 'err'],
      ['Discord skipped: shutting down', ''],
      ['Smarter title', ''],
    ])
  })
})
