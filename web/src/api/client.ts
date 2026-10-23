import type { z } from 'zod'

// The app's server answers every /api/ call with JSON. Errors are {"error": "..."} with a 4xx/5xx
// status, written for the user, so they're shown as they are. Ported from api() in src/ui/app.js.

export class ApiError extends Error {
  readonly status: number
  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export const LOST_CONNECTION = 'Lost connection to Clip Radar. Restart it if this keeps happening.'
export const UNEXPECTED_ANSWER = "Clip Radar sent an answer this screen doesn't understand. Update Clip Radar, or use the current UI."

async function call(path: string, body?: unknown): Promise<unknown> {
  let r: Response
  try {
    r = await fetch(path, body === undefined ? {} : {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  } catch {
    throw new ApiError(LOST_CONNECTION, 0)
  }
  const j: unknown = await r.json().catch(() => ({}))
  if (!r.ok) {
    const msg = typeof j === 'object' && j !== null && 'error' in j && typeof j.error === 'string' ? j.error : ''
    throw new ApiError(msg || `Request failed (${r.status})`, r.status)
  }
  return j
}

/** GET or POST one API path, and check the answer has the shape this screen relies on. */
export async function api<S extends z.ZodType>(schema: S, path: string, body?: unknown): Promise<z.infer<S>> {
  const raw = await call(path, body)
  const parsed = schema.safeParse(raw)
  if (!parsed.success) {
    console.error(`Unexpected answer from ${path}`, parsed.error.issues)
    throw new ApiError(UNEXPECTED_ANSWER, 200)
  }
  return parsed.data
}
