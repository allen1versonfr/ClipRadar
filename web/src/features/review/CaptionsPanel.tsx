import { useEffect, useState } from 'react'
import type { Clip } from '../../api/schemas'
import type { RenderOpts } from '../../prefs'
import { fmtT } from './captions'
import { chunksFor } from './draw'

/** Review › Captions: each line of the clip's captions, to fix what Whisper heard. Click a line's time to
 *  jump there; type to fix it, then Enter (Escape puts it back); × deletes it. */
export function CaptionsPanel({ clip, opts, whisper, now, onEdit, onAdd, onReset, onSeek }: {
  clip: Clip; opts: RenderOpts; whisper: boolean; now: number
  onEdit: (i: number, text: string) => void; onAdd: () => void; onReset: () => void; onSeek: (t: number) => void
}) {
  const kept = clip.state === 'kept'
  const rows = chunksFor(clip, opts)
  const flags = (clip.safety?.flags ?? []) as Array<{ source?: string; start?: number; end?: number; label?: string }>
  const flagFor = (r: { start: number; end: number }) => flags.find(f => f.source === 'speech' && (f.start ?? 0) + clip.start < r.end && (f.end ?? 0) + clip.start > r.start)
  const active = rows.findIndex(r => now >= r.start && now < r.end)
  const empty = !opts.captions ? 'Captions are turned off. Turn them on in the Text tab.'
    : rows.length ? '' : !clip.listened && whisper ? 'Listening to this clip… its captions will show up here in a moment.'
    : whisper ? 'No speech found in this clip. Use Add caption to write your own.'
    : "Captions need Whisper, which isn't available in this install. You can still add captions by hand."
  return (
    <div className="rv-pane" data-testid="rv-captions">
      <span className="hint">Click a time to jump to it. Type to fix a word, then press Enter.</span>
      {empty ? <p className="muted small">{empty}</p> : (
        <ol className="rv-caplines">
          {rows.map((r, i) => {
            const f = flagFor(r), text = r.words.map(w => w.word).join(' ')
            return (
              <li key={`${r.start}-${text}`} className={`${i === active ? 'on' : ''}${r.words.some(w => (w as { edited?: boolean }).edited) ? ' edited' : ''}${f ? ' flag' : ''}`}
                  title={f ? `Content check: ${f.label}` : undefined}>
                <button className="link tnum" onClick={() => onSeek(r.start + 0.01)} aria-label={`Jump to ${fmtT(r.start - clip.start)}`}>{fmtT(r.start - clip.start)}</button>
                <Line text={text} disabled={kept} label={`Caption at ${fmtT(r.start - clip.start)}`} onFocus={() => onSeek(r.start + 0.01)}
                      onCommit={v => { if (v.trim() !== text) onEdit(i, v) }} />
                {!kept && <button className="ibtn" aria-label={`Delete the caption at ${fmtT(r.start - clip.start)}`} onClick={() => onEdit(i, '')}>×</button>}
              </li>
            )
          })}
        </ol>
      )}
      <div className="row wrap">
        <button className="btn sm" disabled={kept || !opts.captions} onClick={onAdd}>+ Add caption at playhead</button>
        <span className="grow" />
        {clip.wordsEdited && !kept && <button className="btn sm ghost" onClick={onReset}>Undo all edits</button>}
      </div>
    </div>
  )
}

function Line({ text, disabled, label, onFocus, onCommit }: { text: string; disabled: boolean; label: string; onFocus: () => void; onCommit: (v: string) => void }) {
  const [v, setV] = useState(text)
  useEffect(() => setV(text), [text])
  return (
    <input className="input" value={v} disabled={disabled} spellCheck aria-label={label} onFocus={onFocus}
           onChange={e => setV(e.target.value)} onBlur={() => onCommit(v)}
           onKeyDown={e => {
             if (e.key === 'Enter') { e.preventDefault(); e.currentTarget.blur() }
             else if (e.key === 'Escape') { setV(text); requestAnimationFrame(() => (e.target as HTMLInputElement).blur()) }
           }} />
  )
}
