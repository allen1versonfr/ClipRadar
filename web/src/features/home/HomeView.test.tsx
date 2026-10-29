import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ToastProvider } from '../../components/Toasts'
import * as handoff from '../../handoff'
import { HomeView } from './HomeView'
import { sourceDetail } from './RunningNow'

const STATUS = { version: '4.15.0', api: 1, ui: 'legacy', ffmpeg: true, ytdlp: true, whisper: true, laughter: true,
  twitchdownloader: true, can_download_tools: true, live: '' }

type Call = [string, Record<string, unknown> | undefined]
let calls: Call[]
let jobAnswers: Array<Record<string, unknown>>
let sources: unknown[]

function fakeServer() {
  return vi.fn(async (path: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : undefined
    calls.push([path, body])
    const answer = (() => {
      if (path === '/api/analyze') return { job: 'j1' }
      if (path === '/api/job/j1') return jobAnswers.length > 1 ? jobAnswers.shift() : jobAnswers[0]
      if (path === '/api/sources') return { items: sources, limits: { downloads: 2, lives: 2 } }
      if (path === '/api/recent') return [{ title: 'Big stream', source: 'https://www.twitch.tv/videos/42', updated: 1, duration: 3600, review: { kept: 2, skipped: 1, todo: 5 } }]
      if (path === '/api/twitch') return { signed_in: false, login: '' }
      return { ok: true }
    })()
    return new Response(JSON.stringify(answer), { status: 200 })
  })
}

beforeEach(() => {
  calls = []
  sources = []
  jobAnswers = [{ id: 'j1', kind: 'analyze', status: 'running', step: 'Downloading', progress: 0.4, eta: 30, log: ['Downloading'] },
    { id: 'j1', kind: 'analyze', status: 'done', step: 'Done', progress: 1, log: [], result: { pid: 'p42', title: 'Big stream' } }]
  localStorage.clear()
  vi.stubGlobal('fetch', fakeServer())
  vi.spyOn(handoff, 'go').mockImplementation(() => {})
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

const renderHome = (status: object = STATUS) =>
  render(<ToastProvider><HomeView status={status as never} refreshStatus={async () => {}} /></ToastProvider>)

describe('Home', () => {
  it("sends Find clips exactly as the original page does, with your saved choices", async () => {
    localStorage.setItem('vc_count2', JSON.stringify('20'))
    localStorage.setItem('vc_preset', JSON.stringify('gaming'))
    localStorage.setItem('vc_cw', JSON.stringify('55'))
    localStorage.setItem('vc_maxh', JSON.stringify('720'))
    localStorage.setItem('vc_opts', JSON.stringify({ language: 'en' }))
    renderHome()
    fireEvent.change(screen.getByTestId('source-input'), { target: { value: ' "https://www.twitch.tv/videos/1" ' } })
    fireEvent.click(screen.getByTestId('find-clips'))
    await waitFor(() => expect(calls.some(c => c[0] === '/api/analyze')).toBe(true))
    const body = calls.find(c => c[0] === '/api/analyze')![1]
    expect(body).toEqual({
      source: 'https://www.twitch.tv/videos/1', chat_file: '', count: 20, length: 0, chat_weight: 55,
      detect: { preset: 'gaming', sensitivity: 'normal', chat_delay: 'auto' }, emotes: true, max_height: 720, cookies: '',
      events: true, deep_scan: false, deep_model: 'auto', language: 'en',
    })
    expect(localStorage.getItem('vc_src')).toBe('"https://www.twitch.tv/videos/1"')
  })

  it('shows progress, then opens the finished video in Review', async () => {
    renderHome()
    fireEvent.change(screen.getByTestId('source-input'), { target: { value: 'D:\\Videos\\stream.mp4' } })
    fireEvent.click(screen.getByTestId('find-clips'))
    await waitFor(() => expect(screen.getByTestId('vod-job').textContent).toMatch(/Downloading.*40%/))
    await waitFor(() => expect(handoff.go).toHaveBeenCalledWith('#review/p42'), { timeout: 3000 })
  })

  it("says why when the video can't be opened", async () => {
    jobAnswers = [{ id: 'j1', kind: 'analyze', status: 'error', step: '', progress: 0, log: [], error: "That video is private." }]
    renderHome()
    fireEvent.change(screen.getByTestId('source-input'), { target: { value: 'https://youtu.be/x' } })
    fireEvent.click(screen.getByTestId('find-clips'))
    expect((await screen.findByTestId('vod-error')).textContent).toBe('That video is private.')
    expect(handoff.go).not.toHaveBeenCalled()
  })

  it('a Recent video opens again with one click', async () => {
    renderHome()
    fireEvent.click(await screen.findByTestId('recent-row'))
    await waitFor(() => expect(calls.find(c => c[0] === '/api/analyze')?.[1]?.source).toBe('https://www.twitch.tv/videos/42'))
  })

  it('a stream type moves Rank by, as in the original page', () => {
    renderHome()
    const vod = screen.getByRole('region', { name: 'Past broadcast or video' })
    fireEvent.change(within(vod).getByDisplayValue('Any stream'), { target: { value: 'podcasts' } })
    expect(localStorage.getItem('vc_cw')).toBe('45')
  })

  it('Running now lists sources and acts on them', async () => {
    sources = [
      { type: 'vod', id: 'j9', status: 'running', title: 'Opening', source: 'x', label: 'Twitch VOD', step: 'Downloading', progress: 0.5, eta: 90 },
      { type: 'vod', id: 'j8', status: 'done', title: 'Ready one', source: 'y', label: 'File', pid: 'p8' },
      { type: 'live', id: 's1', status: 'recording', title: 'Live one', source: 'z', label: 'Twitch livestream', recorded: 75, moments: 1, chat_state: 'ok' },
    ]
    renderHome()
    const rows = await screen.findAllByTestId('source-row')
    expect(rows).toHaveLength(3)
    fireEvent.click(within(rows[0]!).getByRole('button', { name: 'Pause' }))
    await waitFor(() => expect(calls).toContainEqual(['/api/sources/pause', { job: 'j9' }]))
    fireEvent.click(within(rows[1]!).getByRole('button', { name: 'Open' }))
    expect(handoff.go).toHaveBeenCalledWith('#review/p8')
    fireEvent.click(within(rows[2]!).getByRole('button', { name: 'Stop' }))
    await waitFor(() => expect(calls).toContainEqual(['/api/live/s1/stop', {}]))
  })

  it('describes each source as the original page does', () => {
    const base = { title: '', source: '', label: '', step: '', progress: 0, pid: '', error: '', recorded: 0, moments: 0, chat_state: '' }
    expect(sourceDetail({ ...base, type: 'vod', id: 'a', status: 'running', step: 'Reading chat', progress: 0.25, eta: 40 }))
      .toBe('Reading chat · 25% · about 40 s left')
    expect(sourceDetail({ ...base, type: 'live', id: 'b', status: 'recording', recorded: 75, moments: 2, chat_state: 'quiet' }))
      .toBe('Recording 1:15 · 2 moments · no chat')
    expect(sourceDetail({ ...base, type: 'vod', id: 'c', status: 'error', error: 'Private video' })).toBe('Private video')
  })

  it('offers to download missing tools', async () => {
    renderHome({ ...STATUS, ffmpeg: false })
    const banner = await screen.findByTestId('tools-banner')
    expect(banner.textContent).toMatch(/FFmpeg is missing/)
    fireEvent.click(within(banner).getByRole('button', { name: 'Download' }))
    await waitFor(() => expect(calls).toContainEqual(['/api/setup_tools', { want: ['ffmpeg'] }]))
  })
})
