import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ConfirmProvider } from '../../components/Confirm'
import { ToastProvider } from '../../components/Toasts'
import { ReviewView } from './ReviewView'

type Call = [string, Record<string, any> | undefined]
let calls: Call[]
let queue: Array<Record<string, unknown>>
const CLIPS = [1, 2, 3].map(id => ({ id, pid: 'p1', start: id * 20, end: id * 20 + 15, title: `Clip ${id}`, quality: 90 - id * 20,
  vod: '/vod/p1', thumb: '', listened: true, titles: [`Clip ${id}`, `Other ${id}`] }))

beforeEach(() => {
  calls = []
  queue = []
  vi.stubGlobal('fetch', vi.fn(async (path: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) as Record<string, any> : undefined
    calls.push([path, body])
    let answer: unknown = { ok: true }
    if (path === '/api/project/p1') answer = { pid: 'p1', title: 'My stream', duration: 600, vod: '/vod/p1', clips: CLIPS }
    if (path === '/api/queue') answer = { items: queue, eta: null }
    if (path === '/api/queue/add') { queue.push({ id: 'q' + queue.length, status: 'queued', pid: body!.pid, clip_id: body!.clip.id }); answer = queue.at(-1) }
    if (path === '/api/safety') answer = { enabled: true, export_flagged: 'ask' }
    if (path === '/api/titles') answer = { pick: 'a', hashtags: true }
    return new Response(JSON.stringify(answer), { status: 200 })
  }))
  HTMLMediaElement.prototype.play = vi.fn(async () => {})
  HTMLMediaElement.prototype.pause = vi.fn()
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const renderReview = () => render(<ToastProvider><ConfirmProvider><ReviewView pid="p1" /></ConfirmProvider></ToastProvider>)
const posted = (path: string) => calls.filter(([p, b]) => p === path && b).map(([, b]) => b!)

describe('Review', () => {
  it('keeps a clip with its title and moves on; X skips the next and asks why', async () => {
    renderReview()
    const rows = await screen.findAllByTestId('rv-row')
    expect(rows).toHaveLength(3)
    fireEvent.click(screen.getByTestId('rv-keep'))
    await waitFor(() => expect(posted('/api/queue/add')).toHaveLength(1))
    expect(posted('/api/queue/add')[0]!.clip).toMatchObject({ id: 1, title: 'Clip 1', start: 20, end: 35 })
    await waitFor(() => expect((screen.getByTestId('rv-title-input') as HTMLInputElement).value).toBe('Clip 2'))
    expect(posted('/api/clip_state')).toContainEqual(expect.objectContaining({ id: 1, state: 'kept' }))
    fireEvent.keyDown(window, { key: 'x' })
    await waitFor(() => expect(posted('/api/clip_state')).toContainEqual(expect.objectContaining({ id: 2, state: 'skipped' })))
    fireEvent.click(within(screen.getByTestId('toasts')).getByRole('button', { name: 'Duplicate' }))
    expect(posted('/api/feedback')).toContainEqual({ pid: 'p1', clip: 2, action: 'reason', reason: 'duplicate' })
  })

  it('T gives another suggested title, saved as yours', async () => {
    renderReview()
    await screen.findAllByTestId('rv-row')
    fireEvent.keyDown(window, { key: 't' })
    expect((screen.getByTestId('rv-title-input') as HTMLInputElement).value).toBe('Other 1')
  })

  it('a video that is not open any more says how to get it back', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ error: 'This session expired. Press Find clips again.' }), { status: 404 })))
    renderReview()
    expect((await screen.findByRole('alert')).textContent).toContain('This session expired')
    expect(screen.getByRole('link', { name: 'Home › Recent' })).toBeTruthy()
  })
})
