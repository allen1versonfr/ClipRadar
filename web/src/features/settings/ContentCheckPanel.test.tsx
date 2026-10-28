import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ToastProvider } from '../../components/Toasts'
import { ContentCheckPanel } from './ContentCheckPanel'

const BASE = {
  enabled: true, categories: { slurs: true, profanity: true, gambling: false },
  category_info: [{ key: 'slurs', label: 'Hate slur', severity: 3 }, { key: 'profanity', label: 'Swearing', severity: 1 },
    { key: 'gambling', label: 'Gambling', severity: 1 }],
  censor: 'off', censor_categories: ['slurs'], hold: true, custom: ['Alex'], auto: true, notify: 'now',
  sensitivity: 'all', export_flagged: 'ask', remember_dismissed: true, auto_recheck: true,
}
type Call = [string, Record<string, unknown> | undefined]
let calls: Call[]
let cfg: Record<string, unknown>

beforeEach(() => {
  calls = []
  cfg = structuredClone(BASE)
  vi.stubGlobal('fetch', vi.fn(async (path: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : undefined
    calls.push([path, body])
    if (body) {
      Object.assign(cfg, body)
      if (typeof body.custom === 'string') cfg.custom = body.custom.split(',').map(x => x.trim()).filter(Boolean)
    }
    return new Response(JSON.stringify(cfg), { status: 200 })
  }))
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const renderPanel = () => render(<ToastProvider><ContentCheckPanel /></ToastProvider>)
const posted = () => calls.filter(([, b]) => b !== undefined).map(([, b]) => b)

describe('Content check', () => {
  it('lists each category with its severity, what is checked and censored', async () => {
    renderPanel()
    const table = await screen.findByTestId('sf-table')
    expect(table.textContent).toContain('Hate slur · Serious')
    expect((within(table).getByLabelText('Check Hate slur') as HTMLInputElement).checked).toBe(true)
    expect((within(table).getByLabelText('Censor Hate slur') as HTMLInputElement).checked).toBe(true)
    expect((within(table).getByLabelText('Censor Gambling') as HTMLInputElement).disabled).toBe(true)   // not checked, so nothing to censor
  })

  it('saves a category, censoring, and the choices, as the current UI does', async () => {
    renderPanel()
    fireEvent.click(await screen.findByLabelText('Check Gambling'))
    await waitFor(() => expect(posted()).toContainEqual({ categories: { slurs: true, profanity: true, gambling: true } }))
    fireEvent.click(screen.getByLabelText('Censor Swearing'))
    await waitFor(() => expect(posted()).toContainEqual({ censor_categories: ['slurs', 'profanity'] }))
    fireEvent.click(screen.getByRole('radio', { name: 'Bleep it' }))
    await waitFor(() => expect(posted()).toContainEqual({ censor: 'bleep' }))
    expect(await screen.findByText('Flagged words are hidden in captions and bleeped in the audio.')).toBeTruthy()
    fireEvent.click(screen.getByRole('radio', { name: 'Not until reviewed' }))
    await waitFor(() => expect(posted()).toContainEqual({ export_flagged: 'block' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Remember flags I dismiss' }))
    await waitFor(() => expect(posted()).toContainEqual({ remember_dismissed: false }))
  })

  it('saves your own words when you leave the box, not on every key', async () => {
    renderPanel()
    const box = await screen.findByPlaceholderText(/Your real name/)
    expect((box as HTMLTextAreaElement).value).toBe('Alex')
    fireEvent.change(box, { target: { value: 'Alex, Springfield' } })
    expect(posted()).toEqual([])
    fireEvent.blur(box)
    await waitFor(() => expect(posted()).toEqual([{ custom: 'Alex, Springfield' }]))
    await waitFor(() => expect((box as HTMLTextAreaElement).value).toBe('Alex, Springfield'))
  })

  it('turned off, only the switch and the note are left', async () => {
    renderPanel()
    await screen.findByTestId('sf-table')
    fireEvent.click(screen.getByRole('checkbox', { name: 'Check clips' }))
    await waitFor(() => expect(posted()).toEqual([{ enabled: false }]))
    expect(screen.queryByTestId('sf-table')).toBeNull()
    expect(screen.getByText(/safety net, not a guarantee/)).toBeTruthy()
  })
})
