import { useState } from 'react'
import { api } from '../../api/client'
import { TitlePrefs } from '../../api/schemas'
import { Seg } from '../../components/Seg'
import { useToast } from '../../components/Toasts'
import { usePoll } from '../../hooks/usePoll'

/** Settings › Titles and captions. Turning post-caption hashtags on or off changes the Hashtags
 *  preview, so onHashtags tells Settings to refresh it. */
export function TitlesPanel({ onHashtags }: { onHashtags: () => void }) {
  const toast = useToast()
  const poll = usePoll(() => api(TitlePrefs, '/api/titles'), 60_000)
  // a choice shows at once, and the server's answer takes over once it's saved
  const [pending, setPending] = useState<Partial<TitlePrefs>>({})
  const t = poll.data && { ...poll.data, ...pending }
  async function save(patch: Partial<TitlePrefs>) {
    setPending(p => ({ ...p, ...patch }))
    try {
      await api(TitlePrefs, '/api/titles', patch)
      if ('hashtags' in patch) onHashtags()
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), 'err')
    }
    await poll.refresh()
    setPending({})
  }
  return (
    <section className="panel set" aria-labelledby="set-titles" data-testid="set-titles">
      <header><h2 id="set-titles">Titles and captions</h2></header>
      <p className="muted">Each clip gets two titles: A, the best line said or how chat reacted, and B, the same idea with the streamer's name. Titles only describe what's in the clip.</p>
      <div className="fld">Title on new clips
        <Seg label="Title on new clips" value={t?.pick ?? 'a'} onChange={v => void save({ pick: v === 'b' ? 'b' : 'a' })}
             options={[['a', 'A · best line or reaction'], ['b', "B · with the streamer's name"]]} />
      </div>
      <label className="chk" title="When several different chatters type the same short phrase about a moment, offer it as a title">
        <input type="checkbox" checked={t?.chat_titles ?? true} disabled={!t} onChange={e => void save({ chat_titles: e.target.checked })} />
        <span>Titles from what chat said</span>
      </label>
      <p className="fine">Only when at least three different chatters typed the same phrase. One viewer spamming, emote walls, F's, links, numbers and usernames never count. Shown as <b>Chat: "…"</b> so it's clear who said it. A strong line said on stream still comes first.</p>
      <label className="chk">
        <input type="checkbox" checked={t?.hashtags ?? true} disabled={!t} onChange={e => void save({ hashtags: e.target.checked })} />
        <span>Add hashtags to post captions</span>
      </label>
      <p className="fine">Up to five tags that fit the clip (the platform, the streamer, the mood). Hashtags don't help every post, so you can leave them off. You can edit every caption before posting.</p>
    </section>
  )
}
