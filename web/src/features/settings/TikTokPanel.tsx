import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import { Studio } from '../../api/schemas'
import { useToast } from '../../components/Toasts'

/** Posting › TikTok: how Upload to TikTok (in Exports) works here, and Press Post for me. */
export function TikTokPanel() {
  const toast = useToast()
  const [st, setSt] = useState<Studio>()
  useEffect(() => { api(Studio, '/api/studio').then(setSt).catch(() => {}) }, [])
  async function press(on: boolean) {
    try { setSt(await api(Studio, '/api/studio', { press_post: on })) } catch (e) { toast(e instanceof Error ? e.message : String(e), 'err') }
  }
  return (
    <section className="panel set" aria-labelledby="set-tiktok" data-testid="set-tiktok">
      <header><h2 id="set-tiktok">TikTok</h2></header>
      {!st ? <p className="muted">Loading…</p> : st.available ? <>
        <p>Upload to TikTok, in Exports, opens TikTok Studio in Clip Radar's own browser window, puts the video in and types the
          caption. Sign in to TikTok in that window the first time; it remembers you.</p>
        <label className="chk">
          <input type="checkbox" checked={st.press_post} onChange={e => void press(e.target.checked)} data-testid="set-press-post" />
          <span><b>Press Post for me</b>
            <span className="hint">{st.press_post
              ? "On: once the video is in, Clip Radar presses Post. It posts straight to your account, so check captions before you upload. TikTok's terms don't allow automated posting, so heavy use could get an account flagged."
              : 'Off: you check each post in TikTok Studio and press Post yourself.'}</span></span>
        </label>
      </> : (
        <p>Clip Radar gets clips ready and you post them in TikTok Studio: Open TikTok Studio, in Exports, copies the caption,
          selects the video in File Explorer and opens the upload page. To have Clip Radar put the video in for you, install
          Microsoft Edge or Google Chrome.</p>
      )}
      <p className="fine">Clip Radar only says a clip was posted when TikTok confirms it in Clip Radar's TikTok window.</p>
    </section>
  )
}
