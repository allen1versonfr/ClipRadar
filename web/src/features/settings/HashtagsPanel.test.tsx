import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ToastProvider } from '../../components/Toasts'
import { HashtagsPanel, streamersParse, streamersText } from './HashtagsPanel'

const VIEW = {
  config: {
    file: { enabled: true, streamer_tag: true, general: ['#TwitchClips'], streamers: { 'Kai Cenat': ['#KaiCenat', '#Mafia'] }, max: 4 },
    post: { auto: true, global: [], streamers: {}, platforms: { tiktok: ['#fyp'], shorts: [], reels: [], x: [] }, max: 8 },
  },
  suggestions: ['#TwitchClips', '#Gaming', '#ViralClips'],
  preview: { file: 'Chat lost it #KaiCenat #TwitchClips.mp4', post: { tiktok: ['#twitch', '#fyp'], shorts: [], reels: [], x: [] }, post_on: true },
}
type Call = [string, Record<string, unknown> | undefined]
let calls: Call[]

beforeEach(() => {
  calls = []
  vi.stubGlobal('fetch', vi.fn(async (path: string, init?: RequestInit) => {
    calls.push([path, init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : undefined])
    return new Response(JSON.stringify(VIEW), { status: 200 })
  }))
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const renderPanel = () => render(<ToastProvider><HashtagsPanel /></ToastProvider>)
const saves = () => calls.filter(([, b]) => b !== undefined).map(([, b]) => b as Record<string, any>)

describe('Hashtags', () => {
  it('shows the settings, the suggestions not already used, and the preview', async () => {
    renderPanel()
    expect(((await screen.findByPlaceholderText('#TwitchClips #Gaming')) as HTMLInputElement).value).toBe('#TwitchClips')
    expect((screen.getByPlaceholderText('Kai Cenat: #KaiCenat #Mafia') as HTMLTextAreaElement).value).toBe('Kai Cenat: #KaiCenat #Mafia')
    expect(screen.queryByRole('button', { name: '+ #TwitchClips' })).toBeNull()          // already in General tags
    expect(screen.getByRole('button', { name: '+ #Gaming' })).toBeTruthy()
    expect(screen.getByTestId('tag-file').textContent).toBe('Chat lost it #KaiCenat #TwitchClips.mp4')
    expect(screen.getByTestId('tag-preview').textContent).toContain('TikTok: #twitch #fyp')
  })

  it('saves typing once, after a short pause, in the shape the current UI sends', async () => {
    renderPanel()
    const box = await screen.findByPlaceholderText('#TwitchClips #Gaming')
    vi.useFakeTimers()
    fireEvent.change(box, { target: { value: '#TwitchClips #G' } })
    fireEvent.change(box, { target: { value: '#TwitchClips #Gaming' } })
    expect(saves()).toEqual([])
    await act(async () => { await vi.advanceTimersByTimeAsync(600) })
    vi.useRealTimers()
    await waitFor(() => expect(saves()).toHaveLength(1))
    const b = saves()[0]!
    expect(b.file).toEqual({ enabled: true, streamer_tag: true, general: '#TwitchClips #Gaming',
      streamers: { 'Kai Cenat': ['#KaiCenat', '#Mafia'] }, max: 4 })
    expect(b.post.platforms).toEqual({ tiktok: ['#fyp'], shorts: [], reels: [], x: [] })
  })

  it('a suggestion is added at once', async () => {
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: '+ #Gaming' }))
    await waitFor(() => expect(saves()[0]?.file.general).toBe('#TwitchClips #Gaming'))
  })

  it('a preview name asks for the preview, and never cancels a save', async () => {
    renderPanel()
    const box = await screen.findByPlaceholderText('#TwitchClips #Gaming')
    fireEvent.change(box, { target: { value: '#TwitchClips #X' } })
    fireEvent.change(screen.getByPlaceholderText('Kai Cenat'), { target: { value: 'Kai' } })
    await waitFor(() => expect(saves()).toHaveLength(1), { timeout: 2000 })
    await waitFor(() => expect(calls.some(([p]) => p === '/api/hashtags?streamer=Kai')).toBe(true))
  })
})

describe('per-streamer lines', () => {
  it('read and write "name: tags", skipping lines that are not one', () => {
    expect(streamersParse('Kai Cenat: #KaiCenat, #Mafia\nnot a line\n: #x\nxQc:\nxQc: #xqc')).toEqual({ 'Kai Cenat': ['#KaiCenat', '#Mafia'], xQc: ['#xqc'] })
    expect(streamersText({ a: ['#1', '#2'], b: ['#3'] })).toBe('a: #1 #2\nb: #3')
  })
})
