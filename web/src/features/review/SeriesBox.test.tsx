import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Clip, SeriesView } from '../../api/schemas'
import { SeriesBox } from './SeriesBox'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

const VIEW = SeriesView.parse({ settings: {}, list: [{ id: 's1', name: 'Jason × Emily', count: 5, next: 6 }] })
const clip = (extra: object) => Clip.parse({ id: 3, pid: 'p1', start: 0, end: 30, ...extra })

function server(calls: Array<unknown>, answer: object) {
  return vi.fn(async (_p: string, init?: RequestInit) => {
    calls.push(JSON.parse(String(init?.body)))
    return new Response(JSON.stringify({ ok: true, clip: answer, series: VIEW }), { status: 200 })
  })
}

describe('Review › series', () => {
  it('is one small menu when there is nothing to suggest', () => {
    render(<SeriesBox clip={clip({})} kept={false} view={VIEW} onChanged={() => {}} onError={() => {}} />)
    expect(screen.queryByTestId('rv-series-hint')).toBeNull()
    expect(screen.getByRole('combobox', { name: 'Series' })).toBeTruthy()
  })

  it('adds a clip to the suggested series as the next part in one click', async () => {
    const calls: unknown[] = [], changed: unknown[] = []
    vi.stubGlobal('fetch', server(calls, { series: { id: 's1', name: 'Jason × Emily', part: 6 } }))
    render(<SeriesBox clip={clip({ series_hint: { id: 's1', name: 'Jason × Emily', part: 6, why: 'Emily is in this clip' } })}
                      kept={false} view={VIEW} onChanged={p => changed.push(p)} onError={() => {}} />)
    expect(screen.getByTestId('rv-series-hint').textContent).toMatch(/Part of Jason × Emily\?.*Emily is in this clip/)
    fireEvent.click(screen.getByRole('button', { name: 'Add as Pt. 6' }))
    await waitFor(() => expect(changed).toHaveLength(1))
    expect(calls[0]).toMatchObject({ pid: 'p1', clip: 3, action: 'add', id: 's1' })
    expect(changed[0]).toMatchObject({ series: { part: 6 } })
  })

  it('starts a new series with the suggested name, which can be changed first', async () => {
    const calls: unknown[] = []
    vi.stubGlobal('fetch', server(calls, { series: { id: 's2', name: 'Emily arc', part: 1 } }))
    render(<SeriesBox clip={clip({ series_hint: { name: 'Jason × Emily', new: true, why: 'Emily is in 2 of Jason’s clips' } })}
                      kept={false} view={VIEW} onChanged={() => {}} onError={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Start it' }))
    const box = screen.getByRole('textbox', { name: 'New series name' }) as HTMLInputElement
    expect(box.value).toBe('Jason × Emily')
    fireEvent.change(box, { target: { value: 'Emily arc' } })
    fireEvent.click(screen.getByRole('button', { name: 'Start series' }))
    await waitFor(() => expect(calls[0]).toMatchObject({ action: 'new', name: 'Emily arc' }))
  })

  it("Not now hides the suggestion; a clip in a series says so and can be taken out", () => {
    const { rerender } = render(<SeriesBox clip={clip({ series_hint: { id: 's1', name: 'Jason × Emily', part: 6 } })}
                                           kept={false} view={VIEW} onChanged={() => {}} onError={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Not now' }))
    expect(screen.queryByTestId('rv-series-hint')).toBeNull()
    rerender(<SeriesBox clip={clip({ series: { id: 's1', name: 'Jason × Emily', part: 6 } })} kept={false} view={VIEW} onChanged={() => {}} onError={() => {}} />)
    expect(screen.getByTestId('rv-series').textContent).toMatch(/Part of Jason × Emily · Pt\. 6/)
    expect(screen.getByRole('button', { name: 'Take out' })).toBeTruthy()
  })
})
