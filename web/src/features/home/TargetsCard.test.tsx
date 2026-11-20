import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ToastProvider } from '../../components/Toasts'
import { TargetsCard } from './TargetsCard'

type Call = [string, Record<string, any> | undefined]
let calls: Call[]
let list: Array<Record<string, unknown>>
const SET = { mode: 'hands', frequency: 'normal', preset: 'any', min_quality: 0, length: 0, save_vod: true }

beforeEach(() => {
  calls = []
  list = [
    { id: 't1', platform: 'twitch', channel: 'shroud', name: 'shroud', source: 'https://www.twitch.tv/shroud', on: true, settings: SET,
      live: true, session: 'lv9', note: 'Live: clipping since 20:14.', vod: 'keeping', recording: true },
    { id: 't2', platform: 'youtube', channel: '@chan', name: 'chan', source: 'https://www.youtube.com/@chan/live', on: false,
      settings: { ...SET, save_vod: false }, live: null, session: '', note: 'Off' },
  ]
  vi.stubGlobal('fetch', vi.fn(async (path: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) as Record<string, any> : undefined
    calls.push([path, body])
    if (path === '/api/targets/add') {
      if (body!.source === 'nope!') return new Response(JSON.stringify({ error: 'Type a Twitch channel (a name or twitch.tv/name) or a YouTube channel link.' }), { status: 400 })
      list.push({ id: 't3', platform: 'twitch', channel: 'pokimane', name: 'pokimane', source: 'https://www.twitch.tv/pokimane', on: true,
        settings: body!.settings, live: false, session: '', note: 'Offline. Starts clipping when they go live.' })
    }
    return new Response(JSON.stringify({ targets: list, watching: true }), { status: 200 })
  }))
})
afterEach(() => { cleanup(); vi.unstubAllGlobals(); localStorage.clear() })

const show = () => render(<ToastProvider><TargetsCard /></ToastProvider>)
const sent = (p: string) => calls.filter(([q, b]) => q === p && b).map(([, b]) => b!)

describe('Targets', () => {
  it('lists each streamer with what is going on, and a live one opens its session', async () => {
    show()
    const rows = await screen.findAllByTestId('target')
    expect(rows).toHaveLength(2)
    expect(within(rows[0]!).getByTestId('target-note').textContent).toBe('Live: clipping since 20:14.')
    expect(within(rows[0]!).getByRole('link', { name: 'Open' }).getAttribute('href')).toBe('#review/live/lv9')
    expect(within(rows[0]!).getByTestId('rec-badge').textContent).toBe('REC · full stream')     // its full stream is being saved
    expect(within(rows[1]!).queryByRole('link', { name: 'Open' })).toBeNull()
    expect(within(rows[1]!).queryByTestId('rec-badge')).toBeNull()
    expect((within(rows[1]!).getByTestId('target-on') as HTMLInputElement).checked).toBe(false)
  })

  it('adds a streamer with the Live stream settings and your render style', async () => {
    localStorage.setItem('vc_liveFreq', JSON.stringify('lots'))
    localStorage.setItem('vc_liveSaveVod', JSON.stringify(false))
    show()
    await screen.findAllByTestId('target')
    fireEvent.change(screen.getByTestId('target-input'), { target: { value: 'twitch.tv/pokimane' } })
    fireEvent.click(screen.getByTestId('target-add'))
    await waitFor(() => expect(sent('/api/targets/add')).toHaveLength(1))
    const b = sent('/api/targets/add')[0]!
    expect(b.source).toBe('twitch.tv/pokimane')
    expect(b.settings).toMatchObject({ frequency: 'lots', save_vod: false, mode: 'hands' })
    expect(b.params.opts).toBeDefined()
    expect(await screen.findAllByTestId('target')).toHaveLength(3)
  })

  it("a channel it can't watch says what to type", async () => {
    show()
    await screen.findAllByTestId('target')
    fireEvent.change(screen.getByTestId('target-input'), { target: { value: 'nope!' } })
    fireEvent.click(screen.getByTestId('target-add'))
    expect((await screen.findByRole('alert')).textContent).toMatch(/Twitch channel/)
  })

  it('switches one off and changes its settings, saving the whole stream included', async () => {
    show()
    const rows = await screen.findAllByTestId('target')
    fireEvent.click(within(rows[0]!).getByTestId('target-on'))
    await waitFor(() => expect(sent('/api/targets/update')).toContainEqual({ id: 't1', on: false }))
    fireEvent.click(within(rows[0]!).getByTestId('target-edit'))
    const form = await screen.findByTestId('target-settings')
    fireEvent.click(within(form).getByRole('radio', { name: 'Review each clip' }))
    await waitFor(() => expect(sent('/api/targets/update')).toContainEqual({ id: 't1', settings: { mode: 'review' } }))
    fireEvent.click(within(form).getByTestId('target-vod'))
    await waitFor(() => expect(sent('/api/targets/update')).toContainEqual({ id: 't1', settings: { save_vod: false } }))
  })

  it('opening Home brings every target up to date with your render style', async () => {
    show()
    await waitFor(() => expect(sent('/api/targets/update').some(b => b.id === '*' && b.params?.opts)).toBe(true))
  })
})
