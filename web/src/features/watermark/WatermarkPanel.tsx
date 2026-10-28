import { useEffect, useRef, useState } from 'react'
import { api } from '../../api/client'
import { Campaigns, ExportFormat, Imported, type Mark, Picked, Shared, WmImage } from '../../api/schemas'
import { useConfirm } from '../../components/Confirm'
import { Seg } from '../../components/Seg'
import { useToast } from '../../components/Toasts'
import { usePoll } from '../../hooks/usePoll'
import { FONTS, POS_NAMES, wmUrl } from './geometry'
import { WatermarkPreview } from './WatermarkPreview'

export const MAX_LAYERS = 6
export const NEW_MARK: Mark = { kind: 'text', text: '', image: '', pos: 'tl', x: 0.5, y: 0.5, size: 100, opacity: 70, margin: 40,
  rotation: 0, color: '#FFFFFF', font: 'classic', outline: true, box: false, box_color: '#141414', start: 0, end: 0 }

interface Draft { on: boolean; marks: Mark[] }
type Ask = { kind: 'save' | 'share' | 'link'; value: string }
const ASK: Record<Ask['kind'], { label: string; ok: string; placeholder: string }> = {
  save: { label: 'Name for this watermark', ok: 'Save', placeholder: 'The campaign or channel it’s for' },
  share: { label: 'Name of the watermark file', ok: 'Save file', placeholder: 'My watermark' },
  link: { label: 'Link to the file', ok: 'Import', placeholder: 'In Discord: right-click the file › Copy link' },
}
const err = (e: unknown) => (e instanceof Error ? e.message : String(e))

/** Settings › Watermark: text and image layers on every exported clip, saved sets, and files to share
 *  in Discord. While the active campaign has its own watermark, that's the one edited here (it's the
 *  one clips get), as in the current UI. */
export function WatermarkPanel({ reloadKey = 0, onCampaigns }: { reloadKey?: number; onCampaigns: () => void }) {
  const toast = useToast()
  const confirm = useConfirm()
  const ex = usePoll(() => api(ExportFormat, '/api/export'), 60_000)
  const cp = usePoll(() => api(Campaigns, '/api/campaigns'), 60_000)
  const active = cp.data?.list.find(c => c.id === cp.data?.active)
  const camp = active?.watermark.use ? active : undefined
  const [draft, setDraft] = useState<Draft>()
  const [sel, setSel] = useState(0)
  const [preset, setPreset] = useState('')
  const [ask, setAsk] = useState<Ask>()
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined)
  const edits = useRef(0)               // edits not yet saved: the server's copy doesn't replace them
  const [synced, setSynced] = useState(0)
  const target = useRef(camp)
  target.current = camp
  const latest = useRef(draft)
  latest.current = draft

  useEffect(() => {
    if (edits.current || !ex.data || !cp.data) return
    setDraft(camp ? { on: true, marks: camp.watermark.marks } : ex.data.prefs.watermark)
  }, [ex.data, cp.data, camp, synced])
  useEffect(() => () => clearTimeout(timer.current), [])
  useEffect(() => {                  // Campaigns changed which one is active, or its watermark
    if (!reloadKey) return
    void Promise.all([ex.refresh(), cp.refresh()])
  }, [reloadKey]) // reloadKey only: the polls are stable

  async function refresh() { await Promise.all([ex.refresh(), cp.refresh()]) }
  function settled() {
    edits.current = 0
    setSynced(n => n + 1)
  }
  function save(next: Draft, now = false) {
    setDraft(next)
    const mine = ++edits.current
    clearTimeout(timer.current)
    timer.current = setTimeout(async () => {
      if (next.marks.some(m => m.kind === 'text' && !m.text.trim())) return      // still typing: an empty layer would be dropped
      try {
        const c = target.current
        if (c) {
          await api(Campaigns, '/api/campaigns/save', { campaign: { ...c, watermark: { use: true, marks: next.marks } } })
          onCampaigns()
        }
        else await api(ExportFormat, '/api/export', { watermark: next })
      } catch (e) {
        toast(err(e), 'err')
      }
      await refresh()        // both answers first: one alone would put back the other's older copy
      if (mine === edits.current) settled()
    }, now ? 0 : 400)
  }

  if (!draft) return <section className="panel set" data-testid="set-watermark"><header><h2>Watermark</h2></header></section>
  const marks = draft.marks
  const at = Math.max(0, Math.min(sel, marks.length - 1))
  const m = marks[at]
  const setMarks = (ms: Mark[], now = true) => save({ on: camp ? true : draft.on || ms.length > 0, marks: ms }, now)
  const edit = (patch: Partial<Mark>, now = false) => setMarks(marks.map((x, i) => (i === at ? { ...x, ...patch } : x)), now)
  function add(mark: Partial<Mark>) {
    setMarks([...marks, { ...NEW_MARK, ...mark }])
    setSel(marks.length)
  }
  function use(ms: Mark[]) {
    setMarks(structuredClone(ms))
    setSel(0)
  }
  async function pickImage(): Promise<string> {
    try {
      const path = (await api(Picked, '/api/pick_file', { kind: 'image' })).path
      return path ? (await api(WmImage, '/api/watermark/image', { path })).image : ''
    } catch (e) {
      toast(err(e), 'err')
      return ''
    }
  }
  async function importFile(body: { path: string } | { url: string }) {
    let r
    try { r = await api(Imported, '/api/import', body) } catch (e) { toast(err(e), 'err'); return }
    if (r.kind === 'image') { add({ kind: 'image', image: r.image }); toast('Image added as a watermark layer.') }
    else if (r.kind === 'watermark') {
      use(r.marks)
      setPreset(r.name)
      await ex.refresh()
      toast(`Imported the "${r.name}" watermark. It's in use and saved.`)
    } else {
      toast(`Imported the "${r.name}" campaign. Choose it under Campaigns to use it.`)
      onCampaigns()
    }
  }
  async function answer(a: Ask) {
    const value = a.value.trim()
    if (!value) return
    setAsk(undefined)
    try {
      if (a.kind === 'link') return void (await importFile({ url: value }))
      if (a.kind === 'share') {
        const r = await api(Shared, '/api/watermark/share', { name: value, marks })
        return void toast(`Saved "${r.name}" in your projects folder's Shared folder. Drop it into Discord to share it.`)
      }
      const pres = (ex.data?.prefs.wm_presets ?? []).filter(p => p.name !== value).concat([{ name: value, marks }])
      await api(ExportFormat, '/api/export', { wm_presets: pres })
      await ex.refresh()
      setPreset(value)
      toast(`Saved "${value}".`)
    } catch (e) {
      toast(err(e), 'err')
    }
  }
  async function deletePreset() {
    if (!await confirm({ title: `Delete the "${preset}" watermark?`, body: 'Only the saved copy goes. The layers in use now stay.', ok: 'Delete', danger: true })) return
    try {
      await api(ExportFormat, '/api/export', { wm_presets: (ex.data?.prefs.wm_presets ?? []).filter(p => p.name !== preset) })
      setPreset('')
      await ex.refresh()
    } catch (e) {
      toast(err(e), 'err')
    }
  }
  async function useMine() {
    if (!active) return
    try {
      await api(Campaigns, '/api/campaigns/save', { campaign: { ...active, watermark: { ...active.watermark, use: false } } })
      await refresh()
      settled()
      onCampaigns()
    } catch (e) {
      toast(err(e), 'err')
    }
  }
  const presets = ex.data?.prefs.wm_presets ?? []
  const free = m?.pos === 'free'

  return (
    <section className="panel set" aria-labelledby="set-watermark" data-testid="set-watermark">
      <header><h2 id="set-watermark">Watermark</h2></header>
      <label className="chk">
        <input type="checkbox" checked={draft.on} disabled={!!camp} onChange={e => save({ ...draft, on: e.target.checked }, true)} />
        <span>Put a watermark on exported clips<small>Your @handle or a logo, in the same place on every clip.</small></span>
      </label>
      {camp && (
        <div className="notice" data-testid="wm-camp">
          <span>The <b>{camp.name}</b> campaign has its own watermark, so that's the one shown and changed here.</span>
          <button className="btn sm" onClick={() => void useMine()}>Use mine instead</button>
        </div>
      )}
      {draft.on && (
        <>
          <div className="row wrap">
            <select className="input" aria-label="Saved watermarks" value={presets.some(p => p.name === preset) ? preset : ''}
                    onChange={e => {
                      const p = presets.find(x => x.name === e.target.value)
                      setPreset(e.target.value)
                      if (p) { use(p.marks); toast(`Using "${p.name}".`) }
                    }}>
              <option value="">{presets.length ? 'Saved watermarks…' : 'No saved watermarks'}</option>
              {presets.map(p => <option key={p.name}>{p.name}</option>)}
            </select>
            <button className="btn sm" disabled={!marks.length} onClick={() => setAsk({ kind: 'save', value: preset })}>Save as…</button>
            {presets.some(p => p.name === preset) && <button className="btn sm ghost" onClick={() => void deletePreset()}>Delete</button>}
            <span className="grow" />
            <button className="btn sm" title="A watermark or campaign file someone shared, or a logo image"
                    onClick={async () => {
                      try {
                        const path = (await api(Picked, '/api/pick_file', { kind: 'shared' })).path
                        if (path) await importFile({ path })
                      } catch (e) { toast(err(e), 'err') }
                    }}>Import…</button>
            <button className="btn sm" title="The link of a file shared in Discord" onClick={() => setAsk({ kind: 'link', value: '' })}>From link…</button>
            <button className="btn sm" disabled={!marks.length} title="One file with the layers and their images, to share in Discord"
                    onClick={() => setAsk({ kind: 'share', value: preset || camp?.name || 'My watermark' })}>Share…</button>
          </div>
          {ask && (
            <form className="row wrap" onSubmit={e => { e.preventDefault(); void answer(ask) }}>
              <input className="input grow" aria-label={ASK[ask.kind].label} placeholder={ASK[ask.kind].placeholder} autoFocus
                     value={ask.value} onChange={e => setAsk({ ...ask, value: e.target.value })}
                     onKeyDown={e => { if (e.key === 'Escape') setAsk(undefined) }} />
              <button className="btn sm" type="submit" disabled={!ask.value.trim()}>{ASK[ask.kind].ok}</button>
              <button className="btn sm ghost" type="button" onClick={() => setAsk(undefined)}>Cancel</button>
            </form>
          )}
          <div className="wm-grid">
            <WatermarkPreview marks={marks} sel={at} onSelect={setSel}
                              onMove={(i, p) => {
                                edits.current ||= 1          // being dragged: the server's copy waits
                                setDraft({ ...draft, marks: marks.map((x, j) => (j === i ? { ...x, ...p } : x)) })
                              }}
                              onDrop={() => { if (latest.current) save(latest.current, true) }} />
            <div className="wm-side">
              {marks.length ? (
                <ul className="wm-layers" aria-label="Layers, bottom to top">
                  {marks.map((x, i) => (
                    <li key={i} className={i === at ? 'on' : ''}>
                      <button className="wm-pick" aria-pressed={i === at} onClick={() => setSel(i)}>
                        {x.kind === 'image' ? <img src={wmUrl(x.image)} alt="" /> : <b className="wm-t" style={{ color: x.color }}>T</b>}
                        <span className="ellipsis">{x.kind === 'image' ? 'Image' : x.text || 'Text'}</span>
                        <small className="muted">{x.pos === 'free' ? 'placed by hand' : POS_NAMES[x.pos].toLowerCase()}</small>
                      </button>
                      <button className="ibtn" aria-label={`Bring layer ${i + 1} forward`} disabled={i === marks.length - 1}
                              onClick={() => { const ms = [...marks]; [ms[i], ms[i + 1]] = [ms[i + 1]!, ms[i]!]; setMarks(ms); setSel(i + 1) }}>↑</button>
                      <button className="ibtn" aria-label={`Remove layer ${i + 1}`}
                              onClick={() => { setMarks(marks.filter((_, j) => j !== i)); setSel(Math.min(at, marks.length - 2)) }}>×</button>
                    </li>
                  ))}
                </ul>
              ) : <p className="muted small">No layers yet. Add your @handle as text, or a logo image.</p>}
              <div className="row wrap">
                <button className="btn sm" disabled={marks.length >= MAX_LAYERS} onClick={() => add({ kind: 'text', text: '@yourchannel' })}>+ Text layer</button>
                <button className="btn sm" disabled={marks.length >= MAX_LAYERS}
                        onClick={async () => { const image = await pickImage(); if (image) add({ kind: 'image', image }) }}>+ Image layer</button>
              </div>
            </div>
          </div>
          {m && (
            <div className="wm-edit" key={at}>
              {m.kind === 'text'
                ? <label className="fld">Text<input className="input" maxLength={60} placeholder="@yourchannel" spellCheck={false}
                                                   value={m.text} onChange={e => edit({ text: e.target.value })} /></label>
                : <div className="fld">Image<span><button className="btn sm"
                    onClick={async () => { const image = await pickImage(); if (image) edit({ image }, true) }}>Change image…</button></span></div>}
              <div className="fld">Where
                <Seg label="Where" value={m.pos} options={Object.entries(POS_NAMES)} onChange={v => edit({ pos: v as Mark['pos'] }, true)} />
                {free && <span className="hint">Drag the layer on the preview, or use Across and Down.</span>}
              </div>
              <div className="form-grid two">
                <Slider label="Size" unit="%" min={20} max={400} step={5} value={m.size} onChange={v => edit({ size: v })} />
                <Slider label="Opacity" unit="%" min={5} max={100} step={5} value={m.opacity} onChange={v => edit({ opacity: v })} />
                <Slider label="Rotation" unit="°" min={-180} max={180} step={1} value={m.rotation} onChange={v => edit({ rotation: v })} />
                {!free && m.pos !== 'c' && <Slider label="Distance from the side" unit=" px" min={0} max={200} step={5} value={m.margin} onChange={v => edit({ margin: v })} />}
                {free && <Slider label="Across" unit="%" min={0} max={100} step={0.5} value={Math.round(m.x * 1000) / 10} onChange={v => edit({ x: v / 100 })} />}
                {free && <Slider label="Down" unit="%" min={0} max={100} step={0.5} value={Math.round(m.y * 1000) / 10} onChange={v => edit({ y: v / 100 })} />}
              </div>
              {m.kind === 'text' && (
                <div className="form-grid two">
                  <label>Font
                    <select className="input" value={m.font} onChange={e => edit({ font: e.target.value }, true)}>
                      {Object.entries(FONTS).map(([k, f]) => <option key={k} value={k}>{f.label}</option>)}
                    </select>
                  </label>
                  <label>Colour<input type="color" className="swatch" value={m.color.toLowerCase()} onChange={e => edit({ color: e.target.value.toUpperCase() })} /></label>
                  <label className="chk"><input type="checkbox" checked={m.outline} onChange={e => edit({ outline: e.target.checked }, true)} />
                    <span>Outline<small>A dark edge around the text</small></span></label>
                  <label className="chk"><input type="checkbox" checked={m.box} onChange={e => edit({ box: e.target.checked }, true)} />
                    <span>Box behind the text<small>Easier to read on busy footage</small></span></label>
                  {m.box && <label>Box colour<input type="color" className="swatch" value={m.box_color.toLowerCase()} onChange={e => edit({ box_color: e.target.value.toUpperCase() })} /></label>}
                </div>
              )}
              <div className="form-grid two">
                <label>Appears at (seconds)
                  <input className="input" type="number" min={0} step={0.5} defaultValue={m.start || 0} onChange={e => edit({ start: Number(e.target.value) || 0 })} />
                </label>
                <label>Disappears at (seconds)
                  <input className="input" type="number" min={0} step={0.5} placeholder="End" defaultValue={m.end || ''} onChange={e => edit({ end: Number(e.target.value) || 0 })} />
                </label>
              </div>
            </div>
          )}
        </>
      )}
    </section>
  )
}

function Slider({ label, unit, value, onChange, ...r }: { label: string; unit: string; min: number; max: number; step: number; value: number; onChange: (v: number) => void }) {
  return (
    <label><span className="wm-lab">{label}<span className="tnum">{Math.round(value * 10) / 10}{unit}</span></span>
      <input type="range" className="range" {...r} value={value} onChange={e => onChange(Number(e.target.value))} />
    </label>
  )
}
