import { useRef, useState, type PointerEvent, type ReactNode } from 'react'
import { api } from '../../api/client'
import { ExportFormat, FramingSuggest } from '../../api/schemas'
import { Seg } from '../../components/Seg'
import { useToast } from '../../components/Toasts'
import { RENDER_DEFAULTS, type RenderOpts } from '../../prefs'
import { FONTS, fontCss } from '../watermark/geometry'
import { camRects, layoutOf, MAX_CAMS } from './draw'

// Review's Text and Layout tabs (the original page's #pane-text and #pane-layout): your render style,
// shared with the current page through vc_opts; the preview shows every change at once.

type SetOpts = (patch: Partial<RenderOpts>, persist?: boolean) => void
type Preset = { name: string; opts: Record<string, unknown> }

export const SWATCHES = ['#ffe600', '#ffd21f', '#ffb800', '#fff27a', '#ffffff', '#4fd1ff', '#5ee6a8', '#ff6b8b']
export const CAP_TEMPLATES: Preset[] = [
  { name: 'Signature yellow', opts: { caption_style: 'pop', caption_color: '#ffe600', caption_font: 'montserrat', uppercase: true, caption_motion: 'word', words_per_line: 3, caption_emph: false, cap_outline: 120 } },
  { name: 'Karaoke', opts: { caption_style: 'karaoke', caption_color: '#ffd21f', caption_font: 'poppins', uppercase: true, caption_motion: 'none', words_per_line: 4, caption_emph: false } },
  { name: 'Boxed', opts: { caption_style: 'box', caption_color: '#ffe600', caption_font: 'inter', uppercase: true, caption_motion: 'pop', words_per_line: 3, caption_emph: false } },
  { name: 'Bold bounce', opts: { caption_style: 'pop', caption_color: '#ffb800', caption_font: 'luckiest', uppercase: true, caption_motion: 'bounce', words_per_line: 2, caption_emph: true } },
  { name: 'Clean subtitles', opts: { caption_style: 'clean', caption_color: '#ffffff', caption_font: 'inter', uppercase: false, caption_motion: 'fade', words_per_line: 6, caption_emph: false } },
  { name: 'Word by word', opts: { caption_style: 'pop', caption_color: '#ffe600', caption_font: 'anton', uppercase: true, caption_motion: 'reveal', words_per_line: 4, caption_emph: false } },
]
/** What a saved caption style keeps, and a saved framing. */
export const CAP_KEYS = ['captions', 'caption_pos', 'caption_color', 'caption_size', 'words_per_line', 'uppercase', 'caption_style', 'caption_font',
  'caption_anim', 'caption_motion', 'caption_speed', 'caption_emph', 'caption_filler', 'caption_punct', 'caption_spacing', 'caption_offset',
  'cap_x', 'cap_y', 'cap_outline', 'cap_shadow', 'title_style', 'title_font', 'title_size', 'title_x', 'title_y'] as const
export const FRAME_KEYS = ['layout', 'cam', 'cams', 'cam_height', 'game_zoom', 'game_x', 'track', 'track_target', 'focus_y', 'aspect'] as const
const pick = (o: RenderOpts, keys: readonly string[]) => Object.fromEntries(keys.map(k => [k, o[k]]))
/** Reset to default: how captions and titles look out of the box; positions stay. */
export const styleDefaults = () => Object.fromEntries(CAP_KEYS.filter(k => !['cap_x', 'cap_y', 'title_x', 'title_y'].includes(k))
  .map(k => [k, RENDER_DEFAULTS[k as keyof typeof RENDER_DEFAULTS]])) as Partial<RenderOpts>

function Range({ label, value, min, max, step = 1, out, onChange, title }: {
  label: string; value: number; min: number; max: number; step?: number; out: string; onChange: (v: number) => void; title?: string
}) {
  return (
    <label className="rv-range" title={title}>
      <span>{label}</span>
      <input type="range" min={min} max={max} step={step} value={value} onChange={e => onChange(Number(e.target.value))} />
      <output className="tnum">{out}</output>
    </label>
  )
}
function Check({ label, checked, onChange, title }: { label: string; checked: boolean; onChange: (v: boolean) => void; title?: string }) {
  return <label className="chk" title={title}><input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} /><span>{label}</span></label>
}
function Fonts({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <div className="rv-fonts" role="radiogroup" aria-label={label}>
      {Object.entries(FONTS).map(([k, f]) => (
        <button key={k} role="radio" aria-checked={value === k} className={value === k ? 'on' : ''} title={f.label}
                style={{ font: fontCss(k, ({ montserrat: 9.5, luckiest: 11, poppins: 12, classic: 11.5 } as Record<string, number>)[k] ?? 13 * f.scale) }}
                onClick={() => onChange(k)}>{f.label}</button>
      ))}
    </div>
  )
}
/** A name typed in a row instead of a browser prompt. */
function NameRow({ label, onSave, onCancel }: { label: string; onSave: (name: string) => void; onCancel: () => void }) {
  const [v, setV] = useState('')
  return (
    <form className="row wrap" onSubmit={e => { e.preventDefault(); if (v.trim()) onSave(v.trim()) }}>
      <input className="input grow" aria-label={label} placeholder={label} autoFocus value={v} onChange={e => setV(e.target.value)}
             onKeyDown={e => { if (e.key === 'Escape') onCancel() }} />
      <button className="btn sm" type="submit" disabled={!v.trim()}>Save</button>
      <button className="btn sm ghost" type="button" onClick={onCancel}>Cancel</button>
    </form>
  )
}
function Presets({ list, onUse, onDelete }: { list: Preset[]; onUse: (p: Preset) => void; onDelete?: (i: number) => void }) {
  if (!list.length) return <span className="muted small">None yet.</span>
  return (
    <div className="rv-presets">
      {list.map((p, i) => (
        <span key={p.name} className="rv-preset">
          <button className="btn sm" onClick={() => onUse(p)} title="Use this">{p.name}</button>
          {onDelete && <button className="ibtn" aria-label={`Delete ${p.name}`} onClick={() => onDelete(i)}>×</button>}
        </span>
      ))}
    </div>
  )
}
const Sec = ({ title, children }: { title: string; children: ReactNode }) => <section className="rv-sec"><h3 className="sub">{title}</h3>{children}</section>

const TITLE_STYLES: Array<[string, string]> = [['box', 'White box'], ['dark', 'Dark box'], ['accent', 'Color box'], ['outline', 'Outline']]
const CAP_STYLES: Array<[string, string]> = [['pop', 'Pop'], ['box', 'Highlight'], ['karaoke', 'Karaoke'], ['glow', 'Glow'], ['clean', 'Clean']]
const pct = (v: unknown) => `${Number(v)}%`

export function TextPanel({ opts, set, capPresets, whisper, guides, onGuides, onRelisten, onSavePresets }: {
  opts: RenderOpts; set: SetOpts; capPresets: Preset[]; whisper: boolean; guides: boolean; onGuides: (v: boolean) => void
  onRelisten: () => void; onSavePresets: (list: Preset[]) => Promise<void>
}) {
  const toast = useToast()
  const [naming, setNaming] = useState(false)
  const motion = String(opts.caption_motion || (opts.caption_anim ? 'pop' : 'none'))
  const off = Number(opts.caption_offset) || 0
  return (
    <div className="rv-pane" data-testid="rv-text">
      <Sec title="Title">
        <span className="hint">Drag it on the preview to move it.</span>
        <Seg label="Title style" value={String(opts.title_style)} options={TITLE_STYLES} onChange={v => set({ title_style: v })} />
        <Fonts label="Title font" value={String(opts.title_font)} onChange={v => set({ title_font: v })} />
        <Range label="Size" value={Number(opts.title_size)} min={60} max={150} step={5} out={pct(opts.title_size)} onChange={v => set({ title_size: v })} />
      </Sec>
      <Sec title="Captions">
        <Check label="Captions on the video" checked={!!opts.captions} onChange={v => set({ captions: v })} />
        {!whisper && <span className="hint">Captions need Whisper, which isn't available in this install.</span>}
        {opts.captions && <>
          <Seg label="Caption style" value={String(opts.caption_style)} options={CAP_STYLES} onChange={v => set({ caption_style: v })} />
          <Fonts label="Caption font" value={String(opts.caption_font)} onChange={v => set({ caption_font: v })} />
          <div className="rv-swatches" role="radiogroup" aria-label="Highlight colour">
            {SWATCHES.map(c => (
              <button key={c} role="radio" aria-checked={String(opts.caption_color).toLowerCase() === c} aria-label={`Highlight colour ${c}`}
                      className={String(opts.caption_color).toLowerCase() === c ? 'on' : ''} style={{ background: c }} onClick={() => set({ caption_color: c })} />
            ))}
            <input type="color" aria-label="Custom highlight colour" value={String(opts.caption_color)} onChange={e => set({ caption_color: e.target.value })} />
          </div>
          <Range label="Text size" value={Number(opts.caption_size)} min={70} max={140} step={5} out={pct(opts.caption_size)} onChange={v => set({ caption_size: v })} />
          <Range label="Words at once" value={Number(opts.words_per_line)} min={1} max={6} out={String(opts.words_per_line)} onChange={v => set({ words_per_line: v })} />
          <Range label="Outline" value={Number(opts.cap_outline ?? 100)} min={0} max={250} step={10} out={pct(opts.cap_outline ?? 100)} onChange={v => set({ cap_outline: v })} />
          <Range label="Shadow" value={Number(opts.cap_shadow ?? 100)} min={0} max={250} step={10} out={pct(opts.cap_shadow ?? 100)} onChange={v => set({ cap_shadow: v })} />
          <div className="fld">Templates
            <Presets list={CAP_TEMPLATES} onUse={p => { set({ ...p.opts, caption_anim: p.opts.caption_motion !== 'none' }); toast(`Using "${p.name}".`) }} />
          </div>
          <div className="fld">Saved styles
            <Presets list={capPresets} onUse={p => { set(p.opts); toast(`Using "${p.name}".`) }}
                     onDelete={i => void onSavePresets(capPresets.filter((_, j) => j !== i))} />
            {naming ? <NameRow label="Name this caption and title style" onCancel={() => setNaming(false)} onSave={async name => {
              await onSavePresets(capPresets.filter(p => p.name !== name).concat([{ name, opts: pick(opts, CAP_KEYS) }]))
              setNaming(false); toast(`Saved "${name}".`)
            }} /> : (
              <span className="row wrap">
                <button className="btn sm" onClick={() => setNaming(true)}>Save this style…</button>
                <button className="btn sm ghost" title="Captions and title back to how they look out of the box"
                        onClick={() => { set(styleDefaults()); toast('Caption and title style reset.') }}>Reset to default</button>
              </span>
            )}
          </div>
          <label className="fld inline">Position
            <select className="input" value={String(opts.caption_pos)} onChange={e => set({ caption_pos: e.target.value })}>
              <option value="auto">Automatic</option><option value="top">Top</option><option value="middle">Middle</option>
              <option value="bottom">Bottom</option><option value="custom">Where you dragged it</option>
            </select>
          </label>
          <label className="fld inline">Animation
            <select className="input" value={motion} onChange={e => set({ caption_motion: e.target.value, caption_anim: e.target.value !== 'none' })}>
              <option value="pop">Pop in (each line)</option><option value="word">Word pop (each word)</option><option value="bounce">Bounce</option>
              <option value="fade">Fade in</option><option value="slide">Slide up</option><option value="reveal">Word by word</option><option value="none">None</option>
            </select>
          </label>
          {motion !== 'none' && <Range label="Speed" value={Number(opts.caption_speed) || 100} min={50} max={200} step={10} out={pct(Number(opts.caption_speed) || 100)} onChange={v => set({ caption_speed: v })} />}
          <Range label="Line spacing" title="Space between the two lines of a long caption" value={Number(opts.caption_spacing) || 100} min={100} max={200} step={10}
                 out={pct(Number(opts.caption_spacing) || 100)} onChange={v => set({ caption_spacing: v })} />
          <Range label="Timing" title="Captions a little early or late compared with the voice? Minus is earlier, plus is later" value={off} min={-1000} max={1000} step={50}
                 out={`${off > 0 ? '+' : ''}${(off / 1000).toFixed(2)} s`} onChange={v => set({ caption_offset: v })} />
          <Check label="Highlight strong words" title='Words like "no way", "insane" and shouted words stay in the highlight colour' checked={!!opts.caption_emph} onChange={v => set({ caption_emph: v })} />
          <Check label='Keep "um" and "uh"' checked={opts.caption_filler !== false} onChange={v => set({ caption_filler: v })} />
          <Check label="Keep punctuation" checked={opts.caption_punct !== false} onChange={v => set({ caption_punct: v })} />
          <Check label="Capital letters" checked={!!opts.uppercase} onChange={v => set({ uppercase: v })} />
          <label className="fld inline">Accuracy
            <select className="input" value={String(opts.whisper_model)} onChange={e => { set({ whisper_model: e.target.value }); onRelisten() }}>
              <option value="base">Fast</option><option value="small">Balanced</option><option value="medium">Accurate (slow)</option><option value="large-v3">Best (GPU)</option>
            </select>
          </label>
          <label className="fld inline">Language
            <select className="input" value={String(opts.language)} onChange={e => { set({ language: e.target.value }); onRelisten() }}>
              {[['', 'Automatic'], ['en', 'English'], ['es', 'Spanish'], ['pt', 'Portuguese'], ['fr', 'French'], ['de', 'German'], ['it', 'Italian'],
                ['ar', 'Arabic'], ['tr', 'Turkish'], ['ru', 'Russian'], ['pl', 'Polish'], ['ja', 'Japanese'], ['ko', 'Korean']].map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
        </>}
      </Sec>
      <div className="row wrap">
        <button className="btn sm" onClick={() => { set({ title_x: RENDER_DEFAULTS.title_x, title_y: RENDER_DEFAULTS.title_y, cap_x: 0.5, cap_y: 0.7, caption_pos: 'auto' }); toast('Title and captions are back in their usual places.') }}>Reset positions</button>
        <Check label="Show what TikTok covers" checked={guides} onChange={onGuides} />
      </div>
      <p className="hint">Style changes apply to clips you keep from now on.</p>
    </div>
  )
}

export function LayoutPanel({ opts, set, pid, frameAt, framePresets, trackNote, onSavePresets }: {
  opts: RenderOpts; set: SetOpts; pid: string; frameAt: number; framePresets: Preset[]; trackNote: string
  onSavePresets: (list: Preset[]) => Promise<void>
}) {
  const toast = useToast()
  const [naming, setNaming] = useState(false)
  const [autoNote, setAutoNote] = useState('')
  const [autoBusy, setAutoBusy] = useState(false)
  const lay = layoutOf(opts), rects = camRects(opts)
  // which facecam the box you drag is for (Cam 1 is `cam`, the others are `cams`)
  const [camAt, setCamAt] = useState(0)
  const ci = Math.min(camAt, rects.length - 1), cam = rects[ci]!
  const setCam = (box: number[], save = true) => ci === 0 ? set({ cam: box }, save)
    : set({ cams: rects.slice(1).map((r, i) => (i === ci - 1 ? box : r)) }, save)
  function addCam() {
    // a new box in a corner the others don't use, to drag onto the next camera
    const spots = [[0.75, 0, 0.25, 0.25], [0, 0.75, 0.25, 0.25], [0.75, 0.75, 0.25, 0.25], [0, 0, 0.25, 0.25]]
    const free = spots.find(s => !rects.some(r => Math.abs(r[0]! - s[0]!) < 0.1 && Math.abs(r[1]! - s[1]!) < 0.1)) ?? spots[0]!
    set({ cams: [...rects.slice(1), free], layout_auto: false })
    setCamAt(rects.length)
  }
  function removeCam() {
    const left = rects.filter((_, i) => i !== ci)
    set({ cam: left[0]!, cams: left.slice(1), layout_auto: false })
    setCamAt(Math.max(0, ci - 1))
  }
  const picker = useRef<HTMLDivElement>(null)
  const drag = useRef<{ x: number; y: number; prev: number[] }>(undefined)
  const rel = (e: PointerEvent) => {
    const r = picker.current!.getBoundingClientRect()
    return [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))] as const
  }
  async function autoFrame() {
    setAutoBusy(true); setAutoNote('Looking for faces…')
    try {
      const r = await api(FramingSuggest, `/api/framing/${pid}`)
      if (!r.layout) { setAutoNote(r.reason || 'Nothing to suggest.'); return }
      set({ layout: r.layout, layout_auto: true, ...(r.cam ? { cam: r.cam, cams: [] } : {}), ...(r.track ? { track: r.track } : {}),
        ...(r.layout === 'split' && opts.aspect === '16:9' ? { aspect: '9:16' } : {}) })
      setAutoNote(r.reason + (r.confidence < 0.6 ? ' (not sure: check it)' : ''))
    } catch (e) { setAutoNote(e instanceof Error ? e.message : String(e)) }
    finally { setAutoBusy(false) }
  }
  const following = lay === 'full' && opts.track !== 'off'
  return (
    <div className="rv-pane" data-testid="rv-layout">
      <div className="fld">Shape
        <Seg label="Shape" value={String(opts.aspect || '9:16')} onChange={v => set({ aspect: v })}
             options={[['9:16', 'Vertical 9:16'], ['1:1', 'Square 1:1'], ['16:9', 'Wide 16:9']]} />
        {opts.aspect === '16:9' && opts.layout === 'split' && <span className="hint">Cam + game needs a tall frame, so a wide clip uses the whole frame.</span>}
      </div>
      <div className="fld">Layout
        <Seg label="Layout" value={String(opts.layout)} onChange={v => set({ layout: v, layout_auto: false })}
             options={[['split', 'Cam + game'], ['full', 'Whole frame'], ['cam', 'Cam only'], ['blur', 'Blur fit'], ['square', 'Square blur']]} />
        <span className="row wrap"><button className="btn sm" disabled={autoBusy} onClick={() => void autoFrame()}
              title="Look for a facecam or a person and pick the layout that fits">Auto-frame</button><span className="hint">{autoNote}</span></span>
      </div>
      {lay === 'full' && (
        <div className="fld">
          <Check label="Follow the streamer's face" checked={opts.track !== 'off'} onChange={v => set({ track: v ? 'auto' : 'off' })} />
          <label className="fld inline">Who to follow
            <select className="input" disabled={opts.track === 'off'} value={String(opts.track_target || 'auto')} onChange={e => set({ track_target: e.target.value })}>
              <option value="auto">Automatic</option><option value="largest">Biggest face</option><option value="left">Left-most person</option>
              <option value="right">Right-most person</option><option value="point">Nearest to the focus point</option>
            </select>
          </label>
          <span className="hint" data-testid="rv-track-note">{trackNote || 'Smooth, and it holds still while the face is hidden. If no face is found, the crop stays at the focus point.'}</span>
        </div>
      )}
      {(lay === 'split' || lay === 'cam') && (
        <div className="fld">Facecam
          <div ref={picker} className="rv-picker" data-testid="rv-picker"
               onPointerDown={e => { const [x, y] = rel(e); drag.current = { x, y, prev: cam.slice() }; picker.current!.setPointerCapture?.(e.pointerId) }}
               onPointerMove={e => {
                 const d = drag.current
                 if (!d) return
                 const [x, y] = rel(e)
                 setCam([Math.min(d.x, x), Math.min(d.y, y), Math.abs(x - d.x), Math.abs(y - d.y)], false)
               }}
               onPointerUp={() => {
                 const d = drag.current
                 drag.current = undefined
                 if (!d) return
                 if (cam[2]! < 0.03 || cam[3]! < 0.03) setCam(d.prev)
                 else set({ layout_auto: false })          // you drew the camera box: kept for the next video too
               }}>
            <img src={`/api/frame/${pid}?t=${Math.round(frameAt)}`} alt="A frame from the stream" draggable={false} />
            {rects.map((r, i) => (
              <div key={i} className={`rv-cambox${i === ci ? '' : ' other'}`} data-testid="rv-cambox"
                   style={{ left: `${r[0]! * 100}%`, top: `${r[1]! * 100}%`, width: `${r[2]! * 100}%`, height: `${r[3]! * 100}%` }}>
                {rects.length > 1 && <span>{i + 1}</span>}
              </div>
            ))}
          </div>
          <span className="row wrap" data-testid="rv-cams">
            {rects.length > 1 && rects.map((_, i) => (
              <button key={i} className={`btn sm${i === ci ? ' on' : ''}`} aria-pressed={i === ci} onClick={() => setCamAt(i)}>Cam {i + 1}</button>
            ))}
            {rects.length < MAX_CAMS && <button className="btn sm" data-testid="rv-add-cam" onClick={addCam}>Add a facecam</button>}
            {rects.length > 1 && <button className="btn sm ghost" data-testid="rv-remove-cam" onClick={removeCam}>Remove cam {ci + 1}</button>}
          </span>
          <span className="hint">{rects.length > 1
            ? `Drag a box around camera ${ci + 1}. ${lay === 'split' ? 'The cameras sit side by side above the game.' : 'The cameras are stacked, one above the other.'}`
            : 'Drag a box around your camera. Two people on camera? Add a facecam for each.'}</span>
          {lay === 'split' && <Range label="Camera height" value={Math.round(Number(opts.cam_height) * 100)} min={20} max={50}
                                     out={`${Math.round(Number(opts.cam_height) * 100)}%`} onChange={v => set({ cam_height: v / 100 })} />}
        </div>
      )}
      <Range label={following ? 'Focus (when no face)' : 'Focus'} value={Math.round(Number(opts.game_x) * 100)} min={0} max={100}
             out={Number(opts.game_x) < 0.45 ? 'Left' : Number(opts.game_x) > 0.55 ? 'Right' : 'Center'} onChange={v => set({ game_x: v / 100 })} />
      {following && opts.track_target === 'point' && (
        <Range label="Focus height" value={Math.round(Number(opts.focus_y ?? 0.4) * 100)} min={0} max={100}
               out={Number(opts.focus_y) < 0.35 ? 'High' : Number(opts.focus_y) > 0.6 ? 'Low' : 'Middle'} onChange={v => set({ focus_y: v / 100 })} />
      )}
      <Range label="Zoom" value={Math.round(Number(opts.game_zoom) * 100)} min={100} max={200} step={5} out={`${+Number(opts.game_zoom).toFixed(2)}×`}
             onChange={v => set({ game_zoom: v / 100 })} />
      <div className="fld">Saved framings
        <Presets list={framePresets} onUse={p => { set(p.opts); toast(`Using "${p.name}".`) }}
                 onDelete={i => void onSavePresets(framePresets.filter((_, j) => j !== i))} />
        {naming ? <NameRow label="Name this framing" onCancel={() => setNaming(false)} onSave={async name => {
          await onSavePresets(framePresets.filter(p => p.name !== name).concat([{ name, opts: pick(opts, FRAME_KEYS) }]))
          setNaming(false); toast(`Saved "${name}".`)
        }} /> : <span><button className="btn sm" onClick={() => setNaming(true)}>Save this framing…</button></span>}
      </div>
      <Check label="Use the GPU to encode" checked={!!opts.hw_encode} onChange={v => set({ hw_encode: v })} />
      <p className="hint">Changes show in the preview right away and apply to clips you keep from now on.</p>
    </div>
  )
}

export const savePresets = (key: 'cap_presets' | 'frame_presets') => (list: Preset[]) => api(ExportFormat, '/api/export', { [key]: list }).then(() => {})
