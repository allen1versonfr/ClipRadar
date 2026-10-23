import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ConfirmProvider } from '../../components/Confirm'
import { ToastProvider } from '../../components/Toasts'
import { Explain } from '../../api/schemas'
import { explainFacts, LearningPanel, trimNote } from './LearningPanel'

const PROFILES = [
  { streamer: 'bigstreamer', decisions: 12, kept: 8, posted: 1, accept: 67, added: 1, trim: { start: -1.5, end: 2, n: 6 },
    reasons: [{ key: 'boring', label: 'Not interesting', n: 2 }] },
  { streamer: 'smallchannel', decisions: 3, kept: 3, posted: 0, accept: 100, added: 0, trim: null, reasons: [] },
]
const EXPLAIN = {
  status: 'tuned', status_text: 'Ranking for bigstreamer is adjusted from 12 of your decisions.',
  facts: { decisions: 12, kept: 8, skipped: 4, posted: 1, added: 1, reasons: [{ label: 'Not interesting', n: 2 }], trim: { start: -1.5, end: 2, n: 6 } },
  compare: [{ label: 'Loudness', kept: 0.54, skipped: 0.41, says: 'higher in clips you kept' }, { label: 'Cheering', kept: 0.5, skipped: 0.5, says: 'about the same' }],
  learned: [{ label: 'Talking', direction: 'up', strength: 'strongly' }],
  rules: ['The stream getting louder than the last few minutes'], method: 'A small statistical model.',
}
type Call = [string, Record<string, unknown> | undefined]
let calls: Call[]
let state: { enabled: boolean; profiles: typeof PROFILES }

function fakeServer() {
  return vi.fn(async (path: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : undefined
    calls.push([path, body])
    if (path === '/api/learning' && body && 'enabled' in body) state.enabled = Boolean(body.enabled)
    if (path === '/api/learning/forget' && body) state.profiles = body.streamer ? state.profiles.filter(p => p.streamer !== body.streamer) : []
    const answer = path.startsWith('/api/learning/explain') ? EXPLAIN
      : { ...state, total: state.profiles.reduce((n, p) => n + p.decisions, 0), min_decisions: 8 }
    return new Response(JSON.stringify(answer), { status: 200 })
  })
}

beforeEach(() => {
  calls = []
  state = { enabled: true, profiles: [...PROFILES] }
  vi.stubGlobal('fetch', fakeServer())
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

const renderPanel = () => render(<ToastProvider><ConfirmProvider><LearningPanel /></ConfirmProvider></ToastProvider>)
const posted = (path: string) => calls.filter(([p, b]) => p === path && b !== undefined).map(([, b]) => b)

describe('Clip ranking', () => {
  it('lists what was learned for each streamer', async () => {
    renderPanel()
    const items = within(await screen.findByTestId('lr-list')).getAllByRole('listitem')
    expect(items[0]?.textContent).toContain('12 decisions, 8 kept (67% of suggestions), 1 posted')
    expect(items[0]?.textContent).toContain('1 clip you added by hand (moments it missed) · skipped for: not interesting 2 · clips start 1.5 s earlier, end 2 s later (from your trims)')
    expect(items[1]?.textContent).toContain('3 decisions, 3 kept (100% of suggestions) · starts after 8')
    expect(screen.getByText('15 decisions in total')).toBeTruthy()
  })

  it('opens and closes what it learned for a streamer', async () => {
    renderPanel()
    const btn = (await screen.findAllByRole('button', { name: 'What it learned' }))[0] as HTMLElement
    expect(btn.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(btn)
    const ex = await screen.findByTestId('lr-explain')
    expect(calls.some(([p]) => p === '/api/learning/explain?streamer=bigstreamer')).toBe(true)
    expect(btn.getAttribute('aria-expanded')).toBe('true')
    expect(ex.textContent).toContain('Loudness: higher in clips you kept (0.54 kept vs 0.41 skipped)')
    expect(ex.textContent).not.toContain('Cheering')                    // "about the same" isn't a difference
    expect(ex.textContent).toContain('Talking moves clips up strongly')
    fireEvent.click(btn)
    expect(screen.queryByTestId('lr-explain')).toBeNull()
  })

  it('forgets one streamer at once, and everything only after asking', async () => {
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: 'Forget what it learned for smallchannel' }))
    await waitFor(() => expect(posted('/api/learning/forget')).toEqual([{ streamer: 'smallchannel' }]))
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Forget what it learned for smallchannel' })).toBeNull())
    fireEvent.click(screen.getByRole('button', { name: 'Forget everything' }))
    fireEvent.click(within(await screen.findByRole('alertdialog', { name: 'Forget everything learned?' })).getByRole('button', { name: 'Cancel' }))
    expect(posted('/api/learning/forget')).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: 'Forget everything' }))
    fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Forget' }))
    await waitFor(() => expect(posted('/api/learning/forget').at(-1)).toEqual({ streamer: '' }))
    expect(await screen.findByText('Nothing learned yet. Keep and skip a few suggested clips.')).toBeTruthy()
  })

  it('turns learning off, which hides what was learned', async () => {
    renderPanel()
    await screen.findByTestId('lr-list')
    fireEvent.click(screen.getByRole('checkbox', { name: 'Learn from my picks' }))
    await waitFor(() => expect(posted('/api/learning')).toEqual([{ enabled: false }]))
    expect(screen.queryByTestId('lr-list')).toBeNull()
    expect((screen.getByRole('checkbox', { name: 'Learn from my picks' }) as HTMLInputElement).checked).toBe(false)
  })
})

describe('trimNote and explainFacts', () => {
  it('only mentions trims once there are five', () => {
    expect(trimNote({ start: -1, end: 0, n: 4 })).toBe('')
    expect(trimNote({ start: 0, end: 3, n: 5 })).toBe('clips end 3 s later (from your trims)')
    expect(explainFacts(Explain.parse({ ...EXPLAIN, facts: { ...EXPLAIN.facts, trim: { start: 1, end: 0, n: 2 } } })))
      .toContain('2 trims recorded (start and end shift after 5)')
  })
  it('lists the stored facts', () => {
    expect(explainFacts(Explain.parse(EXPLAIN))).toEqual([
      '12 decisions stored: 8 kept (1 posted), 4 skipped',
      '1 clip you made by hand (moments the finder missed)',
      'Skipped for: not interesting (2)',
      'clips start 1.5 s earlier, end 2 s later (from your trims)',
    ])
  })
})
