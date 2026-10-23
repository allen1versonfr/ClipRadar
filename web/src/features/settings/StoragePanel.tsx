import { useState } from 'react'
import { api } from '../../api/client'
import { Cleanup, Cleared, Deleted, Locations, Ok, Picked, StorageUse } from '../../api/schemas'
import { useConfirm } from '../../components/Confirm'
import { useToast } from '../../components/Toasts'
import { fmtBytes, plural } from '../../format'
import { usePoll } from '../../hooks/usePoll'

const errText = (e: unknown) => e instanceof Error ? e.message : String(e)
const DAYS: Array<[number, string]> = [[1, '1 day'], [3, '3 days'], [7, '7 days'], [14, '14 days'], [30, '30 days']]

/** What the last automatic cleanup did, in a sentence (empty before the first run). */
export function lastCleanup(l: Cleanup['last']): string {
  if (!l.at) return ''
  const bits = [`Last run ${new Date(l.at * 1000).toLocaleString()}: freed ${fmtBytes(l.freed)}`]
  if (l.removed.length) bits.push(`removed ${l.removed.length === 1 ? 'the VOD of' : 'the VODs of'} ${l.removed.join(', ')}`)
  if (l.failed.length) bits.push(`couldn't remove ${l.failed.slice(0, 3).join(', ')}${l.failed.length > 3 ? '…' : ''}`)
  return bits.join('; ')
}

/** Settings › Storage: the folders, the space VODs and clips use, and automatic cleanup. */
export function StoragePanel({ refreshStatus }: { refreshStatus: () => Promise<void> }) {
  const toast = useToast()
  const confirm = useConfirm()
  const locs = usePoll(() => api(Locations, '/api/locations'), 30_000)
  const use = usePoll(() => api(StorageUse, '/api/storage'), 30_000)
  const clean = usePoll(() => api(Cleanup, '/api/cleanup'), 60_000)
  const [busy, setBusy] = useState('')

  async function setFolder(key: string, value: string) {
    try {
      const r = await api(Locations, '/api/locations', { [key]: value })
      const err = r.errors[key]
      if (err) toast(err, 'err')
      else toast(value ? 'Folder changed. New work goes there.' : 'Back to the default folder.')
    } catch (e) {
      toast(errText(e), 'err')
    }
    await Promise.all([locs.refresh(), use.refresh(), refreshStatus()])
  }
  async function change(key: string) {
    let path = ''
    try {
      path = (await api(Picked, '/api/pick_file', { kind: 'folder' })).path
    } catch {
      return
    }
    if (path) await setFolder(key, path)
  }
  async function openFolder(key: string) {
    try {
      await api(Ok, '/api/open_folder', { folder: key })
    } catch (e) {
      toast(errText(e), 'err')
    }
  }
  async function clearTemp() {
    try {
      const r = await api(Cleared, '/api/storage/clear_cache', {})
      toast(`Cleared ${fmtBytes(r.freed)} of temporary files.`)
    } catch (e) {
      toast(errText(e), 'err')
    }
    await locs.refresh()
  }
  async function remove(what: 'vods' | 'clips') {
    const u = use.data
    if (!u) return
    const ok = what === 'vods'
      ? await confirm({ title: `Delete ${plural(u.vods.count, 'downloaded stream')}?`, ok: 'Delete VODs', danger: true,
          body: `Frees ${fmtBytes(u.vods.bytes)}. Rendered clips are kept, and so are any videos of your own that you opened with Browse. You can download a VOD again from its link.` })
      : await confirm({ title: u.clips.count === 1 ? 'Delete your clip?' : `Delete all ${u.clips.count} clips?`, ok: 'Delete clips', danger: true,
          body: `Frees ${fmtBytes(u.clips.bytes)}. Every rendered clip in ${u.workroot} is deleted. This can't be undone. Downloaded VODs are kept.` })
    if (!ok) return
    setBusy(what)
    try {
      const r = await api(Deleted, '/api/delete', { what })
      const word = what === 'vods' ? 'stream' : 'clip'
      toast(r.failed.length
        ? `Deleted ${plural(r.deleted, word)}. ${r.failed.length} file(s) were in use; close any player showing them and try again.`
        : `Deleted ${plural(r.deleted, word)} and freed ${fmtBytes(r.freed)}.`, r.failed.length ? 'err' : '')
    } catch (e) {
      toast(errText(e), 'err')
    }
    setBusy('')
    await Promise.all([use.refresh(), locs.refresh()])
  }
  // a choice shows at once, and the server's answer takes over once it's saved
  const [pending, setPending] = useState<Partial<Cleanup['config']>>({})
  async function saveCleanup(patch: Partial<Cleanup['config']>, run = false) {
    if (!clean.data) return
    setPending(p => ({ ...p, ...patch }))
    try {
      await api(Cleanup, '/api/cleanup', { config: { ...clean.data.config, ...pending, ...patch }, run })
      if (run) await use.refresh()
    } catch (e) {
      toast(errText(e), 'err')
    }
    await clean.refresh()
    setPending({})
  }
  async function autoVods(on: boolean) {
    if (on && !await confirm({ title: 'Delete finished VODs automatically?', ok: 'Turn on',
      body: "A downloaded VOD is deleted once every clip is kept or skipped, every kept clip has rendered, nothing failed, and you haven't opened it for the time you choose. Rendered clips are never deleted." })) return
    await saveCleanup({ vods: on }, on)
  }

  const L = locs.data?.locations ?? []
  const problems = L.filter(x => x.problem).map(x => `${x.label}: ${x.problem}`)
  const u = use.data
  const c = clean.data && { ...clean.data.config, ...pending }
  const last = clean.data ? lastCleanup(clean.data.last) : ''
  return (
    <section className="panel set" aria-labelledby="set-storage" data-testid="set-storage">
      <header><h2 id="set-storage">Storage</h2></header>
      {problems.length > 0 && <p className="notice" role="alert">{problems.join(' ')}</p>}
      <ul className="loc-list" data-testid="loc-list">
        {L.map(x => (
          <li key={x.key} className={x.ok ? '' : 'bad'}>
            <div className="loc-main">
              <b>{x.label}</b>
              <code title={x.path || x.where}>{x.where || x.path}</code>
              <small className={x.low ? 'warn-tx' : 'muted'}>
                {x.free != null ? `${fmtBytes(x.free)} free${x.low ? ' (running low)' : ''}` : ''}{x.bytes != null ? ` · using ${fmtBytes(x.bytes)}` : ''}
              </small>
            </div>
            <span className="row wrap loc-acts">
              {x.path && <button className="btn sm ghost" onClick={() => void openFolder(x.key)} aria-label={`Open the ${x.label} folder`}>Open</button>}
              <button className="btn sm" onClick={() => void change(x.key)} aria-label={`Change the ${x.label} folder`}>Change…</button>
              {x.set && <button className="link" onClick={() => void setFolder(x.key, '')} aria-label={`Use the default ${x.label} folder`}>Default</button>}
              {x.key === 'temp' && <button className="btn sm ghost" onClick={() => void clearTemp()}
                title="Previews, framing data and finished live buffers. Never your videos, projects or clips.">Clear</button>}
            </span>
          </li>
        ))}
      </ul>
      <p className="fine">New work goes to a folder you change; nothing already there is moved, and Clip Radar still finds older projects.</p>
      <div className="store-rows">
        <div className="store-row">
          <div><span>Downloaded VODs and live recordings</span>
            <span className="tnum muted" data-testid="vod-use">{!u ? '' : u.vods.bytes ? `${plural(u.vods.count, 'stream')}, ${fmtBytes(u.vods.bytes)}` : 'None'}</span></div>
          <button className="btn danger" disabled={!u?.vods.bytes || !!busy} onClick={() => void remove('vods')}>{busy === 'vods' ? 'Deleting…' : 'Delete VODs'}</button>
        </div>
        <div className="store-row">
          <div><span>Rendered clips</span>
            <span className="tnum muted" data-testid="clip-use">{!u ? '' : u.clips.count ? `${plural(u.clips.count, 'clip')}, ${fmtBytes(u.clips.bytes)}` : 'None'}</span></div>
          <button className="btn danger" disabled={!u?.clips.count || !!busy} onClick={() => void remove('clips')}>{busy === 'clips' ? 'Deleting…' : 'Delete all clips'}</button>
        </div>
      </div>
      <div className="clean-auto">
        <h3>Clean up automatically</h3>
        <label className="chk" title="Previews, framing data and finished live buffers nobody has used for 3 days, and downloads that stopped over a day ago">
          <input type="checkbox" checked={c?.temp ?? true} disabled={!c} onChange={e => void saveCleanup({ temp: e.target.checked })} />
          <span>Old temporary files</span>
        </label>
        <label className="chk">
          <input type="checkbox" checked={c?.skipped ?? false} disabled={!c} onChange={e => void saveCleanup({ skipped: e.target.checked })} />
          <span>Delete a hands-free clip's file when you skip it<small>Only clips that rendered on their own in hands-free live clipping. Clips you rendered yourself are never touched.</small></span>
        </label>
        <label className="chk">
          <input type="checkbox" checked={c?.vods ?? false} disabled={!c} onChange={e => void autoVods(e.target.checked)} />
          <span>Delete a VOD when you're finished with it</span>
        </label>
        {c?.vods && (
          <label className="fld inline">Not opened for
            <select className="input" value={String(c.vod_days)} onChange={e => void saveCleanup({ vod_days: Number(e.target.value) })}>
              {DAYS.map(([d, l]) => <option key={d} value={d}>{l}</option>)}
            </select>
          </label>
        )}
        <p className="fine">Finished means: every suggested clip kept or skipped, every kept clip rendered, and no render waiting or failed (a retry needs the video). Only the downloaded video goes; your rendered clips and your review always stay.</p>
        {last && <p className="fine" data-testid="clean-last">{last}</p>}
      </div>
    </section>
  )
}
