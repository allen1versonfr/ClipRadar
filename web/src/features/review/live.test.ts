import { describe, expect, it } from 'vitest'
import { LiveView } from '../../api/schemas'
import { liveStatus } from './live'

const view = (extra: Record<string, unknown>) => LiveView.parse({ id: 's1', ...extra })

describe('live session header', () => {
  it('says how chat is doing', () => {
    expect(liveStatus(view({ chat_state: 'offline' }))[0]).toMatchObject({ tone: 'warn', text: expect.stringContaining('Chat not connected') })
    expect(liveStatus(view({ chat_state: 'ok', chat_delay: 4.4, delay_auto: true, delay_sure: true }))[0]!.text).toBe('Chat delay 4 s (worked out)')
  })
  it('shows the last two moments forming, how soon clips are ready, and what was passed on', () => {
    const bits = liveStatus(view({ forming: [{ at: 10, quality: 40 }, { at: 70, quality: 55 }, { at: 130, quality: 61 }],
      latency: { ingest: 2, detect: 1, total: 31.6 }, skipped: { confidence: 2, repeat: 1 } })).map(b => b.text)
    expect(bits).toEqual(['Moment forming at 1:10 · score 55', 'Moment forming at 2:10 · score 61', 'Ready about 32 s after it happens', 'Passed on 3'])
  })
})
