import { useEffect, useRef, useState } from 'react'
import { api } from '../../api/client'
import { YouTubeSettings } from '../../api/schemas'
import { Seg } from '../../components/Seg'
import { useToast } from '../../components/Toasts'

const VIS: Array<[string, string]> = [['public', 'Public'], ['unlisted', 'Unlisted'], ['private', 'Private']]

/** Posting › YouTube (5.1): how a clip's YouTube edit goes to YouTube. Ready-made, changeable: who can see it, who it's
 *  for, the title and description (templates filled in for each clip), tags, and Publish for me. */
export function YouTubePanel() {
  const toast = useToast()
  const [s, setS] = useState<YouTubeSettings>()
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined)
  useEffect(() => { api(YouTubeSettings, '/api/youtube').then(setS).catch(() => {}) }, [])
  // saved a moment after each change (typing a template saves once you pause)
  function change(patch: Partial<YouTubeSettings>, now = false) {
    setS(cur => (cur ? { ...cur, ...patch } : cur))
    clearTimeout(timer.current)
    const go = () => api(YouTubeSettings, '/api/youtube', patch).catch(e => toast(e instanceof Error ? e.message : String(e), 'err'))
    if (now) void go(); else timer.current = setTimeout(() => void go(), 500)
  }
  return (
    <section className="panel set" aria-labelledby="set-youtube" data-testid="set-youtube">
      <header><h2 id="set-youtube">YouTube</h2></header>
      <p>Turn on <b>YouTube too</b> in Moments and each clip you keep is also made as a 16:9 YouTube video. In Exports, <b>Upload
        to YouTube</b> puts it in YouTube Studio in Clip Radar's own browser window with these choices; <b>Open YouTube Studio</b> copies
        the title and description and opens the upload page in your browser. Change the title or description for one clip in Exports.</p>
      {!s ? <p className="muted">Loading…</p> : <>
        <div className="row wrap"><span className="muted">Who can see it</span>
          <Seg label="Visibility" value={s.visibility} options={VIS} onChange={v => change({ visibility: v as YouTubeSettings['visibility'] }, true)} /></div>
        <label className="chk"><input type="checkbox" checked={s.kids} onChange={e => change({ kids: e.target.checked }, true)} data-testid="set-yt-kids" />
          <span>Made for kids<span className="hint">YouTube asks this for every video. Stream clips almost never are: leave it off unless yours are.</span></span></label>
        <label className="fld">Title
          <input className="input" value={s.title} maxLength={200} onChange={e => change({ title: e.target.value })} data-testid="set-yt-title" /></label>
        <label className="fld">Description
          <textarea className="input" rows={4} value={s.description} maxLength={5000} onChange={e => change({ description: e.target.value })} data-testid="set-yt-desc" />
          <span className="hint">Filled in for each clip: {s.tokens.map(t => `{${t}}`).join(', ')}. {'{caption}'} is the clip's post caption,
            {' {source}'} a link to the stream at that moment.</span></label>
        <label className="fld">Tags
          <input className="input" value={s.tags} maxLength={500} placeholder="stream, highlights, gaming" onChange={e => change({ tags: e.target.value })} data-testid="set-yt-tags" />
          <span className="hint">Separated by commas.</span></label>
        <label className="chk"><input type="checkbox" checked={s.publish} onChange={e => change({ publish: e.target.checked }, true)} data-testid="set-yt-publish" />
          <span><b>Publish for me</b><span className="hint">{s.publish
            ? 'On: once the details are in, Clip Radar presses Publish. Check the title and description in Exports before you upload.'
            : 'Off: Clip Radar fills everything in and stops at the last step; you press Publish in YouTube Studio.'}</span></span></label>
      </>}
      <p className="fine">Sign in to YouTube in Clip Radar's browser window the first time; it remembers you. Clip Radar only says a
        clip was published when YouTube shows it.</p>
    </section>
  )
}
