import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ToastProvider } from '../../components/Toasts'
import { VodExportView } from './VodExportView'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  localStorage.clear()
})

const VIEW = {
  music_model: true, whisper: true, censor: 'mute', censor_categories: ['Hate slur', 'Swearing'],
  videos: [{ pid: 'p1', title: 'Friday stream', duration: 7200, exists: true, has_audio: true,
    scan: { spans: [{ start: 60, end: 240, confidence: 0.9 }, { start: 3600, end: 3660, confidence: 0.6 }], at: 1, how: 'heard now' },
    scan_job: null, export_job: null }],
}

function server(calls: Array<[string, unknown]>, view: object = VIEW) {
  return vi.fn(async (path: string, init?: RequestInit) => {
    calls.push([path, init?.body ? JSON.parse(String(init.body)) : undefined])
    const body = path === '/api/vodexport' ? view : { job: 'j1' }
    return new Response(JSON.stringify(body), { status: 200 })
  })
}

describe('Export VOD', () => {
  it('says to open a video when none is open', async () => {
    vi.stubGlobal('fetch', server([], { videos: [] }))
    render(<ToastProvider><VodExportView /></ToastProvider>)
    expect((await screen.findByTestId('vodx-empty')).textContent).toMatch(/Open one on Home/)
  })

  it('lists the music found, mutes it all unless kept, and exports with muting and captions', async () => {
    const calls: Array<[string, unknown]> = []
    vi.stubGlobal('fetch', server(calls))
    render(<ToastProvider><VodExportView /></ToastProvider>)
    expect((await screen.findByTestId('vodx-music-summary')).textContent).toBe('2 music stretches, 4:00 in all. All muted.')
    const boxes = screen.getAllByTestId('vodx-span') as HTMLInputElement[]
    expect(boxes.map(b => b.checked)).toEqual([true, true])
    fireEvent.click(boxes[1]!)                                          // keep the second one
    expect(screen.getByTestId('vodx-music-summary').textContent).toBe('2 music stretches, 4:00 in all. 1 muted, 1 kept.')
    fireEvent.click(screen.getByRole('radio', { name: 'Both' }))
    expect(screen.getByTestId('vodx-export').textContent).toBe('Export 2:00:00 · 1 music stretch muted')
    fireEvent.click(screen.getByTestId('vodx-export'))
    await waitFor(() => expect(calls.some(([p]) => p === '/api/vodexport/start')).toBe(true))
    const body = calls.find(([p]) => p === '/api/vodexport/start')![1] as Record<string, unknown>
    expect(body).toMatchObject({ pid: 'p1', captions: 'both', words: 'mute', music: [[60, 240]], size: '1080', quality: 'balanced' })
  })

  it('starts a copyright scan', async () => {
    const calls: Array<[string, unknown]> = []
    vi.stubGlobal('fetch', server(calls, { ...VIEW, videos: [{ ...VIEW.videos[0], scan: null }] }))
    render(<ToastProvider><VodExportView /></ToastProvider>)
    fireEvent.click(await screen.findByRole('button', { name: 'Scan for music' }))
    await waitFor(() => expect(calls).toContainEqual(['/api/vodexport/scan', { pid: 'p1' }]))
  })

  it("without the speech model, exports without captions or word muting instead of failing", async () => {
    const calls: Array<[string, unknown]> = []
    vi.stubGlobal('fetch', server(calls, { ...VIEW, whisper: false }))
    render(<ToastProvider><VodExportView /></ToastProvider>)
    expect((await screen.findAllByText(/Needs the speech model/)).length).toBe(2)
    expect(screen.queryByRole('radiogroup', { name: 'Captions' })).toBeNull()
    fireEvent.click(screen.getByTestId('vodx-export'))
    await waitFor(() => expect(calls.some(([p]) => p === '/api/vodexport/start')).toBe(true))
    expect(calls.find(([p]) => p === '/api/vodexport/start')![1]).toMatchObject({ captions: 'off', words: 'off' })
  })
})
