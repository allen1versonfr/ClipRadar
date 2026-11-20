import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { RecBadge } from './RecBadge'
import { StatusBar } from './StatusBar'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })
const text = () => screen.queryByTestId('rec-badge')?.textContent ?? null

describe('the full stream being saved (REC)', () => {
  it('says what is happening to the recording at each step', () => {
    const cases: Array<[string, boolean, string | null]> = [
      ['keeping', true, 'REC · Saving the full stream'], ['keeping', false, 'Will save the full stream'],
      ['saving', false, 'Saving the full stream as one video…'], ['saved', false, 'Full stream saved'],
      ['stopped', true, 'Full stream stopped saving'], ['failed', false, 'Full stream not saved'], ['off', true, null],
    ]
    for (const [vod, active, want] of cases) {
      render(<RecBadge vod={vod} active={active} />)
      expect(text(), `${vod} ${active}`).toBe(want)
      cleanup()
    }
    render(<RecBadge vod="keeping" active compact />)
    expect(text()).toBe('REC · full stream')
  })

  it('the status bar says a stream is being saved, from any screen', () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ available: false }), { status: 200 })))
    const status = { version: '5.5.0', lives: [{ id: 'a', title: 'shroud (live)', status: 'recording', vod: 'keeping' },
      { id: 'b', title: 'other (live)', status: 'recording', vod: 'off' }] } as never
    render(<StatusBar status={status} error={undefined} queue={undefined} />)
    expect(screen.getByTestId('sb-rec').textContent).toBe('Recording shroud (live) · saving the full stream')
    expect(screen.getByText('Recording other (live)')).toBeTruthy()
  })
})
