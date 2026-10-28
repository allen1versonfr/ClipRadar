import { useState } from 'react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ToastProvider } from '../../components/Toasts'
import { HashtagsPanel } from './HashtagsPanel'
import { TitlesPanel } from './TitlesPanel'

let prefs: Record<string, unknown>
type Call = [string, Record<string, unknown> | undefined]
let calls: Call[]

beforeEach(() => {
  calls = []
  prefs = { pick: 'a', hashtags: true, chat_titles: true }
  vi.stubGlobal('fetch', vi.fn(async (path: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : undefined
    calls.push([path, body])
    if (path === '/api/titles') {
      if (body) Object.assign(prefs, body)
      return new Response(JSON.stringify(prefs), { status: 200 })
    }
    return new Response(JSON.stringify({   // /api/hashtags: the preview follows the post-caption switch
      config: { file: { enabled: true, streamer_tag: true, general: [], streamers: {}, max: 4 },
        post: { auto: true, global: [], streamers: {}, platforms: {}, max: 8 } },
      suggestions: [], preview: { file: 'clip.mp4', post: { tiktok: ['#twitch'] }, post_on: prefs.hashtags },
    }), { status: 200 })
  }))
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function Both() {                  // as Settings wires them
  const [k, setK] = useState(0)
  return <><TitlesPanel onHashtags={() => setK(x => x + 1)} /><HashtagsPanel reloadKey={k} /></>
}

describe('Titles and captions', () => {
  it('saves which title new clips get, and titles from chat', async () => {
    render(<ToastProvider><TitlesPanel onHashtags={() => {}} /></ToastProvider>)
    fireEvent.click(await screen.findByRole('radio', { name: "B · with the streamer's name" }))
    await waitFor(() => expect(prefs.pick).toBe('b'))
    expect(screen.getByRole('radio', { name: "B · with the streamer's name" }).getAttribute('aria-checked')).toBe('true')
    fireEvent.click(screen.getByRole('checkbox', { name: 'Titles from what chat said' }))
    await waitFor(() => expect(prefs.chat_titles).toBe(false))
  })

  it('turning post-caption hashtags off updates the Hashtags preview', async () => {
    render(<ToastProvider><Both /></ToastProvider>)
    expect((await screen.findByTestId('tag-preview')).textContent).toContain('TikTok: #twitch')
    await waitFor(() => expect((screen.getByRole('checkbox', { name: 'Add hashtags to post captions' }) as HTMLInputElement).disabled).toBe(false))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Add hashtags to post captions' }))
    await waitFor(() => expect(screen.getByTestId('tag-preview').textContent).toContain('Post captions: hashtags are off'))
    expect(calls.filter(([p]) => p.startsWith('/api/hashtags')).length).toBe(2)
  })
})
