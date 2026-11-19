import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ConfirmProvider } from '../../components/Confirm'
import { ToastProvider } from '../../components/Toasts'
import { SegmentsView } from './SegmentsView'

afterEach(() => { cleanup(); vi.unstubAllGlobals(); localStorage.clear() })

const seg = (id: string, name: string, start: number, end: number, on = true) => ({ id, name, start, end, cuts: [], gain_db: 0, on, lines: null })
const EDIT = { id: 'e1', name: 'Best bits', pid: 'p1', title: 'Friday stream', duration: 600, ver: 1, length: 50, open: true, exists: true,
  segments: [seg('a', 'Opening', 10, 40), seg('b', 'The fight', 100, 120), seg('c', 'Not used', 200, 210, false)], export_job: null, hear_job: null }

function server(calls: Array<[string, Record<string, unknown> | undefined]>) {
  return vi.fn(async (path: string, init?: RequestInit) => {
    calls.push([path, init?.body ? JSON.parse(String(init.body)) : undefined])
    const body = path === '/api/segments' ? { edits: [EDIT] } : path.startsWith('/api/vodexport/words') ? { words: [], covered: [] }
      : path.startsWith('/api/strip/p1') ? { a: 0, b: 600, hz: 1, wave: [], events: {}, chat: [], start: 0 } : { job: 'j1', ok: true }
    return new Response(JSON.stringify(body), { status: 200 })
  })
}
const renderIt = () => render(<ToastProvider><ConfirmProvider><SegmentsView open="e1" /></ConfirmProvider></ToastProvider>)

describe('Segments (5.0.1: a video for each segment)', () => {
  it('exports each ticked segment as its own video by default', async () => {
    const calls: Array<[string, Record<string, unknown> | undefined]> = []
    vi.stubGlobal('fetch', server(calls))
    renderIt()
    const btn = await screen.findByTestId('segv-export')
    expect(btn.textContent).toBe('Export 2 videos · 0:50')
    fireEvent.click(btn)
    await waitFor(() => expect(calls.find(([p]) => p === '/api/segments/export')?.[1]).toMatchObject({ id: 'e1', mode: 'each', segment: '' }))
  })

  it('one segment can be exported on its own', async () => {
    const calls: Array<[string, Record<string, unknown> | undefined]> = []
    vi.stubGlobal('fetch', server(calls))
    renderIt()
    fireEvent.click(await screen.findByTestId('segv-export-one'))
    await waitFor(() => expect(calls.find(([p]) => p === '/api/segments/export')?.[1]).toMatchObject({ id: 'e1', segment: 'a' }))
  })

  it('or all joined into one video, as before', async () => {
    const calls: Array<[string, Record<string, unknown> | undefined]> = []
    vi.stubGlobal('fetch', server(calls))
    renderIt()
    await screen.findByTestId('segv-export')
    fireEvent.click(screen.getByRole('radio', { name: 'All joined into one video' }))
    expect(screen.getByTestId('segv-export').textContent).toMatch(/^Export one video · 0:50/)
    fireEvent.click(screen.getByTestId('segv-export'))
    await waitFor(() => expect(calls.find(([p]) => p === '/api/segments/export')?.[1]).toMatchObject({ mode: 'join' }))
  })
})
