import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError, LOST_CONNECTION, UNEXPECTED_ANSWER } from './client'
import { Queue } from './schemas'

const answer = (status: number, body: unknown) =>
  vi.fn(async () => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }))

afterEach(() => vi.unstubAllGlobals())

describe('api()', () => {
  it('returns the checked answer, keeping fields this UI does not know yet', async () => {
    vi.stubGlobal('fetch', answer(200, { items: [{ id: 'x', status: 'done', future_field: 1 }], eta: null, workers: 1 }))
    const q = await api(Queue, '/api/queue')
    expect(q.items[0]?.id).toBe('x')
    expect(q.items[0]?.history).toEqual([])
    expect((q.items[0] as Record<string, unknown>).future_field).toBe(1)
  })
  it("shows the server's own error message", async () => {
    vi.stubGlobal('fetch', answer(404, { error: 'Nothing to retry.' }))
    await expect(api(Queue, '/api/queue/retry', { id: 'x' })).rejects.toEqual(new ApiError('Nothing to retry.', 404))
  })
  it('says the connection was lost when the server is gone', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch') }))
    await expect(api(Queue, '/api/queue')).rejects.toThrow(LOST_CONNECTION)
  })
  it('refuses an answer of the wrong shape instead of drawing nonsense', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.stubGlobal('fetch', answer(200, { items: [{ id: 'x', status: 'exploded' }], eta: null }))
    await expect(api(Queue, '/api/queue')).rejects.toThrow(UNEXPECTED_ANSWER)
  })
  it('POSTs JSON', async () => {
    const f = answer(200, { items: [], eta: 0 })
    vi.stubGlobal('fetch', f)
    await api(Queue, '/api/queue', { a: 1 })
    expect(f).toHaveBeenCalledWith('/api/queue', expect.objectContaining({
      method: 'POST', body: '{"a":1}', headers: { 'Content-Type': 'application/json' },
    }))
  })
})
