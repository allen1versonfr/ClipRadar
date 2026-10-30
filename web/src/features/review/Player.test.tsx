import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Player } from './Player'

// Each live moment is its own video, so one player shows clips from several. A video the window can't
// play gets a copy that it can; that mustn't carry over to the next clip's video.
let asked: string[]
let finish: Array<() => void>

beforeEach(() => {
  asked = []
  finish = []
  vi.stubGlobal('fetch', vi.fn(async (path: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) as Record<string, any> : undefined
    let answer: unknown = { ok: true }
    if (path === '/api/preview') {
      asked.push(body!.pid)
      answer = { ready: false, job: 'j_' + body!.pid }
    }
    if (path.startsWith('/api/job/j_')) {
      // the copy is ready once the test says so
      await new Promise<void>(res => finish.push(res))
      answer = { id: path.slice(9), kind: 'preview', status: 'done', progress: 1 }
    }
    return new Response(JSON.stringify(answer), { status: 200 })
  }))
  HTMLMediaElement.prototype.play = vi.fn(async () => {})
  HTMLMediaElement.prototype.pause = vi.fn()
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const video = () => screen.getByTestId('rv-video') as HTMLVideoElement
const ready = async () => { await waitFor(() => expect(finish.length).toBeGreaterThan(0)); await act(async () => { finish.shift()!() }) }

describe('Player', () => {
  it("a copy made for one video isn't used for the next clip's video, which gets its own", async () => {
    const r = render(<Player src="/vod/m1" pid="m1" clipKey="m1:1" start={0} end={10} />)
    fireEvent.error(video())                                  // the window can't play m1
    await ready()
    await waitFor(() => expect(video().getAttribute('src')).toBe('/vod/m1?preview=1'))

    r.rerender(<Player src="/vod/m2" pid="m2" clipKey="m2:1" start={0} end={10} />)
    expect(video().getAttribute('src')).toBe('/vod/m2')       // m2's own video, not a copy it doesn't have
    fireEvent.error(video())                                  // and it can't play either: m2 gets a copy too
    await waitFor(() => expect(asked).toEqual(['m1', 'm2']))
    expect(screen.getByText(/needs a copy the window can play/)).toBeTruthy()
    await ready()
    await waitFor(() => expect(video().getAttribute('src')).toBe('/vod/m2?preview=1'))
  })

  it('a copy that finishes while you are on another clip is used when you come back', async () => {
    const r = render(<Player src="/vod/a" pid="a" clipKey="a:1" start={0} end={10} />)
    fireEvent.error(video())
    await waitFor(() => expect(asked).toEqual(['a']))
    r.rerender(<Player src="/vod/b" pid="b" clipKey="b:1" start={0} end={10} />)
    expect(screen.queryByText(/needs a copy/)).toBeNull()     // b plays as it is
    await ready()                                             // a's copy finishes meanwhile
    r.rerender(<Player src="/vod/a" pid="a" clipKey="a:2" start={0} end={10} />)
    await waitFor(() => expect(video().getAttribute('src')).toBe('/vod/a?preview=1'))
    expect(asked).toEqual(['a'])                              // made once
  })
})

describe('Player, a decode error after playing', () => {
  const decodeError = (el: HTMLVideoElement) => {
    Object.defineProperty(el, 'error', { configurable: true, value: { code: 3 } })
    Object.defineProperty(el, 'videoWidth', { configurable: true, value: 1920 })
  }
  it('loads the original again instead of making a copy, then makes one if it keeps failing', async () => {
    const load = vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {})
    render(<Player src="/vod/g" pid="g" clipKey="g:1" start={0} end={10} />)
    const el = video()
    decodeError(el)
    fireEvent.loadedData(el)                                  // it played
    fireEvent.error(el)                                       // then the decoder failed (GPU busy)
    expect(load).toHaveBeenCalledTimes(1)
    expect(asked).toEqual([])                                 // no copy for that
    fireEvent.error(el)
    expect(load).toHaveBeenCalledTimes(2)
    fireEvent.error(el)                                       // a third time: it really can't be played here
    await waitFor(() => expect(asked).toEqual(['g']))
    load.mockRestore()
  })
  it("a video that never played gets the copy straight away", async () => {
    const load = vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {})
    render(<Player src="/vod/h" pid="h" clipKey="h:1" start={0} end={10} />)
    Object.defineProperty(video(), 'error', { configurable: true, value: { code: 4 } })
    fireEvent.error(video())
    await waitFor(() => expect(asked).toEqual(['h']))
    expect(load).not.toHaveBeenCalled()
    load.mockRestore()
  })
})
