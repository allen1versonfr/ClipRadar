import type { Clip } from '../../api/schemas'
import { checkStatus, type CheckStatus } from './model'

// Review's content check box (the original page's renderCheck, flagRow, proposalBox, applyCheck,
// flaggedList and fixAudio), without the screen.

export interface Flag {
  fp: string; label: string; text: string; status: string; source?: string; start?: number; end?: number
  severity?: number; fixes?: string[]; fixed?: string | null; fix_id?: string | null; cat?: string
}
export interface Fix { id: string; fp: string; kind: string; span?: [number, number]; flag?: string }
export interface LogLine { at: number; text: string }

export const FIX_LABEL: Record<string, string> = { mute: 'Mute it', bleep: 'Bleep it', cut_end: 'End the clip before it',
  cut_start: 'Start the clip after it', mask_title: 'Hide it in the title', mask_post: 'Hide it in the post caption' }
export const FIX_EXPLAIN: Record<string, string> = {
  mute: 'The sound goes silent for these words when the clip renders. Captions show them masked.',
  bleep: 'A bleep plays over these words when the clip renders. Captions show them masked.',
  cut_end: "The clip ends just before these words. Your video itself isn't changed.",
  cut_start: "The clip starts just after these words. Your video itself isn't changed.",
  mask_title: 'The flagged words in the title are replaced with stars.',
  mask_post: 'The flagged words in the post caption are replaced with stars.' }
export const FLAG_STATE: Record<string, string> = { open: '', accepted: 'Left as is', dismissed: 'Dismissed' }

export const flagsOf = (c: Clip) => (c.safety?.flags ?? []) as unknown as Flag[]
export const fixesOf = (c: Clip) => ((c.check?.fixes ?? []) as Fix[])
export const logOf = (c: Clip) => (((c.check as { log?: LogLine[] } | undefined)?.log) ?? [])
export const isStale = (c: Clip) => !!(c.check as { stale?: boolean } | undefined)?.stale

export const isFlagged = (c: Clip) => !!(c.check?.excluded || fixesOf(c).length || flagsOf(c).length)
const ORDER: Partial<Record<CheckStatus, number>> = { needs_review: 0, excluded: 1, reviewed: 2, corrected: 3, dismissed: 4 }
const LEVEL: Record<string, number> = { high: 3, medium: 2, low: 1 }
/** The Flagged tab: flagged clips, those needing review first, then by how serious. */
export function flaggedList(clips: Clip[], filter: string, safetyOn: boolean): Clip[] {
  return clips.filter(c => isFlagged(c) && (filter === 'all' || checkStatus(c, safetyOn) === filter))
    .sort((a, b) => (ORDER[checkStatus(a, safetyOn)] ?? 9) - (ORDER[checkStatus(b, safetyOn)] ?? 9)
      || (LEVEL[b.safety?.level ?? ''] ?? 0) - (LEVEL[a.safety?.level ?? ''] ?? 0))
}

/** The stretches muted or bleeped in this clip ([start, end, kind], video time), plus one being tried out. */
export function fixSpans(c: Clip, trying?: { span?: [number, number] | undefined; kind: string } | null): Array<[number, number, string]> {
  const s = fixesOf(c).filter(f => f.span).map(f => [f.span![0], f.span![1], f.kind] as [number, number, string])
  if (trying?.span) s.push([trying.span[0], trying.span[1], trying.kind])
  return s
}

/** The server's answer to a content check action, on the clip as you have it. */
export function applied(c: Clip, v: Record<string, unknown>): Partial<Clip> {
  const p: Partial<Clip> = { safety: v.safety as Clip['safety'], check: v.check as Clip['check'] }
  if (typeof v.start === 'number' && typeof v.end === 'number' && (v.start !== c.start || v.end !== c.end)) Object.assign(p, { start: v.start, end: v.end, userTrim: !!v.userTrim })
  if (typeof v.title === 'string' && v.title !== c.title) Object.assign(p, { title: v.title, userTitle: !!v.userTitle })
  if (v.userPost && v.postText !== c.postText) Object.assign(p, { userPost: true, postText: String(v.postText ?? '') })
  if (typeof v.state === 'string' && v.state !== c.state) p.state = v.state as Clip['state']
  return p
}

/** What a fix's preview plays: from, to, and the title shown instead (Hide it in the title). */
export function previewPlan(c: Clip, kind: string, view: Record<string, unknown>, span?: [number, number]) {
  const vs = typeof view.start === 'number' ? view.start : c.start, ve = typeof view.end === 'number' ? view.end : c.end
  if ((kind === 'mute' || kind === 'bleep') && span) return { from: Math.max(c.start, span[0] - 2), to: Math.min(c.end, span[1] + 2) }
  if (kind === 'cut_end') return { from: Math.max(vs, ve - 4), to: ve }
  if (kind === 'cut_start') return { from: vs, to: Math.min(ve, vs + 4) }
  return { from: c.start, to: Math.min(c.end, c.start + 4), title: kind === 'mask_title' ? String(view.title ?? '') : undefined }
}
