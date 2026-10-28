import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Mark } from '../../api/schemas'
import { ConfirmProvider } from '../../components/Confirm'
import { ToastProvider } from '../../components/Toasts'
import { dragTo, markAnchor } from './geometry'
import { WatermarkPanel } from './WatermarkPanel'

const mark = (over: object) => Mark.parse({ kind: 'text', text: '@me', ...over })
let prefs: Record<string, any>
let camps: Record<string, any>
type Call = [string, Record<string, any> | undefined]
let calls: Call[]
let slowExport = 0

beforeEach(() => {
  calls = []
  prefs = { watermark: { on: true, marks: [mark({ text: '@kai' })] }, wm_presets: [{ name: 'Mafia', marks: [mark({ text: '@mafia', pos: 'br' })] }] }
  camps = { list: [], active: '' }
  slowExport = 0
  vi.stubGlobal('fetch', vi.fn(async (path: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) as Record<string, any> : undefined
    calls.push([path, body])
    let answer: unknown = { prefs, settings: {} }
    if (path === '/api/export' && body) Object.assign(prefs, body)
    if (path.startsWith('/api/campaigns')) {
      if (body?.campaign) camps.list = camps.list.map((c: any) => (c.id === body.campaign.id ? body.campaign : c))
      answer = camps
    }
    if (path === '/api/pick_file') answer = { path: 'C:/logo.png' }
    if (path === '/api/watermark/image') answer = { image: 'D:/data/watermarks/ab12.png' }
    if (path === '/api/watermark/share') answer = { name: `${body?.name} - watermark.json`, path: 'x' }
    if (path === '/api/export' && !body && slowExport) await new Promise(r => setTimeout(r, slowExport))
    return new Response(JSON.stringify(answer), { status: 200 })
  }))
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const renderPanel = () => render(<ToastProvider><ConfirmProvider><WatermarkPanel /></ConfirmProvider></ToastProvider>)
const posted = (path: string) => calls.filter(([p, b]) => p === path && b).map(([, b]) => b!)

describe('Watermark', () => {
  it('edits the selected layer; typing is saved after a pause', async () => {
    renderPanel()
    const text = await screen.findByDisplayValue('@kai')
    vi.useFakeTimers()
    fireEvent.change(text, { target: { value: '@kaicenat' } })
    expect(posted('/api/export')).toEqual([])
    await act(async () => { await vi.advanceTimersByTimeAsync(450) })
    vi.useRealTimers()
    await waitFor(() => expect(posted('/api/export').at(-1)!.watermark.marks[0].text).toBe('@kaicenat'))
    fireEvent.click(screen.getByRole('radio', { name: 'Bottom right' }))
    await waitFor(() => expect(prefs.watermark.marks[0].pos).toBe('br'))
    expect(screen.getByLabelText(/Distance from the side/)).toBeTruthy()
    fireEvent.click(screen.getByRole('radio', { name: 'Anywhere' }))
    expect(await screen.findByLabelText(/Across/)).toBeTruthy()
    expect(screen.queryByLabelText(/Distance from the side/)).toBeNull()
  })

  it('a new layer stays while the settings come back in any order', async () => {
    renderPanel()
    await screen.findByDisplayValue('@kai')
    slowExport = 150                       // the campaigns answer comes back first
    fireEvent.click(screen.getByRole('button', { name: '+ Text layer' }))
    await waitFor(() => expect(prefs.watermark.marks).toHaveLength(2))
    const end = Date.now() + 400
    while (Date.now() < end) {
      expect(screen.queryByRole('button', { name: 'Remove layer 2' })).not.toBeNull()
      await new Promise(r => setTimeout(r, 10))
    }
  })

  it('an empty text layer is not saved while you type', async () => {
    renderPanel()
    fireEvent.change(await screen.findByDisplayValue('@kai'), { target: { value: '' } })
    await new Promise(r => setTimeout(r, 600))
    expect(posted('/api/export')).toEqual([])
  })

  it('adds an image layer from a picked file, and removes a layer', async () => {
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: '+ Image layer' }))
    await waitFor(() => expect(prefs.watermark.marks).toHaveLength(2))
    expect(prefs.watermark.marks[1]).toMatchObject({ kind: 'image', image: 'D:/data/watermarks/ab12.png', pos: 'tl' })
    expect(posted('/api/watermark/image')).toEqual([{ path: 'C:/logo.png' }])
    expect(screen.getByRole('button', { name: 'Change image…' })).toBeTruthy()       // the new layer is selected
    fireEvent.click(screen.getByRole('button', { name: 'Remove layer 1' }))
    await waitFor(() => expect(prefs.watermark.marks.map((m: any) => m.kind)).toEqual(['image']))
  })

  it('uses a saved watermark, saves one under a name, and asks before deleting', async () => {
    renderPanel()
    fireEvent.change(await screen.findByLabelText('Saved watermarks'), { target: { value: 'Mafia' } })
    await waitFor(() => expect(prefs.watermark.marks[0].text).toBe('@mafia'))
    fireEvent.click(screen.getByRole('button', { name: 'Save as…' }))
    const name = screen.getByLabelText('Name for this watermark')
    fireEvent.change(name, { target: { value: 'Main' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(prefs.wm_presets.map((p: any) => p.name)).toEqual(['Mafia', 'Main']))
    fireEvent.click(await screen.findByRole('button', { name: 'Delete' }))
    const dialog = await screen.findByRole('alertdialog', { name: 'Delete the "Main" watermark?' })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(prefs.wm_presets.map((p: any) => p.name)).toEqual(['Mafia']))
  })

  it("edits the active campaign's own watermark instead, until you choose yours", async () => {
    camps = { active: 'c1', list: [{ id: 'c1', name: 'Clipping Co', watermark: { use: true, marks: [mark({ text: '@clipco' })] } }] }
    renderPanel()
    expect((await screen.findByTestId('wm-camp')).textContent).toContain('Clipping Co')
    const text = await screen.findByDisplayValue('@clipco')
    fireEvent.change(text, { target: { value: '@clipco2' } })
    await waitFor(() => expect(posted('/api/campaigns/save').at(-1)!.campaign.watermark).toMatchObject({ use: true, marks: [{ text: '@clipco2' }] }))
    expect(posted('/api/export')).toEqual([])
    fireEvent.click(screen.getByRole('button', { name: 'Use mine instead' }))
    expect(await screen.findByDisplayValue('@kai')).toBeTruthy()
    expect(screen.queryByTestId('wm-camp')).toBeNull()
  })
})

describe('placement', () => {
  it('matches the renderer: corners clear of the bars, kept inside, centre', () => {
    expect(markAnchor(mark({ pos: 'tl', margin: 40 }), 1080, 1920, 200, 50)).toEqual([40, 1920 * 0.13])
    expect(markAnchor(mark({ pos: 'br', margin: 40 }), 1080, 1920, 200, 50)).toEqual([840, 1920 * 0.8 - 50])
    expect(markAnchor(mark({ pos: 'c' }), 1080, 1920, 200, 50)).toEqual([440, 935])
    expect(markAnchor(mark({ pos: 'free', x: 1, y: 0 }), 1080, 1920, 200, 50)).toEqual([880, 0])
    expect(markAnchor(mark({ pos: 'tr', margin: 40 }), 360, 640, 100, 20)[0]).toBeCloseTo(360 - 100 - 40 / 3)
  })
  it('a dragged layer is placed by hand, snapping to the middle', () => {
    expect(dragTo(185, 320, 360, 640)).toEqual({ pos: 'free', x: 0.5, y: 0.5 })
    expect(dragTo(-20, 700, 360, 640)).toEqual({ pos: 'free', x: 0, y: 1 })
  })
})
