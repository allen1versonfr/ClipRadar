import type { Clip, QueueItem, Safety, YtEdit } from '../../api/schemas'
import type { RenderOpts } from '../../prefs'

// Review's rules, as in the original page (src/ui/app.js: visible, checkStatus, checkGate, postFor,
// queueKeep, clipSnapshot), kept apart from the screen so they're tested on their own.

export type Filter = 'todo' | 'skipped' | 'all' | 'gems' | 'flagged'
/** A clip as the server sent it, ready for the screen (prepClip): caption fixes you made replace the words heard. */
export function prep(c: Clip): Clip {
  const yt = c.yt?.wordsEdited && c.yt.words_edit ? { ...c.yt, words: c.yt.words_edit, words_edit: undefined } : c.yt
  const out = yt === c.yt ? c : { ...c, yt }
  return out.wordsEdited && out.words_edit ? { ...out, wordsOrig: out.words, words: out.words_edit, words_edit: undefined } : out
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

/** The clip's render in the queue: its TikTok edit (5.1: a moment kept with YouTube too also has a YouTube edit) */
export const queueFor = (c: Clip, items: QueueItem[]) =>
  items.find(q => q.pid === c.pid && q.clip_id === c.id && q.platform !== 'youtube') ?? items.find(q => q.pid === c.pid && q.clip_id === c.id)

/** What /api/queue/add is sent to keep a clip: rendered with your render style, as you left it. */
function clipBody(c: Clip, post: string) {
  const clip: Record<string, unknown> = { id: c.id, rank: c.id, start: c.start, end: c.end, title: c.title.trim(), post, thumb: c.thumb, gain: c.gain }
  if (c.wordsEdited) clip.words = c.words.filter(w => w.start >= c.start - 0.2 && w.start < c.end)    // your caption fixes
  return clip
}

/** What Keep sends. youtube (5.1, Moments › YouTube too): a second edit of the same moment for YouTube, 16:9; with
 *  its settings (5.4), the YouTube edit as you made it: its own cut, title, description, captions and style. */
export function keepBody(c: Clip, post: string, opts: RenderOpts, sound = true, youtube: false | true | { opts: Partial<RenderOpts> } = false) {
  const body = { pid: c.pid, clip: clipBody(c, post), opts, sound }
  if (!youtube) return body
  if (youtube === true) return { ...body, youtube }
  const v = ytView(c)
  const text: Record<string, string> = {}
  if (v.userTitle && v.title.trim()) text.title = v.title.trim()
  if (v.userPost) text.description = v.postText
  return { ...body, youtube: { clip: clipBody(v, post), opts: youtube.opts, text } }
}

type EditedWord = Clip['words'][number] & { g?: unknown; edited?: boolean }
const wordsOut = (ws: Clip['words']) => (ws as EditedWord[]).map(w => ({ start: w.start, end: w.end, word: w.word, g: w.g, edited: !!w.edited }))

/** What /api/clip_state is sent: everything you did to a clip, saved with the VOD (5.4: its YouTube edit too). */
export function snapshot(c: Clip) {
  const yt = c.yt && { ...c.yt, words: undefined, words_edit: c.yt.wordsEdited && c.yt.words ? wordsOut(c.yt.words) : undefined }
  return { pid: c.pid, id: c.id, state: c.state, start: c.start, end: c.end, title: c.title, userTrim: !!c.userTrim,
    userTitle: !!c.userTitle, userPost: !!c.userPost, postText: c.postText, wordsEdited: !!c.wordsEdited, gain: c.gain,
    ...(c.wordsEdited ? { words_edit: wordsOut(c.words) } : {}), ...(yt ? { yt } : {}) }
}

// ---------------------------------------------------------------- the YouTube edit (5.4)

/** The moment as its YouTube edit: the TikTok edit, with what was changed for YouTube over it. "Restore original
 *  cut" there goes back to the TikTok edit's cut. Shown in the editor the same way, so everything works on it. */
export function ytView(c: Clip): Clip {
  const y: YtEdit = c.yt ?? {}
  const trim = y.userTrim && y.start != null && y.end != null
  return { ...c, start: trim ? y.start! : c.start, end: trim ? y.end! : c.end, userTrim: !!trim,
    title: y.userTitle && y.title != null ? y.title : c.title, userTitle: !!y.userTitle,
    postText: y.userPost ? y.postText ?? '' : '', userPost: !!y.userPost,
    gain: y.userGain && y.gain != null ? y.gain : c.gain,
    words: y.wordsEdited && y.words ? y.words : c.words, wordsEdited: !!y.wordsEdited || !!c.wordsEdited,
    wordsOrig: y.wordsEdited ? c.words : c.wordsOrig, orig: [c.start, c.end] }
}

const YT_KEYS = ['start', 'end', 'userTrim', 'title', 'userTitle', 'postText', 'userPost', 'gain', 'words', 'wordsEdited', 'wordsOrig', 'orig'] as const

/** An edit made to the YouTube edit (v, as ytView showed it, changed), put back on the moment c: the parts the
 *  YouTube edit has of its own go to c.yt, everything else (kept, skipped, the content check) to the moment. */
export function fromYtView(c: Clip, v: Clip): Clip {
  const was = ytView(c)
  const out: Clip = { ...v }
  for (const k of YT_KEYS) (out as Record<string, unknown>)[k] = (c as Record<string, unknown>)[k]
  const y: YtEdit = { ...c.yt }
  if (v.userTrim) Object.assign(y, { start: v.start, end: v.end, userTrim: true })
  else { y.userTrim = false; delete y.start; delete y.end }
  if (v.userTitle) Object.assign(y, { title: v.title, userTitle: true })
  else { y.userTitle = false; delete y.title }
  if (v.userPost) Object.assign(y, { postText: v.postText, userPost: true })
  else { y.userPost = false; delete y.postText }
  if (v.gain !== was.gain || y.userGain) Object.assign(y, { gain: v.gain, userGain: v.gain !== c.gain })
  if (v.words !== was.words) Object.assign(y, { words: v.words, wordsEdited: v.words !== c.words })
  if (y.wordsEdited === false) delete y.words
  out.yt = y
  return out
}

/** The Text and Layout settings of the YouTube edit: the TikTok edit's, in YouTube's shape (16:9, the whole picture,
 *  on a blurred background when the stream isn't 16:9 itself, nothing followed or zoomed), with what you changed
 *  for YouTube over them. */
export function ytOpts(o: RenderOpts, over: Partial<RenderOpts>, wideSource: boolean): RenderOpts {
  return { ...o, layout: wideSource ? 'full' : 'blur', layout_auto: false, track: 'off', punch: 'off', ...over, aspect: '16:9' } as RenderOpts
}

/** A clip from the server, merged into the one on screen: titles and checks arrive in the background,
 *  and its cut moves to sentence boundaries once it's been listened to, unless you trimmed it, or an edit is
 *  still waiting to be saved. */
export function merge(c: Clip, s: Clip, saving = false): Clip {
  const out: Clip = { ...c, listened: s.listened, words: c.wordsEdited ? c.words : s.words, titles: s.titles, vibe: s.vibe, emoji: s.emoji, reason: s.reason, post: s.post,
    held: s.held, description: s.description ?? c.description, hashtags: s.hashtags ?? c.hashtags,
    safety: s.safety ?? c.safety, check: s.check ?? c.check }
  // an edit of yours still waiting to be saved: the server's copy is older, so it doesn't move the cut or title
  if (saving) return out
  if (!c.userTitle && s.title) out.title = s.title
  if (s.orig && !c.userTrim) out.orig = s.orig
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
/** A clip score's grade, for its colour: 80+ green, 70+ yellow, under 50 red, and 50 to 69 left plain. */
export type Grade = 'top' | 'good' | 'fair' | 'low'
export const qClass = (q: number): Grade => (q >= 80 ? 'top' : q >= 70 ? 'good' : q >= 50 ? 'fair' : 'low')
