import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { usePoll } from './usePoll'

afterEach(() => vi.useRealTimers())

describe('usePoll', () => {
  it('loads at once, then on every tick', async () => {
    vi.useFakeTimers()
    let n = 0
    const { result } = renderHook(() => usePoll(async () => ++n, 1000))
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(result.current.data).toBe(1)
    await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
    expect(result.current.data).toBe(3)
  })

  it('skips ticks while the window is hidden', async () => {
    vi.useFakeTimers()
    let n = 0
    const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)
    renderHook(() => usePoll(async () => ++n, 1000))
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(n).toBe(1)                     // only the first load
    hidden.mockRestore()
  })

  it('never lets an older answer overwrite a newer one', async () => {
    const answers: Array<(v: string) => void> = []
    const { result } = renderHook(() => usePoll(() => new Promise<string>(r => answers.push(r)), 60_000))
    await waitFor(() => expect(answers.length).toBe(1))
    let second: Promise<void> | undefined
    act(() => { second = result.current.refresh() })
    await act(async () => {
      answers[1]?.('new')
      await second
      answers[0]?.('old')
    })
    expect(result.current.data).toBe('new')
  })

  it('reports a failed load and clears it on the next good one', async () => {
    let fail = true
    const { result } = renderHook(() => usePoll(async () => {
      if (fail) throw new Error('Lost connection')
      return 1
    }, 60_000))
    await waitFor(() => expect(result.current.error).toBe('Lost connection'))
    fail = false
    await act(async () => { await result.current.refresh() })
    expect(result.current.error).toBe('')
    expect(result.current.data).toBe(1)
  })
})
