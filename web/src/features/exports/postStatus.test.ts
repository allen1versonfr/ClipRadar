import { describe, expect, it } from 'vitest'
import { QueueItem } from '../../api/schemas'
import { discordStatus, handoffCell, handoffStatus, submission } from './postStatus'

const item = (extra: Record<string, unknown>) => QueueItem.parse({ id: 'q1', status: 'done', title: 'Big play', name: 'Big play.mp4', ...extra })

describe('posting', () => {
  it("only says Posted when TikTok confirmed it", () => {
    expect(handoffCell(item({})).text).toBe('Not yet')
    expect(handoffCell(item({ handoff: { at: 1 } })).text).toBe('Opened in Studio')
    expect(handoffCell(item({ handoff: { at: 1, auto: 'ready' } })).text).toBe('In Studio, press Post')
    expect(handoffCell(item({ handoff: { at: 1, auto: 'pressed' } })).text).toBe('Post pressed')
    expect(handoffCell(item({ handoff: { at: 1, auto: 'posted' } }))).toMatchObject({ text: 'Posted', tone: 'ok' })
    expect(handoffCell(item({ handoff: { at: 1, auto: 'failed', error: 'No file box' } }))).toMatchObject({ text: 'Upload stopped', tone: 'err', title: 'No file box' })
  })
  it('says where an upload stopped, and how to finish it', () => {
    const s = handoffStatus(item({ handoff: { at: 1, auto: 'failed', step: 'sign-in', error: 'Timed out.' } }), undefined)!
    expect(s.text).toBe('The upload stopped while waiting for you to sign in.')
    expect(s.more).toContain('selected in File Explorer')
    expect(handoffStatus(item({}), 'typing the caption')!.text).toBe('typing the caption')
    expect(handoffStatus(item({}), undefined)).toBeNull()
  })
  it("the campaign's Discord: sent with what, or why not", () => {
    expect(discordStatus(item({ discord: { at: 1, attached: true } }))!.text).toMatch(/^Sent to Discord .+ with the video\.$/)
    expect(discordStatus(item({ discord: { at: 1, error: 'The webhook was refused.' } }))).toMatchObject({ tone: 'err', text: 'The webhook was refused.' })
  })
  it('a submission is the title, where it was posted (or the file), and the caption', () => {
    expect(submission(item({ post: 'so good #fyp' }), ' https://tiktok.com/@me/video/1 ')).toBe('Big play\nhttps://tiktok.com/@me/video/1\nso good #fyp')
    expect(submission(item({}), '')).toBe('Big play\nBig play.mp4')
  })
})
