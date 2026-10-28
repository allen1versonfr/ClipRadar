import type { Clip, QueueItem, Safety } from '../../api/schemas'
import type { RenderOpts } from '../../prefs'

// Review's rules, as in the original page (src/ui/app.js: visible, checkStatus, checkGate, postFor,
// queueKeep, clipSnapshot), kept apart from the screen so they're tested on their own.

export type Filter = 'todo' | 'skipped' | 'all' | 'gems'
/** A clip as the server sent it, ready for the screen (prepClip): caption fixes you made replace the words heard. */
export function prep(c: Clip): Clip {
  return c.wordsEdited && c.words_edit ? { ...c, words: c.words_edit, words_edit: undefined } : c
}
export const isGem = (c: Clip) => c.kind === 'gem'
export const keyOf = (c: Pick<Clip, 'pid' | 'id'>) => `${c.pid}:${c.id}`

export function visible(clips: Clip[], filter: Filter): Clip[] {
  return clips.filter(c => (filter === 'gems' ? isGem(c) : !isGem(c) && (filter === 'all' || c.state === filter)))
}

export type CheckStatus = 'excluded' | 'off' | 'unchecked' | 'clean' | 'needs_review' | 'reviewed' | 'corrected' | 'dismissed'
export function checkStatus(c: Clip, safetyOn: boolean): CheckStatus {
  if (c.check?.excluded) return 'excluded'
  if (!c.safety) return safetyOn ? 'unchecked' : 'off'
  if (c.safety.enabled === false) return 'off'
  return (c.safety.status as CheckStatus | undefined) ?? (c.safety.flags.length ? 'needs_review' : 'clean')
}
export const CHECK_LABEL: Record<CheckStatus, string> = {
  off: 'Content check is off', unchecked: 'Not checked', clean: 'Nothing flagged', needs_review: 'Needs review',
  reviewed: 'Reviewed', corrected: 'Corrected', dismissed: 'Dismissed', excluded: 'Excluded from export',
}

/** Before rendering: 'ok', 'ask' (render anyway after a warning), or why it can't render yet. */
export function gate(c: Clip, safety: Pick<Safety, 'enabled' | 'export_flagged'> | undefined): 'ok' | 'ask' | 'excluded' | 'blocked' {
  const st = checkStatus(c, safety?.enabled !== false)
  if (st === 'excluded') return 'excluded'
  if (st !== 'needs_review' || safety?.enabled === false) return 'ok'
  const mode = safety?.export_flagged ?? 'ask'
  return mode === 'allow' ? 'ok' : mode === 'block' ? 'blocked' : 'ask'
}
export const openFlags = (c: Clip) => (c.safety?.flags ?? []).filter(f => f.status === 'open')

const DEFAULT_TAGS = '#twitch #twitchclips'
/** The post caption: yours if you wrote one, else the description and (with the Hashtags switch on) the tags. */
export function postFor(c: Clip, hashtagsOn: boolean): string {
  if (c.userPost) return c.postText
  const desc = c.description || `${c.title.trim() || 'Wait for it...'} ${c.emoji}`.trim()
  const tags = c.hashtags ? c.hashtags.join(' ') : (c.post.split('\n\n')[1] ?? DEFAULT_TAGS)
  return hashtagsOn && tags ? `${desc}\n\n${tags}` : desc
}

export const queueFor = (c: Clip, items: QueueItem[]) => items.find(q => q.pid === c.pid && q.clip_id === c.id)

/** What /api/queue/add is sent to keep a clip: rendered with your render style, as you left it. */
export function keepBody(c: Clip, post: string, opts: RenderOpts, sound = true) {
  const clip: Record<string, unknown> = { id: c.id, rank: c.id, start: c.start, end: c.end, title: c.title.trim(), post, thumb: c.thumb, gain: c.gain }
  if (c.wordsEdited) clip.words = c.words.filter(w => w.start >= c.start - 0.2 && w.start < c.end)    // your caption fixes
  return { pid: c.pid, clip, opts, sound }
}

/** What /api/clip_state is sent: everything you did to a clip, saved with the VOD. */
export function snapshot(c: Clip) {
  return { pid: c.pid, id: c.id, state: c.state, start: c.start, end: c.end, title: c.title, userTrim: !!c.userTrim,
    userTitle: !!c.userTitle, userPost: !!c.userPost, postText: c.postText, wordsEdited: !!c.wordsEdited, gain: c.gain }
}

/** A clip from the server, merged into the one on screen: titles and checks arrive in the background,
 *  and its cut moves to sentence boundaries once it's been listened to, unless you trimmed it. */
export function merge(c: Clip, s: Clip): Clip {
  const out: Clip = { ...c, listened: s.listened, words: c.wordsEdited ? c.words : s.words, titles: s.titles, vibe: s.vibe, emoji: s.emoji, reason: s.reason, post: s.post,
    held: s.held, description: s.description ?? c.description, hashtags: s.hashtags ?? c.hashtags,
    safety: s.safety ?? c.safety, check: s.check ?? c.check }
  if (!c.userTitle && s.title) out.title = s.title
  if (!c.userTrim && c.state !== 'kept') { out.start = s.start; out.end = s.end }
  return out
}

/** The next clip to look at after this one was kept or skipped: the next one to review of the same kind. */
export function nextTodo(clips: Clip[], from: Clip): Clip | undefined {
  const i = clips.findIndex(c => keyOf(c) === keyOf(from))
  const ok = (c: Clip) => c.state === 'todo' && isGem(c) === isGem(from) && keyOf(c) !== keyOf(from)
  return clips.slice(i + 1).find(ok) ?? clips.find(ok)
}

export function fmt(s: number): string {
  const t = Math.max(0, Math.round(s || 0)), h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), x = t % 60
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(x).padStart(2, '0')}` : `${m}:${String(x).padStart(2, '0')}`
}
export const qClass = (q: number) => (q >= 75 ? 3 : q >= 50 ? 2 : 1)
