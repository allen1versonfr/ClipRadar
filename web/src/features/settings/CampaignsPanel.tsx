import { useEffect, useRef, useState } from 'react'
import { api } from '../../api/client'
import { type Campaign, Campaigns, ExportFormat, Imported, Ok, Picked, Shared } from '../../api/schemas'
import { useConfirm } from '../../components/Confirm'
import { Seg } from '../../components/Seg'
import { useToast } from '../../components/Toasts'
import { usePoll } from '../../hooks/usePoll'

/** discordhook.HOOK_RE: a channel webhook, and nothing else */
export const HOOK_RE = /^https:\/\/(?:(?:ptb|canary)\.)?discord(?:app)?\.com\/api(?:\/v\d+)?\/webhooks\/\d+\/[\w-]+$/
const HOOK_HELP = 'In Discord: channel settings › Integrations › Webhooks › Copy webhook URL.'

/** What's typed in the editor: numbers and tags as typed, the rest as the campaign has them. */
interface Form {
  name: string; org: string; link: string; notes: string; caption: string; where: 'end' | 'start'; tags: string
  min: string; max: string; wmUse: boolean; hook: string; auto: boolean; file: boolean; message: string
}
export const formOf = (c: Campaign | undefined, defaultMessage: string): Form => ({
  name: c?.name ?? '', org: c?.org ?? '', link: c?.link ?? '', notes: c?.notes ?? '', caption: c?.caption ?? '',
  where: c?.caption_where ?? 'end', tags: (c?.tags ?? []).join(' '),
  min: c?.min_len ? String(c.min_len) : '', max: c?.max_len ? String(c.max_len) : '',
  wmUse: c?.watermark.use ?? false, hook: c?.discord.webhook ?? '', auto: c?.discord.auto ?? false,
  file: c?.discord.file ?? true, message: c?.discord.message || defaultMessage,
})
/** The campaign /api/campaigns/save is sent: the one being edited with the form on top (tags as typed). */
export function campaignOf(c: Campaign | undefined, f: Form, marks: Campaign['watermark']['marks'] | undefined) {
  return {
    ...(c ?? {}), name: f.name.trim(), org: f.org.trim(), link: f.link.trim(), notes: f.notes, caption: f.caption.trim(),
    caption_where: f.where, tags: f.tags, min_len: Number(f.min) || 0, max_len: Number(f.max) || 0,
    watermark: { use: f.wmUse, marks: marks ?? c?.watermark.marks ?? [] },
    discord: { webhook: f.hook.trim(), auto: f.auto, file: f.file, message: f.message },
  }
}
const err = (e: unknown) => (e instanceof Error ? e.message : String(e))
const rules = (c: Campaign) => [c.caption || c.tags.length ? 'caption rules' : '', c.min_len || c.max_len ? 'length' : '',
  c.watermark.use ? 'watermark' : '', c.discord.webhook ? 'Discord' : ''].filter(Boolean).join(' · ')

/** Settings › Campaigns: a clipping campaign's rules, watermark and Discord channel, and which one is active.
 *  `onChange` tells the Watermark panel (the active campaign can have its own watermark). */
export function CampaignsPanel({ reloadKey = 0, onChange }: { reloadKey?: number; onChange: () => void }) {
  const toast = useToast()
  const confirm = useConfirm()
  const poll = usePoll(() => api(Campaigns, '/api/campaigns'), 60_000)
  const v = poll.data
  const [editId, setEditId] = useState('')              // '' nothing open, 'new', or a campaign's id
  const [form, setForm] = useState<Form>()
  const [copied, setCopied] = useState<Campaign['watermark']['marks']>()
  const [showHook, setShowHook] = useState(false)
  const [shareHook, setShareHook] = useState(false)
  const [link, setLink] = useState<string>()
  const msgBox = useRef<HTMLTextAreaElement>(null)
  const nameBox = useRef<HTMLInputElement>(null)

  useEffect(() => {                  // the Watermark panel changed a campaign, or imported one
    if (reloadKey) void poll.refresh()
  }, [reloadKey])

  const editing = v?.list.find(c => c.id === editId)
  function open(id: string) {
    setEditId(id)
    setForm(formOf(v?.list.find(c => c.id === id), v?.default_message ?? ''))
    setCopied(undefined)
    setShowHook(false)
    if (id === 'new') setTimeout(() => nameBox.current?.focus())
  }
  const set = (patch: Partial<Form>) => setForm(f => f && { ...f, ...patch })
  async function changed() {
    await poll.refresh()
    onChange()
  }

  async function setActive(id: string) {
    try {
      const r = await api(Campaigns, '/api/campaigns/active', { id })
      const a = r.list.find(c => c.id === r.active)
      toast(a ? `Clips you keep now follow ${a.name}'s rules.` : 'No campaign: clips use your own settings.')
    } catch (e) {
      toast(err(e), 'err')
    }
    await changed()
  }
  async function save() {
    if (!form || !v) return
    const c = campaignOf(editing, form, copied)
    if (!c.name) { toast('Give the campaign a name.', 'err'); nameBox.current?.focus(); return }
    if (c.discord.webhook && !HOOK_RE.test(c.discord.webhook)) { toast(`That isn't a Discord webhook link. ${HOOK_HELP}`, 'err'); return }
    const isNew = editId === 'new'
    try {
      const r = await api(Campaigns, '/api/campaigns/save', { campaign: c, activate: isNew && !v.active })
      const saved = isNew ? r.list.at(-1) : r.list.find(x => x.id === editId)
      setEditId(saved?.id ?? '')
      setForm(formOf(saved, r.default_message))
      setCopied(undefined)
      toast(`Saved "${c.name}".`)
    } catch (e) {
      toast(err(e), 'err')
    }
    await changed()
  }
  async function remove() {
    if (!editing) return
    if (!await confirm({ title: `Delete "${editing.name}"?`, body: 'Its rules, watermark and Discord webhook are removed. Clips already rendered keep their files.', ok: 'Delete', danger: true })) return
    try {
      await api(Campaigns, '/api/campaigns/delete', { id: editing.id })
      setEditId('')
    } catch (e) {
      toast(err(e), 'err')
    }
    await changed()
  }
  async function share() {
    try {
      const r = await api(Shared, '/api/campaigns/share', { id: editId, webhook: shareHook })
      toast(`Saved "${r.name}" in your projects folder's Shared folder. Drop it into Discord to share it.`)
    } catch (e) {
      toast(err(e), 'err')
    }
  }
  async function test() {
    try {
      await api(Ok, '/api/campaigns/test', { webhook: form?.hook.trim() ?? '' })
      toast('Sent a test message. Check the channel in Discord.')
    } catch (e) {
      toast(err(e), 'err')
    }
  }
  async function copyMine() {
    const marks = (await api(ExportFormat, '/api/export').catch(() => undefined))?.prefs.watermark.marks ?? []
    if (!marks.length) { toast('Your watermark has no layers yet: add some under Watermark.', 'err'); return }
    setCopied(structuredClone(marks))
    set({ wmUse: true })
  }
  async function importFile(body: { path: string } | { url: string }) {
    try {
      const r = await api(Imported, '/api/import', body)
      if (r.kind === 'campaign') toast(`Imported the "${r.name}" campaign. Choose it as the active campaign to use it.`)
      else if (r.kind === 'watermark') toast(`Imported the "${r.name}" watermark. It's under Watermark › Saved watermarks.`)
      else toast("That's an image, not a campaign file. Add it as an image layer under Watermark.", 'err')
    } catch (e) {
      toast(err(e), 'err')
    }
    await changed()
  }

  if (!v) return <section className="panel set" data-testid="set-campaigns"><header><h2>Campaigns</h2></header></section>
  const marksNow = copied ?? editing?.watermark.marks ?? []
  return (
    <section className="panel set" aria-labelledby="set-campaigns" data-testid="set-campaigns">
      <header><h2 id="set-campaigns">Campaigns</h2></header>
      <p className="muted">For clipping campaigns and clipping Discords. A campaign keeps one organisation's rules together: the text and hashtags every post needs, how long clips must be, its watermark, and the Discord channel finished clips go to. Clips you keep while it's active follow its rules.</p>
      <div className="row wrap">
        <label className="fld inline">Active campaign
          <select className="input" value={v.active} onChange={e => void setActive(e.target.value)}>
            <option value="">None (your own settings)</option>
            {v.list.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </label>
        <span className="grow" />
        <button className="btn sm" onClick={() => open('new')}>+ New</button>
        <button className="btn sm" title="A campaign file someone shared"
                onClick={async () => {
                  const path = (await api(Picked, '/api/pick_file', { kind: 'shared' }).catch(() => undefined))?.path
                  if (path) await importFile({ path })
                }}>Import…</button>
        <button className="btn sm" title="The link of a campaign file shared in Discord" onClick={() => setLink('')}>From link…</button>
      </div>
      {link !== undefined && (
        <form className="row wrap" onSubmit={e => { e.preventDefault(); const u = link.trim(); setLink(undefined); if (u) void importFile({ url: u }) }}>
          <input className="input grow" aria-label="Link to the campaign file" placeholder="In Discord: right-click the file › Copy link" autoFocus
                 value={link} onChange={e => setLink(e.target.value)} onKeyDown={e => { if (e.key === 'Escape') setLink(undefined) }} />
          <button className="btn sm" type="submit" disabled={!link.trim()}>Import</button>
          <button className="btn sm ghost" type="button" onClick={() => setLink(undefined)}>Cancel</button>
        </form>
      )}
      {v.list.length ? (
        <ul className="cp-list" aria-label="Campaigns">
          {v.list.map(c => (
            <li key={c.id}>
              <button className={`cp-row${c.id === editId ? ' on' : ''}`} aria-pressed={c.id === editId} onClick={() => open(c.id)}>
                <b>{c.name}</b>{c.org && <span className="muted small">{c.org}</span>}
                {c.id === v.active && <span className="pill">Active</span>}
                <span className="grow" /><span className="muted small">{rules(c)}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : <p className="muted small">No campaigns yet. Make one, or import the file a clipping server shared.</p>}

      {form && (editId === 'new' || editing) && (
        <div className="cp-edit" data-testid="cp-edit">
          <h3 className="sub">{editing ? editing.name : 'New campaign'}</h3>
          <div className="form-grid two">
            <label>Name<input ref={nameBox} className="input" maxLength={60} placeholder="Kai Cenat clipping" spellCheck={false} value={form.name} onChange={e => set({ name: e.target.value })} /></label>
            <label>Organisation or server<input className="input" maxLength={60} placeholder="Clipping Discord" spellCheck={false} value={form.org} onChange={e => set({ org: e.target.value })} /></label>
            <label>Rules link<input className="input" maxLength={300} placeholder="https://discord.gg/…" spellCheck={false} value={form.link} onChange={e => set({ link: e.target.value })} /></label>
            <label>Notes<textarea className="input" rows={2} maxLength={2000} placeholder="Pay per 1,000 views, only TikTok, no reposts…" value={form.notes} onChange={e => set({ notes: e.target.value })} /></label>
          </div>
          <h3 className="sub">Post caption</h3>
          <div className="form-grid two">
            <label>Text every post must have<textarea className="input" rows={2} maxLength={500} placeholder="@brand · Use code KAI" spellCheck={false} value={form.caption} onChange={e => set({ caption: e.target.value })} /></label>
            <label>Hashtags every post must have<input className="input" placeholder="#ad #KaiCenatClips" spellCheck={false} value={form.tags} onChange={e => set({ tags: e.target.value })} />
              <span className="hint">Added on top of your own and never left out.</span></label>
          </div>
          <Seg label="Where the text goes" value={form.where} options={[['end', 'At the end'], ['start', 'At the start']]} onChange={w => set({ where: w === 'start' ? 'start' : 'end' })} />
          <h3 className="sub">Clip length</h3>
          <div className="form-grid two">
            <label>At least (seconds)<input className="input" type="number" min={0} max={600} step={1} placeholder="Any" value={form.min} onChange={e => set({ min: e.target.value })} /></label>
            <label>At most (seconds)<input className="input" type="number" min={0} max={600} step={1} placeholder="Any" value={form.max} onChange={e => set({ max: e.target.value })} /></label>
          </div>
          <span className="hint">Clips outside these get a warning in Exports.</span>
          <h3 className="sub">Watermark</h3>
          <label className="chk"><input type="checkbox" checked={form.wmUse} onChange={e => set({ wmUse: e.target.checked })} />
            <span>Its own watermark (instead of yours)<small>While this campaign is active, Watermark edits its watermark.</small></span></label>
          <div className="row wrap">
            <button className="btn sm" onClick={() => void copyMine()}>Copy my current watermark</button>
            <span className="muted small" data-testid="cp-wm-info">
              {marksNow.length ? `${marksNow.length} layer${marksNow.length > 1 ? 's' : ''}${copied ? ' (save to keep)' : ''}` : 'No layers yet'}
            </span>
          </div>
          <h3 className="sub">Discord</h3>
          <label className="fld">Webhook link
            <span className="row">
              <input className="input grow" type={showHook ? 'text' : 'password'} maxLength={300} placeholder="https://discord.com/api/webhooks/…"
                     spellCheck={false} autoComplete="off" value={form.hook} onChange={e => set({ hook: e.target.value })} />
              <button className="btn sm" type="button" onClick={() => setShowHook(s => !s)}>{showHook ? 'Hide' : 'Show'}</button>
              <button className="btn sm" type="button" disabled={!form.hook.trim()} onClick={() => void test()}>Test</button>
            </span>
            <span className="hint">{HOOK_HELP} It can only post in that one channel.</span>
          </label>
          <label className="chk"><input type="checkbox" checked={form.auto} onChange={e => set({ auto: e.target.checked })} /><span>Send each clip as soon as it renders</span></label>
          <label className="chk" title={`Discord takes files up to ${v.upload_mb} MB on servers without boosts; bigger clips are sent as a message only.`}>
            <input type="checkbox" checked={form.file} onChange={e => set({ file: e.target.checked })} /><span>Attach the video (when there's no post link yet)</span></label>
          <label className="fld">Message
            <textarea ref={msgBox} className="input" rows={2} maxLength={1500} spellCheck={false} value={form.message} onChange={e => set({ message: e.target.value })} />
          </label>
          <div className="row wrap" aria-label="Add to the message">
            {v.tokens.map(t => (
              <button key={t} className="btn sm ghost" type="button" onClick={() => {
                const pos = msgBox.current?.selectionStart ?? form.message.length
                set({ message: form.message.slice(0, pos) + `{${t}}` + form.message.slice(pos) })
              }}>{`{${t}}`}</button>
            ))}
          </div>
          <div className="row wrap">
            {editing && <button className="btn ghost sm" onClick={() => void remove()}>Delete</button>}
            <span className="grow" />
            {editing && <>
              <label className="chk" title="Anyone with the webhook can post in that channel: share it only with your team">
                <input type="checkbox" checked={shareHook} onChange={e => setShareHook(e.target.checked)} /><span>Include the webhook</span></label>
              <button className="btn sm" title="One file with the rules and watermark, to drop into Discord" onClick={() => void share()}>Share…</button>
            </>}
            <button className="btn sm ghost" onClick={() => setEditId('')}>Close</button>
            <button className="btn primary sm" onClick={() => void save()}>Save</button>
          </div>
        </div>
      )}
    </section>
  )
}
