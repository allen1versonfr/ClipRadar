import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ToastProvider } from '../../components/Toasts'
import { VodExportView } from './VodExportView'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  localStorage.clear()
})

const VIDEO = {
  pid: 'p1', title: 'Friday stream', duration: 7200, exists: true, has_audio: true, copyable: true,
  scan: { spans: [{ start: 60, end: 240, confidence: 0.9, speech: 0.7, suggest: 'voice' }, { start: 3600, end: 3660, confidence: 0.6, speech: 0, suggest: 'mute' }], at: 1, how: 'heard now' },
  flags: { at: 5, stale: false, flags: [
    { id: 'a', start: 100, end: 100.4, text: 'f*****', label: 'Swearing', cat: 'profanity', context: 'oh f***** no', on: true },
    { id: 'b', start: 900, end: 900.5, text: 's***', label: 'Swearing', cat: 'profanity', context: 'that s*** was', on: false },
  ] },
  scan_job: null, words_job: null, hear_job: null, preview_job: null, export_job: null, exported: false,
}
const VIEW = {
  music_model: true, voice_model: true, whisper: true, browser: true, censor: 'mute', censor_categories: ['Swearing'],
  videos: [VIDEO], library: [{ pid: 'old1', title: 'Last week', duration: 3600, where: 'downloaded' }],
}

function server(calls: Array<[string, unknown]>, view: object = VIEW) {
  return vi.fn(async (path: string, init?: RequestInit) => {
    calls.push([path, init?.body ? JSON.parse(String(init.body)) : undefined])
    const body = path === '/api/vodexport' ? view : path.startsWith('/api/vodexport/words') ? { words: [], covered: [] }
      : path === '/api/vodexport/open' ? { pid: 'old1' } : path === '/api/pick_file' ? { path: 'D:\\streams\\big.mkv' } : { job: 'j1' }
    return new Response(JSON.stringify(body), { status: 200 })
  })
}
const sent = (calls: Array<[string, unknown]>, p: string) => calls.find(([q]) => q === p)?.[1] as Record<string, unknown> | undefined

describe('Export VOD', () => {
  it('with nothing open, offers the videos on this PC and a file picker', async () => {
    const calls: Array<[string, unknown]> = []
    vi.stubGlobal('fetch', server(calls, { ...VIEW, videos: [] }))
    render(<ToastProvider><VodExportView /></ToastProvider>)
    expect((await screen.findByTestId('vodx-empty')).textContent).toMatch(/any video on this PC/)
    fireEvent.click(within(screen.getByTestId('vodx-library')).getByRole('button', { name: /Last week/ }))
    await waitFor(() => expect(sent(calls, '/api/vodexport/open')).toEqual({ pid: 'old1' }))
    fireEvent.click(screen.getByTestId('vodx-browse'))
    await waitFor(() => expect(calls.filter(([p]) => p === '/api/vodexport/open').at(-1)?.[1]).toEqual({ path: 'D:\\streams\\big.mkv' }))
  })

  it('each music stretch starts as suggested, can be changed, and is sent with what to do', async () => {
    const calls: Array<[string, unknown]> = []
    vi.stubGlobal('fetch', server(calls))
    render(<ToastProvider><VodExportView /></ToastProvider>)
    expect((await screen.findByTestId('vodx-music-summary')).textContent).toBe('2 music stretches, 4:00 in all. 1 erased, 1 muted.')
    fireEvent.click(within(screen.getByRole('radiogroup', { name: 'Music at 1:00:00' })).getByRole('radio', { name: 'Keep' }))
    expect(screen.getByTestId('vodx-music-summary').textContent).toBe('2 music stretches, 4:00 in all. 1 erased, 1 kept.')
    fireEvent.click(screen.getByTestId('vodx-export'))
    await waitFor(() => expect(sent(calls, '/api/vodexport/start')).toBeDefined())
    expect(sent(calls, '/api/vodexport/start')).toMatchObject({ pid: 'p1', music: [[60, 240, 'voice']] })
  })

  it('without the music remover, music can only be muted', async () => {
    vi.stubGlobal('fetch', server([], { ...VIEW, voice_model: false }))
    render(<ToastProvider><VodExportView /></ToastProvider>)
    expect((await screen.findByTestId('vodx-music-summary')).textContent).toMatch(/All muted/)
    expect(screen.getByTestId('vodx-no-remover')).toBeTruthy()
    expect(screen.queryByRole('radio', { name: 'Erase song' })).toBeNull()
  })

  it('the flagged words queue: ticked ones are muted, untick or tick any', async () => {
    const calls: Array<[string, unknown]> = []
    vi.stubGlobal('fetch', server(calls))
    render(<ToastProvider><VodExportView /></ToastProvider>)
    expect((await screen.findByTestId('vodx-flag-summary')).textContent).toBe('2 flagged words, 1 muted.')
    const boxes = screen.getAllByTestId('vodx-flag') as HTMLInputElement[]
    expect(boxes.map(b => b.checked)).toEqual([true, false])
    fireEvent.click(boxes[1]!)
    expect(screen.getByTestId('vodx-flag-summary').textContent).toBe('2 flagged words, 2 muted.')
    fireEvent.click(screen.getByTestId('vodx-export'))
    await waitFor(() => expect(sent(calls, '/api/vodexport/start')).toBeDefined())
    const ws = sent(calls, '/api/vodexport/start')!.word_spans as number[][]
    expect(ws.map(x => Math.round(x[0]!))).toEqual([100, 900])
  })

  it('checks for flagged words', async () => {
    const calls: Array<[string, unknown]> = []
    vi.stubGlobal('fetch', server(calls, { ...VIEW, videos: [{ ...VIDEO, flags: null }] }))
    render(<ToastProvider><VodExportView /></ToastProvider>)
    fireEvent.click(await screen.findByTestId('vodx-check-words'))
    await waitFor(() => expect(sent(calls, '/api/vodexport/words')).toMatchObject({ pid: 'p1' }))
  })

  it('captions: the look is sent with the export, and with captions off nothing about listening is shown', async () => {
    const calls: Array<[string, unknown]> = []
    vi.stubGlobal('fetch', server(calls))
    render(<ToastProvider><VodExportView /></ToastProvider>)
    const caps = await screen.findByTestId('vodx-captions')
    fireEvent.click(within(caps).getByRole('radio', { name: 'Boxed' }))
    fireEvent.click(within(caps).getByRole('radio', { name: 'Top' }))
    fireEvent.click(screen.getByTestId('vodx-export'))
    await waitFor(() => expect(sent(calls, '/api/vodexport/start')).toBeDefined())
    expect(sent(calls, '/api/vodexport/start')).toMatchObject({ captions: 'burn', style: { caption_style: 'box', caption_pos: 'top' } })
    fireEvent.click(within(caps).getByRole('radio', { name: 'Off' }))
    expect(screen.queryByTestId('vodx-style')).toBeNull()
    expect(within(caps).queryByText(/listened to first/)).toBeNull()
    expect(screen.getByTestId('vodx-speed').textContent).toMatch(/copied as it is/)
  })

  it('the exact preview starts from where the player is', async () => {
    const calls: Array<[string, unknown]> = []
    vi.stubGlobal('fetch', server(calls))
    render(<ToastProvider><VodExportView /></ToastProvider>)
    fireEvent.click(await screen.findByRole('button', { name: '1:00 to 4:00 (3:00)' }))
    fireEvent.click(screen.getByTestId('vodx-exact-btn'))
    await waitFor(() => expect(sent(calls, '/api/vodexport/preview')).toMatchObject({ pid: 'p1', at: 60, music: [[60, 240, 'voice'], [3600, 3660, 'mute']] }))
  })

  it('without the speech model, exports without captions instead of failing', async () => {
    const calls: Array<[string, unknown]> = []
    vi.stubGlobal('fetch', server(calls, { ...VIEW, whisper: false, videos: [{ ...VIDEO, flags: null }] }))
    render(<ToastProvider><VodExportView /></ToastProvider>)
    expect((await screen.findAllByText(/Needs the speech model/)).length).toBe(2)
    expect(screen.queryByRole('radiogroup', { name: 'Captions' })).toBeNull()
    fireEvent.click(screen.getByTestId('vodx-export'))
    await waitFor(() => expect(sent(calls, '/api/vodexport/start')).toMatchObject({ captions: 'off', word_spans: [] }))
  })

  it('a flagged words check already made can be used without the speech model', async () => {
    const calls: Array<[string, unknown]> = []
    vi.stubGlobal('fetch', server(calls, { ...VIEW, whisper: false }))
    render(<ToastProvider><VodExportView /></ToastProvider>)
    expect((await screen.findByTestId('vodx-flag-summary')).textContent).toBe('2 flagged words, 1 muted.')
    expect(screen.getByTestId('vodx-flags-no-whisper')).toBeTruthy()
    expect(screen.queryByTestId('vodx-check-words')).toBeNull()
    fireEvent.click(screen.getByTestId('vodx-export'))
    await waitFor(() => expect(sent(calls, '/api/vodexport/start')).toBeDefined())
    expect((sent(calls, '/api/vodexport/start')!.word_spans as number[][]).length).toBe(1)
  })

  it('after an export, uploads to YouTube Studio or opens it', async () => {
    const calls: Array<[string, unknown]> = []
    vi.stubGlobal('fetch', server(calls, { ...VIEW, videos: [{ ...VIDEO, exported: true }] }))
    render(<ToastProvider><VodExportView /></ToastProvider>)
    fireEvent.click(await screen.findByTestId('vodx-yt-upload'))
    await waitFor(() => expect(sent(calls, '/api/vodexport/youtube')).toMatchObject({ pid: 'p1', title: 'Friday stream', auto: true }))
    fireEvent.click(screen.getByTestId('vodx-yt-open'))
    await waitFor(() => expect(calls.filter(([p]) => p === '/api/vodexport/youtube').at(-1)?.[1]).toMatchObject({ auto: false }))
  })

  it('a new scan starts the choices over', async () => {
    let view: object = VIEW
    vi.stubGlobal('fetch', vi.fn(async (p: string) => new Response(JSON.stringify(p === '/api/vodexport' ? view : { words: [], covered: [] }), { status: 200 })))
    const { rerender } = render(<ToastProvider><VodExportView /></ToastProvider>)
    await screen.findByTestId('vodx-music-summary')
    fireEvent.click(within(screen.getByRole('radiogroup', { name: 'Music at 1:00' })).getByRole('radio', { name: 'Keep' }))
    expect(screen.getByTestId('vodx-music-summary').textContent).toMatch(/1 muted, 1 kept/)
    view = { ...VIEW, videos: [{ ...VIDEO, scan: { ...VIDEO.scan, at: 2 } }] }
    fireEvent(document, new Event('visibilitychange'))
    rerender(<ToastProvider><VodExportView /></ToastProvider>)
    await waitFor(() => expect(screen.getByTestId('vodx-music-summary').textContent).toMatch(/1 erased, 1 muted/), { timeout: 4000 })
  })

  it('music the scan missed can be marked, is erased in the export, and stays through a new scan', async () => {
    const calls: Array<[string, unknown]> = []
    let view: object = VIEW
    vi.stubGlobal('fetch', vi.fn(async (p: string, init?: RequestInit) => {
      calls.push([p, init?.body ? JSON.parse(String(init.body)) : undefined])
      return new Response(JSON.stringify(p === '/api/vodexport' ? view : p.startsWith('/api/vodexport/words') ? { words: [], covered: [] } : { job: 'j1' }), { status: 200 })
    }))
    const { rerender } = render(<ToastProvider><VodExportView /></ToastProvider>)
    fireEvent.click(await screen.findByTestId('vodx-add-song'))
    expect(screen.getByTestId('vodx-music-summary').textContent).toBe('3 music stretches, 4:30 in all. 2 erased, 1 muted.')
    expect(screen.getAllByTestId('vodx-span')[0]!.textContent).toMatch(/Marked by you/)
    fireEvent.click(screen.getByTestId('vodx-export'))
    await waitFor(() => expect(sent(calls, '/api/vodexport/start')).toBeDefined())
    expect(sent(calls, '/api/vodexport/start')!.music).toEqual([[0, 30, 'voice'], [60, 240, 'voice'], [3600, 3660, 'mute']])
    // kept on the server with the video, so Segments uses it too
    await waitFor(() => expect(sent(calls, '/api/vodexport/music')).toMatchObject({ pid: 'p1', at: 1 }), { timeout: 2000 })
    const saved = () => calls.filter(([p]) => p === '/api/vodexport/music').at(-1)?.[1] as { songs: Array<{ mine?: boolean }> } | undefined
    await waitFor(() => expect(saved()?.songs.filter(s => s.mine)).toHaveLength(1), { timeout: 2000 })
    view = { ...VIEW, videos: [{ ...VIDEO, scan: { ...VIDEO.scan, at: 2 } }] }
    fireEvent(document, new Event('visibilitychange'))
    rerender(<ToastProvider><VodExportView /></ToastProvider>)
    await waitFor(() => expect(screen.getAllByTestId('vodx-span')).toHaveLength(3), { timeout: 4000 })
    fireEvent.click(within(screen.getAllByTestId('vodx-span')[0]!).getByRole('button', { name: /Remove the music you marked/ }))
    expect(screen.getAllByTestId('vodx-span')).toHaveLength(2)
  })

  it('Hear it makes the exact preview from where that music starts', async () => {
    const calls: Array<[string, unknown]> = []
    vi.stubGlobal('fetch', server(calls))
    render(<ToastProvider><VodExportView /></ToastProvider>)
    fireEvent.click((await screen.findAllByTestId('vodx-span-hear'))[1]!)
    await waitFor(() => expect(sent(calls, '/api/vodexport/preview')).toMatchObject({ at: 3600, music: [[60, 240, 'voice'], [3600, 3660, 'mute']] }))
  })

  it('Find segments: the parts found are listed, ticked as found, renamed and sent to Segments together', async () => {
    const calls: Array<[string, unknown]> = []
    const found = { at: 3, length: 'medium', heard: 'all', parts: [
      { start: 0, end: 870, name: 'friends · sister', on: true, why: 'Talking' },
      { start: 870, end: 1560, name: 'nyc · trip', on: true, why: 'Talking' },
      { start: 1560, end: 1650, name: 'Part 3', on: false, quiet: true, why: 'Mostly quiet (a break?)' }] }
    vi.stubGlobal('fetch', server(calls, { ...VIEW, videos: [{ ...VIDEO, found }] }))
    render(<ToastProvider><VodExportView /></ToastProvider>)
    expect((await screen.findByTestId('vodx-found-summary')).textContent).toBe('3 parts, 2 ticked.')
    fireEvent.change(screen.getByRole('textbox', { name: 'Name of part 2' }), { target: { value: 'emily talks abt her nyc trip' } })
    fireEvent.click(screen.getByRole('checkbox', { name: 'Send part 1 to Segments' }))
    fireEvent.click(screen.getByTestId('vodx-found-send'))
    await waitFor(() => expect(sent(calls, '/api/segments/add_many')).toMatchObject({ pid: 'p1', name: 'Friday stream segments',
      parts: [{ start: 870, end: 1560, name: 'emily talks abt her nyc trip' }] }))
  })

  it('Find segments asks for the length chosen', async () => {
    const calls: Array<[string, unknown]> = []
    vi.stubGlobal('fetch', server(calls))
    render(<ToastProvider><VodExportView /></ToastProvider>)
    fireEvent.click(within(await screen.findByTestId('vodx-find')).getByRole('radio', { name: 'Long (10 to 30 min)' }))
    fireEvent.click(screen.getByTestId('vodx-find-btn'))
    await waitFor(() => expect(sent(calls, '/api/vodexport/segfind')).toMatchObject({ pid: 'p1', length: 'long' }))
  })
})
