import { useEffect, useRef, useState } from 'react'
import { api } from '../../api/client'
import { type Campaigns, DiscordSent, Job, JobStarted, Ok, type QueueItem, Studio } from '../../api/schemas'
import { useToast } from '../../components/Toasts'
import { discordStatus, handoffStatus, submission } from './posting'

const err = (e: unknown) => (e instanceof Error ? e.message : String(e))

/** Copy text, saying so; where the clipboard is refused, the text is shown to copy by hand. */
export function useCopy() {
  const toast = useToast()
  return (text: string, what = 'Caption') => navigator.clipboard.writeText(text).then(() => toast(`${what} copied.`), () => toast(text))
}

// uploads running, per clip: the step they're on (kept while you look at another clip)
const uploading: Record<string, string> = {}

/** TikTok: Open TikTok Studio (the caption copied, the file shown, the upload page opened), or Upload to TikTok
 *  (Clip Radar's own browser window puts the video in and types the caption; Press Post for me presses Post). */
export function TikTokPanel({ q, onChanged }: { q: QueueItem; onChanged: () => Promise<void> }) {
  const toast = useToast()
  const copy = useCopy()
  const [caption, setCaption] = useState(q.post || q.title)
  const [studio, setStudio] = useState<Studio>({ available: false, press_post: false })
  const [step, setStep] = useState(uploading[q.id])
  const [error, setError] = useState('')
  useEffect(() => { setCaption(q.post || q.title); setError(''); setStep(uploading[q.id]) }, [q.id])
  useEffect(() => { api(Studio, '/api/studio').then(setStudio).catch(() => {}) }, [])
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
    if (uploading[q.id]) return
    setError('')
    const text = caption.trim()
    if (text) void navigator.clipboard.writeText(text).catch(() => {})     // in case a step has to be finished by hand
    let job: string
    try { job = (await api(JobStarted, '/api/handoff', { item: q.id, caption: text, auto: true })).job } catch (e) { setError(err(e)); return }
    const set = (s: string) => { uploading[q.id] = s; setStep(s) }
    set('Opening TikTok Studio')
    let j
    try {
      for (;;) {
        j = await api(Job, `/api/job/${job}`)
        if (j.status !== 'running') break
        set(j.step || 'Working in TikTok Studio')
        await new Promise(r => setTimeout(r, 700))
      }
    } catch (e) { toast(err(e), 'err') }
    delete uploading[q.id]
    setStep(undefined)
    await onChanged()
    const r = j?.result as { posted?: boolean; pressed?: boolean } | undefined
    if (j?.status === 'done') toast(r?.posted ? 'Posted on TikTok.' : r?.pressed ? 'Clip Radar pressed Post. Check the TikTok window.'
      : 'The video and caption are in TikTok Studio. Press Post there.')
    else if (j?.error) toast(j.error, 'err')
  }
  async function press(on: boolean) {
    try { setStudio(await api(Studio, '/api/studio', { press_post: on })) } catch (e) { toast(err(e), 'err') }
  }
  return (
    <section className="post-panel" aria-labelledby={`tt-${q.id}`} data-testid="tt-panel">
      <h3 id={`tt-${q.id}`}>TikTok</h3>
      <p className="muted small">{studio.available
        ? "Upload to TikTok opens TikTok Studio in Clip Radar's own browser window, puts the video in and types the caption. Sign in to TikTok there the first time."
        : "Clip Radar gets it ready; you post it in TikTok Studio. The caption is copied, the video is selected in File Explorer to drag in, and TikTok Studio's upload page opens in your browser."}</p>
      <label className="fld">Caption
        <textarea className="input" rows={4} maxLength={2200} value={caption} onChange={e => setCaption(e.target.value)} data-testid="tt-caption" />
        <span className="hint tnum">{caption.length} / 2200</span>
      </label>
      {studio.available && <>
        <label className="fld inline"><input type="checkbox" checked={studio.press_post} onChange={e => void press(e.target.checked)} data-testid="tt-press" />Press Post for me</label>
        <span className="hint">{studio.press_post
          ? "On: once the video is in, Clip Radar presses Post. It posts straight to your account, so check the caption first. TikTok's terms don't allow automated posting, so heavy use could get an account flagged."
          : 'Off: you check the post in TikTok Studio and press Post yourself.'}</span>
      </>}
      {error && <p className="err-tx" role="alert">{error}</p>}
      <div className="row wrap">
        <button className="btn ghost" onClick={() => void copy(caption)}>Copy caption</button>
        <button className={`btn${studio.available ? '' : ' primary'}`} onClick={() => void openStudio()} data-testid="tt-studio"
                title="Copies the caption, selects the video in File Explorer and opens TikTok Studio's upload page in your browser">Open TikTok Studio</button>
        {studio.available && <button className="btn primary" disabled={!!step} onClick={() => void upload()} data-testid="tt-auto">Upload to TikTok</button>}
      </div>
      {status && (
        <div className="post-status" aria-live="polite" data-testid="tt-status">
          {step ? <span className="row"><span className="meter busy" role="progressbar" aria-label={step}><i /></span>{step}</span>
            : <span className={status.tone === 'err' ? 'err-tx' : ''}>{status.text}</span>}
          {status.more && <span className="muted">{status.more}</span>}
        </div>
      )}
    </section>
  )
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
