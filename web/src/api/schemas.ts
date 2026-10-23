import { z } from 'zod'

// What this UI reads from the server, checked at runtime. Objects are "loose": the server adds fields
// over time and older screens must keep working, so only the fields used here are required.
// The server's side of these shapes: queue_public() and the render lifecycle in src/server.py.

export const STAGES = ['queued', 'validating', 'preparing', 'rendering', 'verifying', 'done', 'error', 'cancelled'] as const
export type Stage = (typeof STAGES)[number]

export const Attempt = z.looseObject({
  n: z.number(),
  started: z.number(),
  ended: z.number().optional(),
  outcome: z.enum(['running', 'done', 'error', 'cancelled', 'interrupted']),
  stage: z.string().optional(),
  kind: z.string().optional(),
  error: z.string().optional(),
  encoder: z.string().optional(),
  exit: z.number().optional(),
  fallback: z.string().optional(),
})
export type Attempt = z.infer<typeof Attempt>

export const QueueItem = z.looseObject({
  id: z.string(),
  status: z.enum(['queued', 'rendering', 'done', 'error', 'cancelled']),
  // items saved before 4.13 have no stage: the status stands in for it
  stage: z.enum(STAGES).optional(),
  stage_at: z.number().optional(),
  step: z.string().default(''),
  progress: z.number().default(0),
  eta: z.number().nullable().optional(),
  title: z.string().default(''),
  rank: z.number().optional(),
  source_title: z.string().default(''),
  duration: z.number().default(0),
  thumb: z.string().default(''),
  name: z.string().optional(),
  folder: z.string().optional(),
  url: z.string().optional(),
  error: z.string().default(''),
  detail: z.string().default(''),
  error_kind: z.string().optional(),
  retryable: z.boolean().optional(),
  warning: z.string().optional(),
  captions_skipped: z.boolean().optional(),
  srt: z.boolean().optional(),
  history: z.array(Attempt).default([]),
})
export type QueueItem = z.infer<typeof QueueItem>

export const Queue = z.looseObject({
  items: z.array(QueueItem),
  eta: z.number().nullable(),
  workers: z.number().optional(),
})
export type Queue = z.infer<typeof Queue>

export const Status = z.looseObject({
  version: z.string(),
  api: z.number(),
  ui: z.string(),
  ffmpeg: z.boolean().optional(),
})
export type Status = z.infer<typeof Status>

export const Ok = z.looseObject({ ok: z.boolean().optional(), retried: z.number().optional() })
