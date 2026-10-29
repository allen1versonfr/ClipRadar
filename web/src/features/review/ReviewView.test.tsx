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

  it('an edit saved after a pause never undoes a keep made in the meantime', async () => {
    renderReview()
    await waitFor(() => expect((screen.getByTestId('rv-title-input') as HTMLInputElement).value).toBe('Clip 1'))
    fireEvent.change(screen.getByTestId('rv-title-input'), { target: { value: 'Mine' } })
    fireEvent.click(screen.getByTestId('rv-keep'))                 // within the pause
    await waitFor(() => expect(posted('/api/queue/add')).toHaveLength(1))
    await new Promise(r => setTimeout(r, 800))
    const last = posted('/api/clip_state').filter(b => b.id === 1).at(-1)
    expect(last).toMatchObject({ state: 'kept', title: 'Mine' })
  })

  it("the server's older cut, arriving while Restore isn't saved yet, doesn't undo it", async () => {
    let release: (v: unknown) => void = () => {}, asked: (v: unknown) => void = () => {}
    const gate = new Promise(r => { release = r }), polled = new Promise(r => { asked = r })
    const trimmed = { ...CLIPS[0]!, start: 22, end: 30, orig: [20, 35], userTrim: true }
    const base = fetch as unknown as (p: string, i?: RequestInit) => Promise<Response>
    vi.stubGlobal('fetch', vi.fn(async (path: string, init?: RequestInit) => {
      if (path === '/api/project/p1') return new Response(JSON.stringify({ pid: 'p1', title: 'S', duration: 600, vod: '/vod/p1', clips: [trimmed, CLIPS[1]] }))
      if (path === '/api/clips/p1') {
        asked(null)
        await gate                                      // the answer that left before Restore was saved
        return new Response(JSON.stringify({ clips: [trimmed, CLIPS[1]], listening: true }))
      }
      return base(path, init)
    }))
    renderReview()
    const restore = await screen.findByRole('button', { name: 'Restore original cut' })
    await polled                                        // a request for the clips is on its way
    fireEvent.click(restore)
    release(null)
    await new Promise(r => setTimeout(r, 900))
    expect(posted('/api/clip_state').filter(b => b.id === 1).at(-1)).toMatchObject({ start: 20, end: 35, userTrim: false })
  })

  it('T gives another suggested title, saved as yours', async () => {
    renderReview()
    await waitFor(() => expect((screen.getByTestId('rv-title-input') as HTMLInputElement).value).toBe('Clip 1'))     // selected
    fireEvent.keyDown(window, { key: 't' })
    expect((screen.getByTestId('rv-title-input') as HTMLInputElement).value).toBe('Other 1')
  })

  it('re-ranking with other detection settings keeps what you did to clips still suggested', async () => {
    const base = fetch as unknown as (p: string, i?: RequestInit) => Promise<Response>
    vi.stubGlobal('fetch', vi.fn(async (path: string, init?: RequestInit) => {
      if (path === '/api/repick') {
        const body = JSON.parse(String(init!.body)) as Record<string, any>
        calls.push([path, body])
        if (body.dry_run) return new Response(JSON.stringify({ count: 2, same: 1, added: 1, dropped: 2, top: [{ start: 20, end: 35, quality: 70, reason: 'Chat went off' }] }))
        return new Response(JSON.stringify({ pid: 'p1', title: 'My stream', duration: 600, vod: '/vod/p1', detect_text: 'Gaming',
          clips: [{ ...CLIPS[0]!, title: 'Server title' }, { id: 4, pid: 'p1', start: 300, end: 320, title: 'New one', vod: '/vod/p1' }] }))
      }
      return base(path, init)
    }))
    renderReview()
    await waitFor(() => expect((screen.getByTestId('rv-title-input') as HTMLInputElement).value).toBe('Clip 1'))
    fireEvent.change(screen.getByTestId('rv-title-input'), { target: { value: 'Mine' } })
    fireEvent.click(screen.getByTestId('rv-detect-open'))
    fireEvent.click(await screen.findByTestId('rv-det-try'))
    expect((await screen.findByTestId('rv-det-cmp')).textContent).toContain('2 clips. 1 the same as now, 1 new, 2 dropped')
    fireEvent.click(screen.getByTestId('rv-det-apply'))
    await waitFor(() => expect(screen.getAllByTestId('rv-row')).toHaveLength(2))
    expect(posted('/api/repick').at(-1)).toMatchObject({ pid: 'p1', detect: { preset: 'any' } })
    expect(posted('/api/repick').at(-1)!.dry_run).toBeUndefined()
    expect((screen.getByTestId('rv-title-input') as HTMLInputElement).value).toBe('Mine')
    expect(screen.queryByTestId('rv-detect')).toBeNull()
  })

  it('a moment of the stream outside the clips previews, and becomes a clip', async () => {
    const base = fetch as unknown as (p: string, i?: RequestInit) => Promise<Response>
    vi.stubGlobal('fetch', vi.fn(async (path: string, init?: RequestInit) => {
      if (path === '/api/add_clip') {
        const body = JSON.parse(String(init!.body)) as Record<string, any>
        calls.push([path, body])
        return new Response(JSON.stringify({ id: 9, pid: 'p1', start: body.t - 10, end: body.t + 10, title: '', vod: '/vod/p1', manual: true }))
      }
      return base(path, init)
    }))
    renderReview()
    await screen.findAllByTestId('rv-row')
    fireEvent.click(screen.getByTestId('rv-heat'), { clientX: 0.5 })      // the canvas has no size here: 0.5 px is 300 s
    expect((await screen.findByTestId('rv-free')).textContent).toContain('Previewing 5:00 of the stream.')
    fireEvent.click(screen.getByTestId('rv-add-here'))
    await waitFor(() => expect(screen.getAllByTestId('rv-row')).toHaveLength(4))
    expect(posted('/api/add_clip')[0]).toMatchObject({ pid: 'p1', length: 0 })
    expect(screen.queryByTestId('rv-free')).toBeNull()
    expect(screen.getAllByTestId('rv-row').at(-1)!.getAttribute('aria-current')).toBe('true')
    expect(posted('/api/listen').at(-1)).toMatchObject({ order: [9] })
  })

  it("title B with the streamer's name: set the name, then pick it", async () => {
    const base = fetch as unknown as (p: string, i?: RequestInit) => Promise<Response>
    const ab = { ...CLIPS[0]!, title_a: 'He missed', title_b: 'Streamer missed', title_b_named: false }
    vi.stubGlobal('fetch', vi.fn(async (path: string, init?: RequestInit) => {
      if (path === '/api/project/p1') return new Response(JSON.stringify({ pid: 'p1', title: 'S', duration: 600, vod: '/vod/p1', clips: [ab] }))
      if (path === '/api/streamer') {
        calls.push([path, JSON.parse(String(init!.body))])
        return new Response(JSON.stringify({ name: 'Jason', clips: [{ id: 1, title: 'Clip 1', title_a: 'He missed', title_b: 'Jason missed', title_b_named: true }] }))
      }
      return base(path, init)
    }))
    renderReview()
    fireEvent.click(await screen.findByTestId('rv-streamer'))
    fireEvent.change(screen.getByTestId('rv-streamer-input'), { target: { value: ' Jason ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect((screen.getByTestId('rv-streamer')).textContent).toContain('Streamer: Jason · change'))
    expect(posted('/api/streamer')[0]).toEqual({ pid: 'p1', name: 'Jason' })
    fireEvent.click(within(screen.getByTestId('rv-title-opts')).getByRole('button', { name: /Jason missed/ }))
    expect((screen.getByTestId('rv-title-input') as HTMLInputElement).value).toBe('Jason missed')
    await waitFor(() => expect(posted('/api/clip_state').at(-1)).toMatchObject({ id: 1, title: 'Jason missed', userTitle: true }))
  })

  it('a video that is not open any more says how to get it back', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ error: 'This session expired. Press Find clips again.' }), { status: 404 })))
    renderReview()
    expect((await screen.findByRole('alert')).textContent).toContain('This session expired')
    expect(screen.getByRole('link', { name: 'Home › Recent' })).toBeTruthy()
  })
})
