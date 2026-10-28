import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ConfirmProvider } from '../../components/Confirm'
import { ToastProvider } from '../../components/Toasts'
import { Update } from '../../api/schemas'
import { SettingsView } from './SettingsView'
import { updateLine } from './UpdatesPanel'

const STATUS = { version: '4.16.0', api: 1, ui: 'legacy', ffmpeg: true, ytdlp: false, whisper: true, laughter: true,
  twitchdownloader: true, can_download_tools: true, live: '',
  hotkey: { supported: true, enabled: true, registered: true, keys: 'Ctrl+Alt+C', error: '' } }
const UPDATE = { current: '4.16.0', state: 'idle', available: true, latest: '4.17.0', notes: 'Faster renders.',
  page: 'https://example.test/r', progress: 0, error: '', checked: 1, can_install: true, token: false, token_hint: '', auto: true }

type Call = [string, Record<string, unknown> | undefined]
let calls: Call[]
let twitch: Record<string, unknown>

function fakeServer() {
  return vi.fn(async (path: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : undefined
    calls.push([path, body])
    const answer = (() => {
      if (path === '/api/export') return { prefs: { renders: 1, downloads: 1, lives: 2, priority: 'responsive', ...body } }
      if (path === '/api/perf') return { cpus: 8, mem_free: 4e9, mem_total: 16e9, waiting_for_memory: false, encoder: 'h264_nvenc', speeds: { h264_nvenc: 3.1 } }
      if (path === '/api/twitch') return twitch
      if (path === '/api/twitch/token') return { signed_in: true, login: 'viewer', turbo: true, error: '', window_login: false }
      if (path.startsWith('/api/update')) return UPDATE
      if (path === '/api/storage') return { uninstaller: true }
      if (path === '/api/setup_tools') return { job: 'tools1' }
      if (path === '/api/job/tools1') return { id: 'tools1', kind: 'setup', status: 'done', progress: 1, log: [] }
      if (path === '/api/hotkey') return { ...STATUS.hotkey, ...body }
      return { ok: true }
    })()
    return new Response(JSON.stringify(answer), { status: 200 })
  })
}

beforeEach(() => {
  calls = []
  twitch = { signed_in: false, login: '', turbo: null, error: '', window_login: false }
  localStorage.clear()
  vi.stubGlobal('fetch', fakeServer())
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

const renderSettings = (refreshStatus = async () => {}) => render(
  <ToastProvider><ConfirmProvider><SettingsView status={STATUS as never} refreshStatus={refreshStatus} /></ConfirmProvider></ToastProvider>)
const posted = (path: string) => calls.filter(([p, b]) => p === path && b !== undefined).map(([, b]) => b)
const panel = (id: string) => within(screen.getByTestId(id))

describe('Settings', () => {
  it('has every section, so nothing sends you to the current UI', async () => {
    renderSettings()
    const names = (await screen.findAllByRole('heading', { level: 2 })).map(h => h.textContent)
    for (const n of ['Export format', 'Watermark', 'Campaigns', 'Caption words', 'Hashtags', 'Content check', 'Storage', 'Updates'])
      expect(names).toContain(n)
    expect(screen.queryByText(/More settings are in the current UI/)).toBeNull()
  })

  it('saves how much runs at once, and shows what this PC has', async () => {
    renderSettings()
    const perf = panel('set-perf')
    const renders = await perf.findByLabelText('Clips rendering at once')
    await waitFor(() => expect((renders as HTMLSelectElement).disabled).toBe(false))
    fireEvent.change(renders, { target: { value: '3' } })
    fireEvent.click(perf.getByRole('radio', { name: 'Finish faster' }))
    await waitFor(() => expect(posted('/api/export')).toEqual([{ renders: 3 }, { priority: 'fast' }]))
    expect((await perf.findByTestId('perf-info')).textContent).toContain('8 processor threads · memory 4.0 GB free of 16.0 GB')
    expect(perf.getByTestId('perf-info').textContent).toContain('h264_nvenc 3.1× real time')
  })

  it('asks before updating, and does nothing on Cancel', async () => {
    renderSettings()
    const upd = panel('set-updates')
    expect((await upd.findByTestId('upd-state')).textContent).toBe('Version 4.17.0 is available.')
    fireEvent.click(upd.getByRole('button', { name: 'Update now' }))
    const dialog = await screen.findByRole('alertdialog', { name: 'Update to Clip Radar 4.17.0?' })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(posted('/api/update/install')).toEqual([])
    fireEvent.click(upd.getByRole('button', { name: 'Update now' }))
    fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Update now' }))
    await waitFor(() => expect(posted('/api/update/install')).toEqual([{}]))
  })

  it('signs in to Twitch with a pasted token when there is no app window', async () => {
    renderSettings()
    const tw = panel('set-twitch')
    expect((await tw.findByRole('button', { name: 'Sign in with Twitch' }) as HTMLButtonElement).disabled).toBe(true)
    twitch = { signed_in: true, login: 'viewer', turbo: true, error: '', window_login: false }
    fireEvent.change(tw.getByLabelText('Twitch sign-in token'), { target: { value: ' abc123 ' } })
    fireEvent.click(tw.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(posted('/api/twitch/token')).toEqual([{ token: 'abc123' }]))
    await waitFor(() => expect(tw.getByTestId('tw-state').textContent).toBe('viewer · Turbo'))
    expect(tw.getByRole('button', { name: 'Sign out' })).toBeTruthy()
  })

  it('downloads a missing tool and refreshes what is installed', async () => {
    const refresh = vi.fn(async () => {})
    renderSettings(refresh)
    const tools = panel('set-tools')
    const row = tools.getByText('yt-dlp').closest('tr') as HTMLElement
    expect(row.textContent).toContain('Not installed')
    fireEvent.click(within(row).getByRole('button', { name: 'Download' }))
    await waitFor(() => expect(posted('/api/setup_tools')).toEqual([{ want: ['ytdlp'] }]))
    await waitFor(() => expect(refresh).toHaveBeenCalled())
  })

  it('keeps download quality with the current UI settings', () => {
    renderSettings()
    fireEvent.change(panel('set-tools').getByLabelText('Download quality'), { target: { value: '720' } })
    expect(localStorage.getItem('vc_maxh')).toBe('"720"')
  })

  it('turns the clip hotkey off', async () => {
    const refresh = vi.fn(async () => {})
    renderSettings(refresh)
    expect(panel('set-live').getByTestId('hk-state').textContent).toBe('Ready. Press it while live clipping is running.')
    fireEvent.click(panel('set-live').getByRole('checkbox'))
    await waitFor(() => expect(posted('/api/hotkey')).toEqual([{ enabled: false }]))
    await waitFor(() => expect(refresh).toHaveBeenCalled())
  })

  it('puts the focus on Cancel for a destructive action, and Escape cancels it', async () => {
    renderSettings()
    fireEvent.click(await panel('set-about').findByRole('button', { name: 'Uninstall…' }))
    const dialog = await screen.findByRole('alertdialog', { name: 'Uninstall Clip Radar?' })
    expect(document.activeElement?.textContent).toBe('Cancel')
    fireEvent.keyDown(dialog, { key: 'Escape' })
    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(posted('/api/uninstall')).toEqual([])
  })
})

describe('updateLine', () => {
  const u = (over: object) => Update.parse({ ...UPDATE, available: false, ...over })
  it('says what the updater is doing', () => {
    expect(updateLine(u({ state: 'downloading', available: true, progress: 0.42 })).text).toBe('Downloading version 4.17.0… 42%')
    expect(updateLine(u({ checked: 1000 }), 1000 + 7200)).toEqual({ text: "You're up to date. Checked 2 h ago.", cls: 'ok' })
    expect(updateLine(u({ checked: 0 })).text).toBe('Not checked yet.')
    expect(updateLine(u({ state: 'error', error: 'No internet.' }))).toEqual({ text: 'No internet.', cls: 'err' })
    expect(updateLine(u({ state: 'nonsense' })).text).toContain('up to date')
  })
})
