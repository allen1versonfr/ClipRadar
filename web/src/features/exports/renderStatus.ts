import type { Attempt, QueueItem, Stage } from '../../api/schemas'

/** 75 -> "1:15", 3725 -> "1:02:05" (same as fmt() in the original page). */
export function fmtTime(seconds: number): string {
  const s = Math.max(0, Math.round(seconds || 0))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const x = s % 60
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(x).padStart(2, '0')}` : `${m}:${String(x).padStart(2, '0')}`
}

/** Time left, as the original page says it. Null means there's nothing to go on yet. */
export function fmtLeft(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return 'working out time left'
  if (seconds < 5) return 'almost done'
  if (seconds < 60) return `about ${Math.max(5, Math.round(seconds / 5) * 5)} s left`
  return `about ${Math.round(seconds / 60)} min left`
}

/** Where the item is. Items saved before stages existed only have a status. */
export function stageOf(q: QueueItem): Stage {
  if (q.stage) return q.stage
  return q.status === 'rendering' ? 'rendering' : q.status === 'done' ? 'done' : q.status === 'error' ? 'error' : 'queued'
}

/** A percentage is FFmpeg's own progress, so there is one only while FFmpeg is encoding. */
export function isMeasured(q: QueueItem): boolean {
  return q.status === 'rendering' && stageOf(q) === 'rendering'
}

export interface StatusText {
  label: string
  /** 0..1 while measured, null while a stage can't be measured (shown as a moving bar) */
  fraction: number | null
}

/** What a row says about its render, given the current time in seconds. */
export function statusText(q: QueueItem, now: number): StatusText {
  if (q.status === 'done') return { label: 'Ready' + (q.srt ? ' + .srt' : ''), fraction: 1 }
  if (q.status === 'error') return { label: 'Failed', fraction: null }
  if (q.status === 'queued') return { label: q.step && q.step !== 'Waiting' ? q.step : 'Waiting', fraction: null }
  if (isMeasured(q)) {
    const pct = Math.round(q.progress * 100)
    return { label: `${q.step || 'Rendering'} ${pct}% · ${fmtLeft(q.eta)}`, fraction: q.progress }
  }
  const since = q.stage_at ? now - q.stage_at : 0
  return { label: `${q.step || 'Preparing'} · ${fmtTime(since)}`, fraction: null }
}

/** One line per attempt, so "Retry keeps failing" shows whether it failed the same way each time. */
export function attemptLine(a: Attempt): string {
  const parts = [`Attempt ${a.n}: ${a.outcome}${a.kind ? ` (${a.kind})` : ''}`]
  if (a.stage && a.outcome !== 'done') parts.push(`during ${a.stage}`)
  if (a.encoder) parts.push(a.encoder)
  if (a.exit !== undefined) parts.push(`exit ${a.exit}`)
  if (a.fallback) parts.push(a.fallback)
  return parts.join(', ')
}

/** The summary above the list: "3 ready, 1 rendering (about 2 min left), 1 failed". */
export function summary(items: QueueItem[], eta: number | null): string {
  if (!items.length) return ''
  const done = items.filter(q => q.status === 'done').length
  const active = items.filter(q => q.status === 'queued' || q.status === 'rendering').length
  const failed = items.filter(q => q.status === 'error').length
  return `${done} ready` + (active ? `, ${active} rendering (${fmtLeft(eta)})` : '') + (failed ? `, ${failed} failed` : '')
}
