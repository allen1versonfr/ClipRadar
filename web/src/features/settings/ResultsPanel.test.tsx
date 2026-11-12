import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ConfirmProvider } from '../../components/Confirm'
import { ToastProvider } from '../../components/Toasts'
import { headline, ResultsPanel } from './ResultsPanel'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

const G = (name: string, median: number) => ({ name, videos: 2, views: median * 2, median, like_rate: 3, share_rate: 0.1, comments_per_1k: 0.5 })
const VIEW = { imported: Date.now() / 1000, videos: 15, matched: 12, total: [G('All videos', 9000)],
  by_streamer: [G('Adin', 118000), G('Jason', 8500)], by_series: [G('Jason × Emily', 9000)], by_kind: [G('Arguments and drama', 40000), G('Everything else', 7000)],
  list: [{ title: 'Adin fries Wendy Ortiz', views: 184583, clip: 'Adin fries Wendy' }] }

describe('Settings › TikTok results', () => {
  it('says how the best streamer compares with the next', () => {
    expect(headline([G('Adin', 118000), G('Jason', 8500)])).toBe('Adin: 118K views per video, 13.9× Jason (8.5K).')
    expect(headline([G('A', 100), G('B', 90)])).toBe('')
    expect(headline([G('A', 100)])).toBe('')
  })

  it('imports an export and says what it did', async () => {
    const calls: Array<[string, unknown]> = []
    vi.stubGlobal('fetch', vi.fn(async (p: string, init?: RequestInit) => {
      calls.push([p, init?.body ? JSON.parse(String(init.body)) : undefined])
      const body = p === '/api/results/import' ? { ...VIEW, last: { videos: 15, matched: 12, learned: 10 } } : VIEW
      return new Response(JSON.stringify(body), { status: 200 })
    }))
    render(<ToastProvider><ConfirmProvider><ResultsPanel /></ConfirmProvider></ToastProvider>)
    expect((await screen.findByTestId('results-headline')).textContent).toMatch(/^Adin: 118K views per video/)
    expect(screen.getByTestId('results-kind').textContent).toMatch(/Arguments and drama/)
    const file = new File(['Video title,Total views\nx,1\n'], 'content.csv', { type: 'text/csv' })
    fireEvent.change(screen.getByTestId('results-file'), { target: { files: [file] } })
    expect((await screen.findByTestId('results-last')).textContent).toBe('15 videos read, 12 matched to your clips, 10 clips given to ranking.')
    const sent = calls.find(([p]) => p === '/api/results/import')![1] as { name: string; data: string }
    expect(sent.name).toBe('content.csv')
    expect(atob(sent.data)).toMatch(/^Video title,Total views/)
  })
})
