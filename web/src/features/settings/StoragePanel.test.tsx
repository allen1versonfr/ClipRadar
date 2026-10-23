import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ConfirmProvider } from '../../components/Confirm'
import { ToastProvider } from '../../components/Toasts'
import { Cleanup } from '../../api/schemas'
import { lastCleanup, StoragePanel } from './StoragePanel'

const LOCS = [
  { key: 'projects', label: 'Projects', path: 'D:\\Clips', where: 'D:\\Clips', set: true, ok: true, problem: '', free: 50e9, low: false, bytes: null },
  { key: 'clips', label: 'Rendered clips', path: '', where: 'Inside each project folder', set: false, ok: false, problem: "E: isn't connected.", free: 2e9, low: true, bytes: null },
  { key: 'temp', label: 'Temporary files and cache', path: 'C:\\T', where: 'C:\\T', set: false, ok: true, problem: '', free: 9e9, low: false, bytes: 3e6 },
]
type Call = [string, Record<string, unknown> | undefined]
let calls: Call[]
let picked: string
let locErrors: Record<string, string>
let cleanCfg: Record<string, unknown>

function fakeServer() {
  return vi.fn(async (path: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : undefined
    calls.push([path, body])
    const answer = (() => {
      if (path === '/api/locations') return { locations: LOCS, errors: body ? locErrors : {} }
      if (path === '/api/storage') return { vods: { count: 3, bytes: 12e9 }, clips: { count: 1, bytes: 40e6 }, workroot: 'D:\\Clips', uninstaller: false }
      if (path === '/api/cleanup') {
        if (body) cleanCfg = body.config as Record<string, unknown>
        return { config: cleanCfg, last: { at: 0, freed: 0, removed: [], failed: [] } }
      }
      if (path === '/api/pick_file') return { path: picked }
      if (path === '/api/delete') return { deleted: 3, freed: 12e9, failed: [] }
      if (path === '/api/storage/clear_cache') return { freed: 3e6, locations: LOCS }
      return { ok: true }
    })()
    return new Response(JSON.stringify(answer), { status: 200 })
  })
}

beforeEach(() => {
  calls = []
  picked = ''
  locErrors = {}
  cleanCfg = { temp: true, skipped: false, vods: false, vod_days: 7 }
  vi.stubGlobal('fetch', fakeServer())
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

const refreshStatus = vi.fn(async () => {})
const renderPanel = () => render(<ToastProvider><ConfirmProvider><StoragePanel refreshStatus={refreshStatus} /></ConfirmProvider></ToastProvider>)
const posted = (path: string) => calls.filter(([p, b]) => p === path && b !== undefined).map(([, b]) => b)

describe('Storage', () => {
  it('lists each folder with its free space, and says what is wrong', async () => {
    renderPanel()
    const list = await screen.findByTestId('loc-list')
    await waitFor(() => expect(list.querySelectorAll('li').length).toBe(3))
    expect(list.textContent).toContain('50.0 GB free')
    expect(list.textContent).toContain('2.0 GB free (running low)')
    expect(list.textContent).toContain('using 3 MB')
    expect(screen.getByRole('alert').textContent).toBe("Rendered clips: E: isn't connected.")
    expect(screen.getAllByRole('button', { name: /^Use the default/ }).length).toBe(1)   // only the folder that was changed
  })

  it('changes a folder to the one picked, and does nothing when the picker is closed', async () => {
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: 'Change the Rendered clips folder' }))
    await waitFor(() => expect(calls.some(([p]) => p === '/api/pick_file')).toBe(true))
    expect(posted('/api/locations')).toEqual([])
    picked = 'E:\\Clips'
    fireEvent.click(screen.getByRole('button', { name: 'Change the Rendered clips folder' }))
    await waitFor(() => expect(posted('/api/locations')).toEqual([{ clips: 'E:\\Clips' }]))
    await waitFor(() => expect(refreshStatus).toHaveBeenCalled())
  })

  it('shows why a folder was refused', async () => {
    locErrors = { clips: "Clip Radar can't write to that folder." }
    picked = 'Z:\\'
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: 'Change the Rendered clips folder' }))
    expect(await screen.findByText("Clip Radar can't write to that folder.")).toBeTruthy()
  })

  it('deletes VODs only after asking, and says what it freed', async () => {
    renderPanel()
    expect((await screen.findByTestId('vod-use')).textContent).toBe('3 streams, 12.0 GB')
    await waitFor(() => expect((screen.getByRole('button', { name: 'Delete VODs' }) as HTMLButtonElement).disabled).toBe(false))
    fireEvent.click(screen.getByRole('button', { name: 'Delete VODs' }))
    const dialog = await screen.findByRole('alertdialog', { name: 'Delete 3 downloaded streams?' })
    expect(dialog.textContent).toContain('Frees 12.0 GB. Rendered clips are kept')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete VODs' }))
    await waitFor(() => expect(posted('/api/delete')).toEqual([{ what: 'vods' }]))
    expect(await screen.findByText('Deleted 3 streams and freed 12.0 GB.')).toBeTruthy()
  })

  it('asks before deleting finished VODs automatically; Cancel leaves it off', async () => {
    renderPanel()
    const box = await screen.findByRole('checkbox', { name: "Delete a VOD when you're finished with it" })
    await waitFor(() => expect((box as HTMLInputElement).disabled).toBe(false))
    fireEvent.click(box)
    fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Cancel' }))
    expect((box as HTMLInputElement).checked).toBe(false)
    expect(posted('/api/cleanup')).toEqual([])
    fireEvent.click(box)
    fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Turn on' }))
    await waitFor(() => expect(posted('/api/cleanup')).toEqual([{ config: { temp: true, skipped: false, vods: true, vod_days: 7 }, run: true }]))
    const days = await screen.findByLabelText('Not opened for')
    fireEvent.change(days, { target: { value: '14' } })
    await waitFor(() => expect(posted('/api/cleanup').at(-1)).toEqual({ config: { temp: true, skipped: false, vods: true, vod_days: 14 }, run: false }))
  })
})

describe('lastCleanup', () => {
  const last = (over: object) => Cleanup.parse({ config: {}, last: { at: 1_700_000_000, freed: 2e9, removed: [], failed: [], ...over } }).last
  it('says what the last run did', () => {
    expect(lastCleanup(last({ at: 0 }))).toBe('')
    expect(lastCleanup(last({ removed: ['Big stream'] }))).toMatch(/freed 2\.0 GB; removed the VOD of Big stream$/)
    expect(lastCleanup(last({ removed: ['A', 'B'], failed: ['a', 'b', 'c', 'd'] }))).toMatch(/removed the VODs of A, B; couldn't remove a, b, c…$/)
  })
})
