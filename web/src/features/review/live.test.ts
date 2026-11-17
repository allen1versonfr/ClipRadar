import { describe, expect, it } from 'vitest'
import { LiveView } from '../../api/schemas'
import { liveStatus, vodNote } from './live'

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
  it('says the full stream is being saved, and where it went', () => {
    expect(vodNote(view({ status: 'recording', vod: { state: 'keeping' } }))!.text).toContain('Saving the full stream')
    expect(vodNote(view({ status: 'ended', vod: { state: 'keeping' } }))).toBeNull()
    expect(vodNote(view({ status: 'ended', vod: { state: 'saving' } }))!.text).toBe('Joining the full stream into one video.')
    expect(vodNote(view({ status: 'ended', vod: { state: 'saved', path: 'D:/Clips/x_stream/vod.mp4', title: 'x stream' } })))
      .toEqual({ text: 'Full stream saved to D:/Clips/x_stream/vod.mp4', title: 'x stream', href: '#vod' })
  })
  it('warns when the disk filled up, saving failed or it was never on', () => {
    expect(vodNote(view({ status: 'recording', vod: { state: 'stopped', upto: 3725 } })))
      .toMatchObject({ tone: 'warn', text: 'Disk nearly full: the full stream stopped saving at 1:02:05. Moments are still clipped.' })
    expect(vodNote(view({ status: 'ended', vod: { state: 'failed', note: "Couldn't save the full stream" } }))).toMatchObject({ tone: 'err' })
    expect(vodNote(view({ status: 'recording', vod: { state: 'off', note: 'Not saving the full stream: low disk.' } }))).toMatchObject({ tone: 'warn' })
    expect(vodNote(view({ status: 'recording' }))).toBeNull()
  })
})
