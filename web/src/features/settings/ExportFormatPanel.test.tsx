import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ToastProvider } from '../../components/Toasts'
import { codecPatch, ExportFormatPanel, insertToken } from './ExportFormatPanel'

const SETTINGS = { scale: 1, fps: 0, vcodec: 'h264', crf: 20, vbitrate: 0, abitrate: 192, container: 'mp4', loudness: -14 }
let prefs: Record<string, any>
let saved: Array<Record<string, any>>

function view() {
  return {
    describe: `${prefs.preset} · ${prefs.quality}`,
    presets: [{ key: 'tiktok', label: 'TikTok', note: '1080 × 1920' }, { key: 'x', label: 'X', note: '720 × 1280' }],
    quality: [{ key: 'balanced', label: 'Balanced', note: 'Recommended.' }, { key: 'high', label: 'High quality', note: 'Sharper.' }],
    tokens: ['title', 'streamer', 'date'], preview_name: `${prefs.template.replace('{title}', 'Chat lost it')}.mp4`,
    settings: { ...SETTINGS, ...prefs.custom }, prefs,
  }
}

beforeEach(() => {
  saved = []
  prefs = { preset: 'tiktok', quality: 'balanced', custom: {}, subtitles: 'burn', sounds: true, denoise: false, voice: false, template: '{title}' }
  vi.stubGlobal('fetch', vi.fn(async (_path: string, init?: RequestInit) => {
    if (init?.body) {
      const b = JSON.parse(String(init.body)) as Record<string, any>
      saved.push(b)
      Object.assign(prefs, b)
    }
    return new Response(JSON.stringify(view()), { status: 200 })
  }))
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const renderPanel = () => render(<ToastProvider><ExportFormatPanel /></ToastProvider>)

describe('Export format', () => {
  it('picks a preset and a quality, with their notes', async () => {
    renderPanel()
    fireEvent.click(await screen.findByRole('radio', { name: 'X' }))
    await waitFor(() => expect(saved).toEqual([{ preset: 'x' }]))
    expect(await screen.findByText('720 × 1280')).toBeTruthy()
    fireEvent.click(screen.getByRole('radio', { name: 'High quality' }))
    await waitFor(() => expect(screen.getByTestId('ex-describe').textContent).toBe('x · high'))
  })

  it('advanced settings are your changes on top of the preset, and can be reset', async () => {
    renderPanel()
    fireEvent.change(await screen.findByLabelText('Resolution'), { target: { value: '0.5' } })
    await waitFor(() => expect(saved.at(-1)).toEqual({ custom: { scale: 0.5 } }))
    fireEvent.change(screen.getByLabelText('Video quality'), { target: { value: 'b:12000' } })
    await waitFor(() => expect(saved.at(-1)).toEqual({ custom: { scale: 0.5, vbitrate: 12000 } }))
    fireEvent.click(await screen.findByRole('button', { name: "Back to the preset's settings" }))
    await waitFor(() => expect(saved.at(-1)).toEqual({ custom: {} }))
    await waitFor(() => expect(screen.queryByRole('button', { name: "Back to the preset's settings" })).toBeNull())
  })

  it('VP9 makes the file WebM', async () => {
    renderPanel()
    fireEvent.change(await screen.findByLabelText('Video codec'), { target: { value: 'vp9' } })
    await waitFor(() => expect(saved.at(-1)).toEqual({ custom: { vcodec: 'vp9', container: 'webm' } }))
  })

  it('file names: typed text saves after a pause; a token goes in at the cursor', async () => {
    renderPanel()
    const box = await screen.findByPlaceholderText('{title}') as HTMLInputElement
    vi.useFakeTimers()
    fireEvent.change(box, { target: { value: '{title} clip' } })
    expect(saved).toEqual([])
    await act(async () => { await vi.advanceTimersByTimeAsync(500) })
    vi.useRealTimers()
    await waitFor(() => expect(saved).toEqual([{ template: '{title} clip' }]))
    await waitFor(() => expect(screen.getByTestId('ex-preview').textContent).toBe('Chat lost it clip.mp4'))
    box.setSelectionRange(7, 7)
    fireEvent.click(screen.getByRole('button', { name: '{streamer}' }))
    await waitFor(() => expect(saved.at(-1)).toEqual({ template: '{title} - {streamer} clip' }))
  })
})

describe('rules', () => {
  it('codec and file type go together', () => {
    const st = { ...SETTINGS }
    expect(codecPatch(st, { vcodec: 'vp9' })).toEqual({ vcodec: 'vp9', container: 'webm' })
    expect(codecPatch({ ...st, vcodec: 'vp9', container: 'webm' }, { vcodec: 'hevc' })).toEqual({ vcodec: 'hevc', container: 'mp4' })
    expect(codecPatch({ ...st, vcodec: 'vp9', container: 'webm' }, { container: 'mov' })).toEqual({ container: 'mov', vcodec: 'h264' })
    expect(codecPatch(st, { container: 'mov' })).toEqual({ container: 'mov', vcodec: 'h264' })
  })
  it('a token is separated from the text before it', () => {
    expect(insertToken('{title}', 7, 'date')).toBe('{title} - {date}')
    expect(insertToken('{title} ', 8, 'date')).toBe('{title} {date}')
    expect(insertToken('', 0, 'title')).toBe('{title}')
  })
})
