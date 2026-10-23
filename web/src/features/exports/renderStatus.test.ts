import { describe, expect, it } from 'vitest'
import { QueueItem } from '../../api/schemas'
import { attemptLine, fmtLeft, fmtTime, isMeasured, stageOf, statusText, summary } from './renderStatus'

const item = (over: object) => QueueItem.parse({ id: 'a1', status: 'queued', ...over })

describe('times', () => {
  it('formats like the original page', () => {
    expect(fmtTime(75)).toBe('1:15')
    expect(fmtTime(3725)).toBe('1:02:05')
    expect(fmtTime(-3)).toBe('0:00')
  })
  it('says time left the same way as the original page', () => {
    expect(fmtLeft(null)).toBe('working out time left')
    expect(fmtLeft(3)).toBe('almost done')
    expect(fmtLeft(6)).toBe('about 5 s left')
    expect(fmtLeft(90)).toBe('about 2 min left')
  })
})

describe('render status', () => {
  it('shows a percentage only while FFmpeg encodes', () => {
    const r = item({ status: 'rendering', stage: 'rendering', step: 'Rendering', progress: 0.42, eta: 20 })
    expect(isMeasured(r)).toBe(true)
    expect(statusText(r, 0)).toEqual({ label: 'Rendering 42% · about 20 s left', fraction: 0.42 })
  })
  it('shows the step and how long it has taken, never a made-up percentage, in other stages', () => {
    for (const stage of ['validating', 'preparing', 'verifying'] as const) {
      const r = item({ status: 'rendering', stage, step: 'Getting captions ready', progress: 0.3, stage_at: 100 })
      expect(isMeasured(r)).toBe(false)
      expect(statusText(r, 172)).toEqual({ label: 'Getting captions ready · 1:12', fraction: null })
    }
  })
  it('treats items saved before stages existed by their status', () => {
    expect(stageOf(item({ status: 'rendering' }))).toBe('rendering')
    expect(stageOf(item({ status: 'done' }))).toBe('done')
    expect(stageOf(item({ status: 'error' }))).toBe('error')
  })
  it('labels finished and failed clips', () => {
    expect(statusText(item({ status: 'done', srt: true }), 0).label).toBe('Ready + .srt')
    expect(statusText(item({ status: 'error' }), 0).label).toBe('Failed')
    expect(statusText(item({ step: 'Waiting to try again' }), 0).label).toBe('Waiting to try again')
  })
  it('writes one line per attempt', () => {
    expect(attemptLine({ n: 1, started: 0, outcome: 'error', kind: 'layout', stage: 'rendering', encoder: 'libx264', exit: 234 }))
      .toBe('Attempt 1: error (layout), during rendering, libx264, exit 234')
    expect(attemptLine({ n: 2, started: 0, outcome: 'done', stage: 'done', encoder: 'h264_amf' }))
      .toBe('Attempt 2: done, h264_amf')
  })
  it('sums up the list', () => {
    const items = [item({ status: 'done' }), item({ status: 'done' }), item({ status: 'rendering' }), item({ status: 'error' })]
    expect(summary(items, 90)).toBe('2 ready, 1 rendering (about 2 min left), 1 failed')
    expect(summary([], null)).toBe('')
  })
})
