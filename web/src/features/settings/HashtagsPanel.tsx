import { useEffect, useRef, useState } from 'react'
import { api } from '../../api/client'
import { Hashtags } from '../../api/schemas'
import { useToast } from '../../components/Toasts'

const PLATFORMS: Array<[string, string]> = [['tiktok', 'TikTok'], ['shorts', 'Shorts'], ['reels', 'Reels'], ['x', 'X']]
const FILE_MAX = [1, 2, 3, 4, 5, 6, 7, 8]
const POST_MAX = [3, 4, 5, 6, 8, 10, 12, 15]

/** "Kai Cenat: #KaiCenat #Mafia", one streamer per line (the original page's format). */
export const streamersText = (m: Record<string, string[]>) => Object.entries(m).map(([k, v]) => `${k}: ${v.join(' ')}`).join('\n')
export function streamersParse(txt: string): Record<string, string[]> {
  const out: Record<string, string[]> = {}
  for (const line of txt.split('\n')) {
    const i = line.indexOf(':')
    if (i < 1) continue
    const name = line.slice(0, i).trim()
    const tags = line.slice(i + 1).trim()
    if (name && tags) out[name] = tags.split(/[\s,]+/).filter(Boolean)
  }
  return out
}
const words = (s: string) => s.split(/[\s,]+/).filter(Boolean)

interface Draft {
  fileOn: boolean; fileName: boolean; fileGeneral: string; fileStreamers: string; fileMax: number
  postAuto: boolean; postGlobal: string; postStreamers: string; plats: Record<string, string>; postMax: number
}
const draftOf = (c: Hashtags['config']): Draft => ({
  fileOn: c.file.enabled, fileName: c.file.streamer_tag, fileGeneral: c.file.general.join(' '),
  fileStreamers: streamersText(c.file.streamers), fileMax: c.file.max,
  postAuto: c.post.auto, postGlobal: c.post.global.join(' '), postStreamers: streamersText(c.post.streamers),
  plats: Object.fromEntries(PLATFORMS.map(([k]) => [k, (c.post.platforms[k] ?? []).join(' ')])), postMax: c.post.max,
})
/** What /api/hashtags is sent: the same shape the current UI sends. */
export const bodyOf = (d: Draft, streamer: string) => ({
  file: { enabled: d.fileOn, streamer_tag: d.fileName, general: d.fileGeneral, streamers: streamersParse(d.fileStreamers), max: d.fileMax },
  post: { auto: d.postAuto, global: d.postGlobal, streamers: streamersParse(d.postStreamers),
    platforms: Object.fromEntries(Object.entries(d.plats).map(([k, v]) => [k, words(v)])), max: d.postMax },
  streamer,
})

/** Settings › Hashtags. What you type stays as typed; it's saved after a short pause, and the
 *  suggestions and preview come back from Clip Radar. */
export function HashtagsPanel({ reloadKey = 0 }: { reloadKey?: number }) {
  const toast = useToast()
  const [view, setView] = useState<Hashtags>()
  const [draft, setDraft] = useState<Draft>()
  const [streamer, setStreamer] = useState('')
  const saveTimer = useRef<ReturnType<typeof setTimeout>>(undefined)
  const previewTimer = useRef<ReturnType<typeof setTimeout>>(undefined)     // apart, so a preview never cancels a save

  useEffect(() => {
    api(Hashtags, '/api/hashtags').then(v => { setView(v); setDraft(draftOf(v.config)) })
      .catch(e => toast(e instanceof Error ? e.message : String(e), 'err'))
    return () => { clearTimeout(saveTimer.current); clearTimeout(previewTimer.current) }
  }, [toast])
  const streamerRef = useRef(streamer)
  streamerRef.current = streamer
  useEffect(() => {                  // post-caption hashtags were turned on or off: the preview changes
    if (!reloadKey) return
    api(Hashtags, `/api/hashtags?streamer=${encodeURIComponent(streamerRef.current)}`).then(setView).catch(() => {})
  }, [reloadKey])

  function schedule(timer: typeof saveTimer, fn: () => Promise<void>, ms: number) {
    clearTimeout(timer.current)
    timer.current = setTimeout(() => void fn(), ms)
  }
  function change(patch: Partial<Draft>, now = false) {
    if (!draft) return
    const d = { ...draft, ...patch }
    setDraft(d)
    schedule(saveTimer, async () => {
      try {
        setView(await api(Hashtags, '/api/hashtags', bodyOf(d, streamer)))
      } catch (e) {
        toast(e instanceof Error ? e.message : String(e), 'err')
      }
    }, now ? 0 : 500)
  }
  function preview(name: string) {
    setStreamer(name)
    schedule(previewTimer, async () => {
      try {
        setView(await api(Hashtags, `/api/hashtags?streamer=${encodeURIComponent(name)}`))
      } catch { /* the preview waits for the next change */ }
    }, 300)
  }

  if (!view || !draft) return <section className="panel set" data-testid="set-tags"><header><h2>Hashtags</h2></header></section>
  const have = new Set(words(draft.fileGeneral).map(t => t.toLowerCase()))
  const fileName = draft.fileOn ? view.preview.file : view.preview.file.replace(/(?: #[^\s.]+)+(?=\.\w+$)/, '') + ' (no tags)'
  return (
    <section className="panel set" aria-labelledby="set-tags" data-testid="set-tags">
      <header><h2 id="set-tags">Hashtags</h2></header>
      <p className="muted">Two separate lists: tags added to each exported <b>file name</b>, and tags under the <b>post caption</b> you copy. A streamer's community tags go only where you put them.</p>
      <div className="tag-cols">
        <div className="tag-card">
          <label className="chk"><input type="checkbox" checked={draft.fileOn} onChange={e => change({ fileOn: e.target.checked }, true)} /><span><b className="tag-head">In file names</b></span></label>
          {draft.fileOn && (
            <>
              <label className="chk"><input type="checkbox" checked={draft.fileName} onChange={e => change({ fileName: e.target.checked }, true)} /><span>The streamer's name as a tag</span></label>
              <label className="fld">General tags
                <input className="input" placeholder="#TwitchClips #Gaming" spellCheck={false} value={draft.fileGeneral}
                       onChange={e => change({ fileGeneral: e.target.value })} />
              </label>
              <div className="row wrap" aria-label="Suggested tags">
                {view.suggestions.filter(t => !have.has(t.toLowerCase())).map(t => (
                  <button key={t} className="btn sm ghost" onClick={() => change({ fileGeneral: `${draft.fileGeneral} ${t}`.trim() }, true)}>+ {t}</button>
                ))}
              </div>
              <label className="fld">Per streamer <span className="hint">one per line: name: tags</span>
                <textarea className="input" rows={3} placeholder="Kai Cenat: #KaiCenat #Mafia" spellCheck={false} value={draft.fileStreamers}
                          onChange={e => change({ fileStreamers: e.target.value })} />
              </label>
              <label className="fld inline">At most
                <select className="input" value={String(draft.fileMax)} onChange={e => change({ fileMax: Number(e.target.value) }, true)}>
                  {FILE_MAX.map(n => <option key={n}>{n}</option>)}
                </select>
              </label>
            </>
          )}
        </div>
        <div className="tag-card">
          <div className="fld"><b className="tag-head">In post captions</b><span className="hint">On or off with "Add hashtags to post captions" in Titles and captions.</span></div>
          <label className="chk" title="The platform, the streamer and the clip's mood"><input type="checkbox" checked={draft.postAuto}
                 onChange={e => change({ postAuto: e.target.checked }, true)} /><span>Clip Radar's tags for each clip</span></label>
          <label className="fld">On every post
            <input className="input" placeholder="#fyp #clips" spellCheck={false} value={draft.postGlobal} onChange={e => change({ postGlobal: e.target.value })} />
          </label>
          <label className="fld">Per streamer <span className="hint">one per line: name: tags</span>
            <textarea className="input" rows={3} placeholder="Kai Cenat: #KaiCenatClips" spellCheck={false} value={draft.postStreamers}
                      onChange={e => change({ postStreamers: e.target.value })} />
          </label>
          <div className="fld">Per platform
            <div className="plat-grid">
              {PLATFORMS.map(([k, label]) => (
                <label key={k}>{label}
                  <input className="input" spellCheck={false} value={draft.plats[k] ?? ''} onChange={e => change({ plats: { ...draft.plats, [k]: e.target.value } })} />
                </label>
              ))}
            </div>
          </div>
          <label className="fld inline">At most
            <select className="input" value={String(draft.postMax)} onChange={e => change({ postMax: Number(e.target.value) }, true)}>
              {POST_MAX.map(n => <option key={n}>{n}</option>)}
            </select>
          </label>
        </div>
      </div>
      <div className="tag-preview" data-testid="tag-preview">
        <label className="fld inline">Preview for
          <input className="input" placeholder="Kai Cenat" spellCheck={false} value={streamer} onChange={e => preview(e.target.value)} />
        </label>
        <p className="fine">File name: <code data-testid="tag-file">{fileName}</code></p>
        {view.preview.post_on
          ? PLATFORMS.map(([k, label]) => <p className="fine" key={k}>{label}: <code>{(view.preview.post[k] ?? []).join(' ') || '(none)'}</code></p>)
          : <p className="fine">Post captions: hashtags are off (Titles and captions › Add hashtags to post captions).</p>}
      </div>
    </section>
  )
}
