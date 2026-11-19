import { type ReactNode, useEffect, useRef, useState } from 'react'
import { api } from '../../api/client'
import { type Campaigns, DiscordSent, Job, JobStarted, Ok, type QueueItem, Studio, YtHandoff } from '../../api/schemas'
import { useToast } from '../../components/Toasts'
import { SpellHint } from '../spelling/SpellHint'
import { discordStatus, handoffStatus, submission } from './postStatus'

const err = (e: unknown) => (e instanceof Error ? e.message : String(e))

/** Copy text, saying so; where the clipboard is refused, the text is shown to copy by hand. */
export function useCopy() {
  const toast = useToast()
  return (text: string, what = 'Caption') => navigator.clipboard.writeText(text).then(() => toast(`${what} copied.`), () => toast(text))
}

// uploads running, per clip: the step they're on (kept while you look at another clip)
const uploading: Record<string, string> = {}
// what the last answer said, for a panel shown before its own answer comes
let lastStudio: Studio = { available: false, press_post: false }

/** TikTok: Open TikTok Studio (the caption copied, the file shown, the upload page opened), or Upload to TikTok
 *  (Clip Radar's own browser window puts the video in and types the caption; Press Post for me presses Post). */
export function TikTokPanel({ q, onChanged, children, uploads }: {
  q: QueueItem; onChanged: () => Promise<void>; children?: ReactNode
  /** 5.1: whether Clip Radar can upload for you, from the list's own answer: the buttons are right from the first
   *  frame (before, Upload to TikTok appeared a moment later and Open TikTok Studio got pressed in its place) */
  uploads?: { tiktok: boolean; press_post: boolean } | undefined
}) {
  const toast = useToast()
  const copy = useCopy()
  const [caption, setCaption] = useState(q.post || q.title)
  const [studio, setStudio] = useState<Studio>(() => uploads ? { available: uploads.tiktok, press_post: uploads.press_post } : lastStudio)
  const [step, setStep] = useState(uploading[q.id])
  const [error, setError] = useState('')
  const shown = useRef(q.id)               // the clip on screen: an upload's steps only show on its own clip
  useEffect(() => { shown.current = q.id; setCaption(q.post || q.title); setError(''); setStep(uploading[q.id]) }, [q.id])
  useEffect(() => { api(Studio, '/api/studio').then(s => { lastStudio = s; setStudio(s) }).catch(() => {}) }, [])
  const status = handoffStatus(q, step)

  async function openStudio() {
    setError('')
    const text = caption.trim()
    if (text) void copy(text)
    try {
      await api(Ok, '/api/handoff', { item: q.id, caption: text })
      toast('TikTok Studio is opening. The caption is copied and the video is selected in File Explorer.')
      await onChanged()
    } catch (e) { setError(err(e)) }
  }
  async function upload() {
    const id = q.id
    if (uploading[id]) return
    setError('')
    const text = caption.trim()
    if (text) void navigator.clipboard.writeText(text).catch(() => {})     // in case a step has to be finished by hand
    // 4.40: the step shows (and the button is off) on the click, not once the server answers: a second click
    // in between started a second upload of the same clip
    const set = (s?: string) => {
      if (s) uploading[id] = s; else delete uploading[id]
      if (shown.current === id) setStep(s)
    }
    set('Opening TikTok Studio')
    let job: string
    try { job = (await api(JobStarted, '/api/handoff', { item: id, caption: text, auto: true })).job }
    catch (e) { set(undefined); if (shown.current === id) setError(err(e)); return }
    let j
    // 4.45: one status check that failed (the app busy for a moment) ended this loop while the upload went on: the
    // button came back on and the panel said Clip Radar had been closed. Only 40 failures in a row (10 s) give up.
    for (let misses = 0; ;) {
      try {
        j = await api(Job, `/api/job/${job}`)
        misses = 0
      } catch (e) {
        if (++misses >= 40) { toast(err(e), 'err'); break }
        await new Promise(r => setTimeout(r, 250))
        continue
      }
      if (j.status !== 'running') break
      set(j.step || 'Working in TikTok Studio')
      await new Promise(r => setTimeout(r, 250))        // 4.40: 700 ms made every step, and the end, show late
    }
    set(undefined)
    await onChanged()
    const r = j?.result as { posted?: boolean; pressed?: boolean } | undefined
    if (j?.status === 'done') toast(r?.posted ? 'Posted on TikTok.' : r?.pressed ? 'Clip Radar pressed Post. Check the TikTok window.'
      : 'The video and caption are in TikTok Studio. Press Post there.')
    else if (j?.error) toast(j.error, 'err')
  }
  async function press(on: boolean) {
    setStudio(s => ({ ...s, press_post: on }))            // shown at once; the server's answer follows
    try { const s = await api(Studio, '/api/studio', { press_post: on }); lastStudio = s; setStudio(s) } catch (e) { toast(err(e), 'err') }
  }
  const actions = (
    <section className="post-panel tt-top" aria-labelledby={`tt-${q.id}`} data-testid="tt-panel">
      <h3 id={`tt-${q.id}`} className="sr-only">TikTok</h3>
      <div className="row wrap">
        {studio.available && <button className="btn primary" disabled={!!step} onClick={() => void upload()} data-testid="tt-auto">Upload to TikTok</button>}
        <button className={`btn${studio.available ? '' : ' primary'}`} onClick={() => void openStudio()} data-testid="tt-studio"
                title="Copies the caption, selects the video in File Explorer and opens TikTok Studio's upload page in your browser">Open TikTok Studio</button>
        <button className="btn ghost" onClick={() => void copy(caption)}>Copy caption</button>
      </div>
      {error && <p className="err-tx" role="alert">{error}</p>}
      {status && (
        <div className="post-status" aria-live="polite" data-testid="tt-status">
          {step ? <span className="row"><span className="meter busy" role="progressbar" aria-label={step}><i /></span>{step}</span>
            : <span className={status.tone === 'err' ? 'err-tx' : ''}>{status.text}</span>}
          {status.more && <span className="muted">{status.more}</span>}
        </div>
      )}
      {studio.available && <>
        <label className="fld inline"><input type="checkbox" checked={studio.press_post} onChange={e => void press(e.target.checked)} data-testid="tt-press" />Press Post for me</label>
        <span className="hint" data-testid="tt-press-hint">{studio.press_post
          ? "On: once the video is in, Clip Radar presses Post. It posts straight to your account, so check the caption first. TikTok's terms don't allow automated posting, so heavy use could get an account flagged."
          : 'Off: you check the post in TikTok Studio and press Post yourself.'}</span>
      </>}
    </section>
  )
  const captionBox = (
    <section className="post-panel" aria-label="TikTok caption">
      <label className="fld">Caption for TikTok
        <textarea className="input" rows={4} maxLength={2200} value={caption} spellCheck onChange={e => setCaption(e.target.value)} data-testid="tt-caption" />
        <SpellHint text={caption} onFix={setCaption} />
        <span className="hint tnum">{caption.length} / 2200 · {studio.available
          ? "Upload to TikTok opens TikTok Studio in Clip Radar's own browser window, puts the video in and types this caption."
          : 'Open TikTok Studio copies this caption, selects the video in File Explorer and opens the upload page.'}</span>
      </label>
    </section>
  )
  // the buttons first, above the clip; the caption they use at the bottom
  return <>{actions}{children}{captionBox}</>
}

/** Submit to a campaign: the link to your post (saved as you type), Copy submission, Send to Discord. */
export function SubmitPanel({ q, campaigns, onChanged }: { q: QueueItem; campaigns: Campaigns | undefined; onChanged: () => Promise<void> }) {
  const toast = useToast()
  const copy = useCopy()
  const [url, setUrl] = useState(q.post_url)
  const [error, setError] = useState('')
  const [sending, setSending] = useState(false)
  const t = useRef<ReturnType<typeof setTimeout>>(undefined)
  useEffect(() => { setUrl(q.post_url); setError('') }, [q.id])
  const cp = q.campaign
  if (!cp || q.status !== 'done') return null
  const c = campaigns?.list.find(x => x.id === cp.id)
  const sent = discordStatus(q)
  function typed(v: string) {
    setUrl(v)
    clearTimeout(t.current)
    t.current = setTimeout(() => {
      api(Ok, '/api/queue/post_url', { item: q.id, url: v.trim() }).then(() => setError(''), e => setError(err(e)))
    }, 500)
  }
  async function send() {
    setError(''); setSending(true)
    try {
      const r = await api(DiscordSent, '/api/discord/send', { item: q.id, post_url: url.trim() })
      toast(r.attached ? 'Sent to Discord with the video.' : 'Sent to Discord.')
      if (r.note) toast(r.note)
      await onChanged()
    } catch (e) { setError(err(e)) } finally { setSending(false) }
  }
  return (
    <section className="post-panel" aria-labelledby={`sub-${q.id}`} data-testid="submit-panel">
      <h3 id={`sub-${q.id}`}>Submit to {cp.name}</h3>
      <p className="muted small">{!c ? "This campaign was deleted, so there's nowhere to send it."
        : c.discord.webhook ? `Sends ${c.discord.file ? 'the video (or your post link, once you have one)' : 'your post link'} to the campaign's Discord channel.${c.notes ? ` Notes: ${c.notes}` : ''}`
        : "Add the campaign's Discord webhook in Settings › Campaigns to send clips from here."}</p>
      <label className="fld">Link to your post
        <input className="input" maxLength={300} placeholder="https://www.tiktok.com/@you/video/…" spellCheck={false} value={url}
               onChange={e => typed(e.target.value)} data-testid="submit-url" />
        <span className="hint">Paste it once you've posted: campaigns count views from the post.</span>
      </label>
      {error && <p className="err-tx" role="alert">{error}</p>}
      <div className="row wrap">
        <button className="btn" onClick={() => void copy(submission(q, url), 'Submission')}>Copy submission</button>
        <button className="btn primary" disabled={!c?.discord.webhook || sending} onClick={() => void send()} data-testid="submit-send">Send to Discord</button>
      </div>
      {sent && <div className="post-status" data-testid="submit-status"><span className={sent.tone === 'err' ? 'err-tx' : ''}>{sent.text}</span>{sent.note && <span className="muted">{sent.note}</span>}</div>}
    </section>
  )
}

// YouTube uploads running, per clip: the step they're on
const ytUploading: Record<string, string> = {}

/** YouTube (5.1), for a clip's YouTube edit: Open YouTube Studio (the pass-through: title and description copied, the
 *  file shown, YouTube's upload page opened), or Upload to YouTube (Clip Radar's own browser window puts it in and fills
 *  in Settings › YouTube's choices; Publish is pressed only when Publish for me is on there). */
export function YouTubePanel({ q, onChanged, children, uploads }: {
  q: QueueItem; onChanged: () => Promise<void>; children?: ReactNode
  uploads?: { youtube: boolean; publish: boolean } | undefined
}) {
  const toast = useToast()
  const copy = useCopy()
  const [title, setTitle] = useState(q.yt_texts?.title ?? q.title)
  const [desc, setDesc] = useState(q.yt_texts?.description ?? '')
  const [step, setStep] = useState(ytUploading[q.id])
  const [error, setError] = useState('')
  const shown = useRef(q.id)
  useEffect(() => { shown.current = q.id; setTitle(q.yt_texts?.title ?? q.title); setDesc(q.yt_texts?.description ?? ''); setError(''); setStep(ytUploading[q.id]) }, [q.id])
  const can = uploads?.youtube ?? true
  const yt = q.yt
  const status = step ? '' : !yt ? '' : yt.auto === 'published' ? 'Published on YouTube.'
    : yt.auto === 'ready' ? (yt.left || 'The video is in YouTube Studio. Finish it there.')
    : yt.auto === 'failed' ? (yt.error || "The upload didn't finish.")
    : yt.auto === 'working' ? 'Uploading…' : 'YouTube Studio was opened. Upload the video there.'

  async function openStudio() {
    setError('')
    void copy(`${title.trim()}\n\n${desc.trim()}`.trim(), 'Title and description')
    try {
      await api(YtHandoff, '/api/yt_handoff', { item: q.id, title: title.trim(), description: desc })
      toast('YouTube Studio is opening. The title and description are copied and the video is selected in File Explorer.')
      await onChanged()
    } catch (e) { setError(err(e)) }
  }
  async function upload() {
    const id = q.id
    if (ytUploading[id]) return
    setError('')
    const set = (s?: string) => {
      if (s) ytUploading[id] = s; else delete ytUploading[id]
      if (shown.current === id) setStep(s)
    }
    set('Opening YouTube Studio')
    let job: string
    try { job = (await api(YtHandoff, '/api/yt_handoff', { item: id, title: title.trim(), description: desc, auto: true })).job ?? '' }
    catch (e) { set(undefined); if (shown.current === id) setError(err(e)); return }
    let j
    for (let misses = 0; ;) {
      try { j = await api(Job, `/api/job/${job}`); misses = 0 } catch (e) {
        if (++misses >= 40) { toast(err(e), 'err'); break }
        await new Promise(r => setTimeout(r, 250)); continue
      }
      if (j.status !== 'running') break
      set(j.step || 'Working in YouTube Studio')
      await new Promise(r => setTimeout(r, 250))
    }
    set(undefined)
    await onChanged()
    const r = j?.result as { published?: boolean; left?: string } | undefined
    if (j?.status === 'done') toast(r?.published ? 'Published on YouTube.' : r?.left || 'The video is in YouTube Studio. Finish it there.')
    else if (j?.error) toast(j.error, 'err')
  }
  const actions = (
    <section className="post-panel tt-top" aria-label="YouTube" data-testid="yt-panel">
      <div className="row wrap">
        {can && <button className="btn primary" disabled={!!step} onClick={() => void upload()} data-testid="yt-auto">Upload to YouTube</button>}
        <button className={`btn${can ? '' : ' primary'}`} onClick={() => void openStudio()} data-testid="yt-studio"
                title="Copies the title and description, selects the video in File Explorer and opens YouTube's upload page in your browser">Open YouTube Studio</button>
        <button className="btn ghost" onClick={() => void copy(title, 'Title')}>Copy title</button>
      </div>
      {error && <p className="err-tx" role="alert">{error}</p>}
      {(step || status) && (
        <div className="post-status" aria-live="polite" data-testid="yt-status">
          {step ? <span className="row"><span className="meter busy" role="progressbar" aria-label={step}><i /></span>{step}</span>
            : <span className={yt?.auto === 'failed' ? 'err-tx' : ''}>{status}</span>}
        </div>
      )}
      {can && <span className="hint" data-testid="yt-settings-hint">{uploads?.publish
        ? 'Publish for me is on: Clip Radar presses Publish once the details are in. '
        : 'You press Publish in YouTube Studio yourself. '}Visibility, who it’s for, tags and Publish for me are in{' '}
        <a href="#settings/youtube">Settings › YouTube</a>.</span>}
    </section>
  )
  const fields = (
    <section className="post-panel" aria-label="YouTube title and description">
      <label className="fld">Title for YouTube
        <input className="input" maxLength={100} value={title} onChange={e => setTitle(e.target.value)} data-testid="yt-title" />
        <span className="hint tnum">{title.length} / 100</span>
      </label>
      <label className="fld">Description
        <textarea className="input" rows={4} maxLength={5000} value={desc} spellCheck onChange={e => setDesc(e.target.value)} data-testid="yt-desc" />
      </label>
    </section>
  )
  return <>{actions}{children}{fields}</>
}
