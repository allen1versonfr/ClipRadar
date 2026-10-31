import type { QueueItem } from '../../api/schemas'

// Exports › posting: what Clip Radar knows about a clip's trip to TikTok and the campaign's Discord
// (the original page's handoffCell and renderTTStatus). Clip Radar only says a clip was posted when
// TikTok confirmed it in Clip Radar's own TikTok window.

export const TT_STEP: Record<string, string> = { browser: 'opening the browser', 'sign-in': 'waiting for you to sign in',
  'file box': 'putting the video in', 'caption box': 'typing the caption', caption: 'typing the caption',
  upload: 'TikTok was taking the video in', file: 'finding the clip' }

const time = (at: number) => new Date(at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

/** The TikTok column in the list. */
export function handoffCell(q: QueueItem): { text: string; tone: '' | 'ok' | 'err'; title: string } {
  const h = q.handoff
  if (!h) {
    if (q.tiktok?.state === 'done') return { text: q.tiktok.mode === 'draft' ? 'Sent to drafts' : 'Posted', tone: 'ok', title: 'Posted with an older version of Clip Radar' }
    return { text: 'Not yet', tone: '', title: '' }
  }
  const when = new Date(h.at * 1000).toLocaleString()
  switch (h.auto) {
    case 'posted': return { text: 'Posted', tone: 'ok', title: `TikTok confirmed it ${when}` }
    case 'working': return { text: 'Uploading…', tone: '', title: '' }
    case 'ready': return { text: 'In Studio, press Post', tone: '', title: `Video and caption put in TikTok Studio ${when}` }
    case 'pressed': return { text: 'Post pressed', tone: '', title: `Clip Radar pressed Post ${when}; TikTok hadn't confirmed yet` }
    case 'failed': return { text: 'Upload stopped', tone: 'err', title: h.error ?? '' }
    default: return { text: 'Opened in Studio', tone: '', title: `Opened TikTok Studio ${when}` }
  }
}

/** What the TikTok panel says about the last handoff: a line, and what to do next. */
export function handoffStatus(q: QueueItem, step: string | undefined): { text: string; more: string; tone: '' | 'ok' | 'err' } | null {
  if (step) return { text: step, more: '', tone: '' }
  const h = q.handoff
  if (!h) return null
  const at = time(h.at)
  switch (h.auto) {
    case 'posted': return { text: `Posted at ${at}. TikTok confirmed it in Clip Radar's TikTok window.`, more: '', tone: 'ok' }
    case 'ready': return { text: `The video and caption are in TikTok Studio (${at}).`, more: "Check them in Clip Radar's TikTok window and press Post there.", tone: 'ok' }
    case 'pressed': return { text: `Clip Radar pressed Post at ${at}, but TikTok hadn't confirmed it yet.`, more: "Look in Clip Radar's TikTok window to see whether it went through.", tone: '' }
    case 'failed': return { text: `The upload stopped while ${TT_STEP[h.step ?? ''] ?? 'working in TikTok Studio'}.`,
      more: `${h.error ?? ''} The video is selected in File Explorer, so you can drag it in and finish ${h.step === 'browser' ? 'in TikTok Studio, opening in your browser now' : 'there'}.`.trim(), tone: 'err' }
    case 'working': return { text: 'An upload was started but Clip Radar was closed before it finished.', more: 'Upload again, or finish it in the TikTok window.', tone: '' }
    default: return { text: `Opened TikTok Studio ${at}.`, more: "Finish uploading and posting there. Clip Radar can't see your browser, so it doesn't know whether the clip was posted.", tone: '' }
  }
}

/** The campaign's Discord: sent, or why it wasn't. */
export function discordStatus(q: QueueItem): { text: string; note: string; tone: 'ok' | 'err' } | null {
  const d = q.discord
  if (!d) return null
  if (d.error) return { text: d.error, note: '', tone: 'err' }
  return { text: `Sent to Discord ${time(d.at)}${d.attached ? ' with the video' : d.post_url ? ' with your post link' : ''}.`, note: d.note ?? '', tone: 'ok' }
}

/** Copy submission: the title, where it was posted (or the file), and the caption. */
export const submission = (q: QueueItem, url: string) => [q.title, url.trim() || q.name, q.post].filter(Boolean).join('\n')
