import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { App } from './App'
import { ConfirmProvider } from './components/Confirm'
import { ToastProvider } from './components/Toasts'

afterEach(() => { cleanup(); vi.unstubAllGlobals(); location.hash = '' })

function serve(running: boolean, calls: string[]) {
  vi.stubGlobal('fetch', vi.fn(async (path: string) => {
    calls.push(path)
    const answer = path === '/api/status' ? { version: '9.9.9', api: 1, ui: 'web', running, lives: [{ id: 's1', title: 'chan', status: 'recording' }] }
      : path === '/api/queue' ? { items: [{ id: 'q', status: 'rendering', stage: 'rendering', history: [] }], eta: null }
      : path === '/api/update' ? { available: true, latest: '10.0.0', state: 'idle' }
      : path === '/api/recent' || path === '/api/sources' ? [] : { ok: true }
    return new Response(JSON.stringify(path === '/api/sources' ? { items: [] } : answer), { status: 200 })
  }))
}
const app = () => render(<ToastProvider><ConfirmProvider><App /></ConfirmProvider></ToastProvider>)

describe('the app frame', () => {
  it('closing the window while something runs asks first (window.askQuit), and Quit stops Clip Radar', async () => {
    const calls: string[] = []
    serve(true, calls)
    app()
    await waitFor(() => expect(screen.getByTestId('status-bar').textContent).toContain('Rendering 1 clip'))
    expect(screen.getByTestId('status-bar').textContent).toContain('Recording chan')
    expect(screen.getByTestId('sb-update').textContent).toBe('Update to 10.0.0')
    expect(within(screen.getByTestId('nav-exports')).getByText('1')).toBeTruthy()          // the badge
    const asked = (window as unknown as { askQuit: () => Promise<void> }).askQuit()
    fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Cancel' }))
    await asked
    expect(calls).not.toContain('/api/quit')
    fireEvent.click(screen.getByTestId('quit'))
    fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Quit' }))
    expect(await screen.findByText('Clip Radar has stopped. You can close this window.')).toBeTruthy()
    expect(calls).toContain('/api/quit')
  })
  it("going to another screen and back keeps Review as you left it, and keys pressed elsewhere don't reach it", async () => {
    const calls: string[] = [], posts: Array<[string, unknown]> = []
    const CLIPS = [1, 2, 3].map(id => ({ id, pid: 'p1', start: id * 20, end: id * 20 + 15, title: `Clip ${id}`, vod: '/vod/p1', listened: true, titles: [] }))
    vi.stubGlobal('fetch', vi.fn(async (path: string, init?: RequestInit) => {
      calls.push(path)
      if (init?.body) posts.push([path, JSON.parse(String(init.body))])
      const answer = path === '/api/status' ? { version: '9.9.9', api: 1, ui: 'web' }
        : path === '/api/project/p1' ? { pid: 'p1', title: 'My stream', duration: 600, vod: '/vod/p1', clips: CLIPS }
        : path === '/api/queue' ? { items: [], eta: null } : path === '/api/sources' ? { items: [] }
        : path === '/api/update' ? { available: false } : path === '/api/recent' ? [] : { ok: true }
      return new Response(JSON.stringify(answer), { status: 200 })
    }))
    HTMLMediaElement.prototype.play = vi.fn(async () => {})
    HTMLMediaElement.prototype.pause = vi.fn()
    location.hash = '#review/p1'
    app()
    const rows = await screen.findAllByTestId('rv-row')
    fireEvent.click(rows[2]!)                                         // the third clip
    fireEvent.click(screen.getByRole('tab', { name: /^All/ }))       // the All list
    await waitFor(() => expect((screen.getByTestId('rv-title-input') as HTMLInputElement).value).toBe('Clip 3'))
    location.hash = '#exports'
    window.dispatchEvent(new HashChangeEvent('hashchange'))
    await screen.findByText(/Nothing rendered yet/)
    fireEvent.keyDown(window, { key: 'x' })                           // on Exports: not a Skip
    fireEvent.keyDown(window, { key: 'Enter' })                       // nor a Keep
    expect(screen.getByTestId('nav-review').getAttribute('href')).toBe('#review/p1')
    location.hash = '#review/p1'
    window.dispatchEvent(new HashChangeEvent('hashchange'))
    await waitFor(() => expect((screen.getByTestId('rv-title-input') as HTMLInputElement).value).toBe('Clip 3'))
    expect(screen.getByRole('tab', { name: /^All/ }).getAttribute('aria-selected')).toBe('true')
    expect(calls.filter(p => p === '/api/project/p1')).toHaveLength(1)                 // not opened again
    expect(posts.filter(([p, b]) => p === '/api/clip_state' && ['skipped', 'kept'].includes((b as { state: string }).state))).toEqual([])
    expect(posts.some(([p]) => p === '/api/queue/add')).toBe(false)
  })
})
