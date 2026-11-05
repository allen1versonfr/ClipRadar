import { describe, expect, it } from 'vitest'
import { QueueItem } from '../../api/schemas'
import { discordStatus, handoffCell, handoffStatus, submission } from './postStatus'

const item = (extra: Record<string, unknown>) => QueueItem.parse({ id: 'q1', status: 'done', title: 'Big play', name: 'Big play.mp4', ...extra })

describe('posting', () => {
  it('sends you to your own browser when the TikTok window never opened', () => {
    const s = handoffStatus(item({ handoff: { at: 1, auto: 'failed', step: 'browser', error: "It wouldn't open." } }), undefined)!
    expect(s.more).toContain('finish in TikTok Studio, opening in your browser now')
    expect(handoffStatus(item({ handoff: { at: 1, auto: 'failed', step: 'caption' } }), undefined)!.more).toContain('finish there')
  })

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
  it('an upload stopped part way, or lost after Post was pressed, says so (4.45)', () => {
    const stopped = handoffStatus(item({ handoff: { at: 1, auto: 'failed', step: 'stopped', error: 'x' } }), undefined)!
    expect(stopped.text).toBe('The upload was stopped before it finished.')
    const post = handoffStatus(item({ handoff: { at: 1, auto: 'failed', step: 'post', error: 'It may have been posted.' } }), undefined)!
    expect(post.text).toBe('The upload stopped after Clip Radar pressed Post.')
    expect(post.more).toBe('It may have been posted.')
    expect(post.more).not.toContain('drag it in')
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
