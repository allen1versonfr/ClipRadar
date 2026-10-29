import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ToastProvider } from '../../components/Toasts'
import { ExportsView } from './ExportsView'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const items = [
  { id: 'd1', status: 'done', stage: 'done', title: 'Chat lost it', duration: 31, name: 'Chat lost it.mp4', url: '/export/d1',
    history: [{ n: 1, started: 1, ended: 2, outcome: 'done', encoder: 'libx264' }] },
  { id: 'e1', status: 'error', stage: 'error', title: 'Crop at the edge', duration: 12, error_kind: 'layout', retryable: false,
    error: "The clip's layout couldn't be built from this video.", detail: '[Parsed_crop_0] Failed to configure input pad',
    history: [{ n: 1, started: 1, ended: 2, outcome: 'error', kind: 'layout', stage: 'rendering', encoder: 'libx264', exit: 234 }] },
  { id: 'r1', status: 'rendering', stage: 'preparing', step: 'Getting captions ready', progress: 0, title: 'Big play',
    duration: 20, stage_at: Date.now() / 1000 - 5 },
]

function server(calls: Array<[string, unknown]>) {
  return vi.fn(async (path: string, init?: RequestInit) => {
    calls.push([path, init?.body ? JSON.parse(String(init.body)) : undefined])
    const body = path === '/api/queue' ? { items, eta: 40, workers: 1 } : { ok: true, retried: 1 }
    return new Response(JSON.stringify(body), { status: 200 })
  })
}

describe('Exports', () => {
  it('lists renders newest first, with honest status for each stage', async () => {
    vi.stubGlobal('fetch', server([]))
    render(<ToastProvider><ExportsView /></ToastProvider>)
    const rows = await screen.findAllByTestId('export-row')
    expect(rows.map(r => r.dataset.id)).toEqual(['r1', 'e1', 'd1'])
    expect(screen.getByTestId('exports-summary').textContent).toBe('1 ready, 1 rendering (about 40 s left), 1 failed')
    const preparing = within(rows[0]!).getByTestId('export-status').textContent ?? ''
    expect(preparing).toMatch(/^Getting captions ready · 0:0\d$/)          // elapsed time, no percentage
    expect(preparing).not.toMatch(/%/)
    expect(within(rows[0]!).getByRole('progressbar').getAttribute('aria-valuenow')).toBeNull()
    expect(within(rows[1]!).getByTestId('export-status').textContent).toBe('Failed')
    expect(within(rows[2]!).getByTestId('export-status').textContent).toBe('Ready')
  })

  it('explains a failure with every attempt, and retries it', async () => {
    const calls: Array<[string, unknown]> = []
    vi.stubGlobal('fetch', server(calls))
    render(<ToastProvider><ExportsView /></ToastProvider>)
    fireEvent.click((await screen.findAllByTestId('export-row'))[1]!)
    const detail = screen.getByTestId('export-detail')
    expect(within(detail).getByTestId('export-error').textContent).toMatch(/layout couldn't be built/)
    expect(detail.textContent).toMatch(/Retrying won't help until that's fixed/)
    fireEvent.click(within(detail).getByRole('button', { name: 'Details' }))
    expect(screen.getByTestId('export-details').textContent).toContain('Attempt 1: error (layout), during rendering, libx264, exit 234')
    fireEvent.click(within(detail).getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(calls).toContainEqual(['/api/queue/retry', { id: 'e1' }]))
    expect(await screen.findByText('Rendering again…')).toBeTruthy()
  })

  it('retries every failed clip at once, and cancels from the list', async () => {
    const calls: Array<[string, unknown]> = []
    vi.stubGlobal('fetch', server(calls))
    render(<ToastProvider><ExportsView /></ToastProvider>)
    fireEvent.click(await screen.findByTestId('retry-all'))
    await waitFor(() => expect(calls).toContainEqual(['/api/queue/retry', { id: 'all' }]))
    const rows = screen.getAllByTestId('export-row')
    fireEvent.click(within(rows[0]!).getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(calls).toContainEqual(['/api/queue/remove', { id: 'r1' }]))
  })

  it("says so when Clip Radar can't be reached", async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch') }))
    render(<ToastProvider><ExportsView /></ToastProvider>)
    expect((await screen.findByRole('alert')).textContent).toMatch(/Lost connection to Clip Radar/)
  })
  it('a rendered campaign clip: open TikTok Studio with your caption, then send your post link to Discord', async () => {
    const calls: Array<[string, any]> = []
    const done = { id: 'c1', status: 'done', stage: 'done', title: 'Clutch', duration: 20, name: 'Clutch.mp4', url: '/export/c1', post: 'old caption',
      link: 'https://twitch.tv/videos/1?t=0h1m0s', campaign: { id: 'k1', name: 'Big Server' }, history: [] }
    vi.stubGlobal('fetch', vi.fn(async (path: string, init?: RequestInit) => {
      const body = init?.body ? JSON.parse(String(init.body)) : undefined
      calls.push([path, body])
      const answer = path === '/api/queue' ? { items: [done], eta: null, workers: 1 }
        : path === '/api/campaigns' ? { list: [{ id: 'k1', name: 'Big Server', discord: { webhook: 'https://discord.com/api/webhooks/x' } }], active: 'k1' }
        : path === '/api/studio' ? { available: false, press_post: false }
        : path === '/api/discord/send' ? { attached: false } : { ok: true }
      return new Response(JSON.stringify(answer), { status: 200 })
    }))
    Object.assign(navigator, { clipboard: { writeText: vi.fn(async () => {}) } })
    render(<ToastProvider><ExportsView /></ToastProvider>)
    expect((await screen.findByTestId('ex-campaign')).textContent).toContain('Campaign: Big Server')
    fireEvent.change(await screen.findByTestId('tt-caption'), { target: { value: 'new caption #fyp' } })
    fireEvent.click(screen.getByTestId('tt-studio'))
    await waitFor(() => expect(calls).toContainEqual(['/api/handoff', { item: 'c1', caption: 'new caption #fyp' }]))
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('new caption #fyp')
    expect(screen.queryByTestId('tt-auto')).toBeNull()                 // no browser to drive: the manual way only
    fireEvent.change(screen.getByTestId('submit-url'), { target: { value: 'https://www.tiktok.com/@me/video/9' } })
    await waitFor(() => expect(calls).toContainEqual(['/api/queue/post_url', { item: 'c1', url: 'https://www.tiktok.com/@me/video/9' }]), { timeout: 2000 })
    fireEvent.click(screen.getByTestId('submit-send'))
    await waitFor(() => expect(calls).toContainEqual(['/api/discord/send', { item: 'c1', post_url: 'https://www.tiktok.com/@me/video/9' }]))
    fireEvent.click(screen.getByRole('button', { name: 'Copy source link' }))
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('https://twitch.tv/videos/1?t=0h1m0s')
  })
})
