import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Campaign } from '../../api/schemas'
import { ConfirmProvider } from '../../components/Confirm'
import { ToastProvider } from '../../components/Toasts'
import { CampaignsPanel, campaignOf, formOf, HOOK_RE } from './CampaignsPanel'

const HOOK = 'https://discord.com/api/webhooks/123/abc-DEF'
let view: { list: Array<Record<string, any>>; active: string }
type Call = [string, Record<string, any> | undefined]
let calls: Call[]

beforeEach(() => {
  calls = []
  view = { active: '', list: [{ id: 'k1', name: 'Kai clipping', org: 'Clip Discord', caption: '@brand', tags: ['#ad'], min_len: 10, max_len: 60,
    watermark: { use: false, marks: [] }, discord: { webhook: HOOK, auto: true, file: true, message: '**{title}**' } }] }
  vi.stubGlobal('fetch', vi.fn(async (path: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) as Record<string, any> : undefined
    calls.push([path, body])
    if (path === '/api/campaigns/save') {
      const c = { ...body!.campaign, id: body!.campaign.id ?? 'n2', tags: String(body!.campaign.tags).split(/\s+/).filter(Boolean) }
      view.list = view.list.some(x => x.id === c.id) ? view.list.map(x => (x.id === c.id ? c : x)) : [...view.list, c]
      if (body!.activate) view.active = c.id
    }
    if (path === '/api/campaigns/active') view.active = body!.id
    if (path === '/api/campaigns/delete') view.list = view.list.filter(x => x.id !== body!.id)
    if (path === '/api/export') return new Response(JSON.stringify({ prefs: { watermark: { on: true, marks: [{ kind: 'text', text: '@me' }] } }, settings: {} }))
    return new Response(JSON.stringify({ ...view, tokens: ['title', 'post_url'], default_message: '**{title}**\n{post_url}' }), { status: 200 })
  }))
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const renderPanel = (onChange = () => {}) => render(<ToastProvider><ConfirmProvider><CampaignsPanel onChange={onChange} /></ConfirmProvider></ToastProvider>)
const posted = (path: string) => calls.filter(([p, b]) => p === path && b).map(([, b]) => b!)

describe('Campaigns', () => {
  it('makes a campaign, which becomes the active one when none is', async () => {
    const onChange = vi.fn()
    renderPanel(onChange)
    fireEvent.click(await screen.findByRole('button', { name: '+ New' }))
    fireEvent.change(screen.getByPlaceholderText('Kai Cenat clipping'), { target: { value: 'Mafia' } })
    fireEvent.change(screen.getByPlaceholderText('#ad #KaiCenatClips'), { target: { value: '#mafia #clips' } })
    fireEvent.change(screen.getAllByPlaceholderText('Any')[1]!, { target: { value: '45' } })
    fireEvent.click(screen.getByRole('radio', { name: 'At the start' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(posted('/api/campaigns/save')).toHaveLength(1))
    const b = posted('/api/campaigns/save')[0]!
    expect(b.activate).toBe(true)
    expect(b.campaign).toMatchObject({ name: 'Mafia', tags: '#mafia #clips', max_len: 45, min_len: 0, caption_where: 'start' })
    expect(await screen.findByRole('heading', { name: 'Mafia' })).toBeTruthy()       // the saved one stays open
    expect(onChange).toHaveBeenCalled()
  })

  it('a link that is not a Discord webhook is not saved', async () => {
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: /Kai clipping/ }))
    fireEvent.change(screen.getByPlaceholderText(/discord.com\/api\/webhooks/), { target: { value: 'https://example.com/hook' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await new Promise(r => setTimeout(r, 50))
    expect(posted('/api/campaigns/save')).toEqual([])
  })

  it('copies your watermark into the campaign, kept on Save', async () => {
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: /Kai clipping/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Copy my current watermark' }))
    await waitFor(() => expect(screen.getByTestId('cp-wm-info').textContent).toBe('1 layer (save to keep)'))
    expect((screen.getByRole('checkbox', { name: /Its own watermark/ }) as HTMLInputElement).checked).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(posted('/api/campaigns/save')[0]!.campaign.watermark).toMatchObject({ use: true, marks: [{ text: '@me' }] }))
  })

  it('chooses the active campaign, and asks before deleting one', async () => {
    renderPanel()
    fireEvent.change(await screen.findByRole('combobox', { name: 'Active campaign' }), { target: { value: 'k1' } })
    await waitFor(() => expect(posted('/api/campaigns/active')).toEqual([{ id: 'k1' }]))
    expect(await screen.findByText('Active')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Kai clipping/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }))
    fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Cancel' }))
    expect(posted('/api/campaigns/delete')).toEqual([])
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }))
    fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(posted('/api/campaigns/delete')).toEqual([{ id: 'k1' }]))
  })
})

describe('the form', () => {
  it('round-trips a campaign, numbers and tags as typed', () => {
    const c = Campaign.parse(view.list[0])
    const f = formOf(c, 'x')
    expect(f).toMatchObject({ tags: '#ad', min: '10', max: '60', hook: HOOK, message: '**{title}**' })
    expect(campaignOf(c, { ...f, min: '', tags: '#a #b' }, undefined)).toMatchObject({ id: 'k1', min_len: 0, max_len: 60, tags: '#a #b' })
    expect(formOf(undefined, 'default').message).toBe('default')
  })
  it('knows a webhook link', () => {
    expect(HOOK_RE.test(HOOK)).toBe(true)
    expect(HOOK_RE.test('https://ptb.discord.com/api/v10/webhooks/1/x')).toBe(true)
    expect(HOOK_RE.test('https://discord.com/channels/1/2')).toBe(false)
  })
})
