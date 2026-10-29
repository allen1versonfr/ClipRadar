import { useState } from 'react'
import type { Clip, Safety } from '../../api/schemas'
import { ago, plural } from '../../format'
import { FIX_EXPLAIN, FIX_LABEL, FLAG_STATE, fixesOf, flagsOf, isStale, logOf } from './checks'
import { fmtT } from './captions'
import { CHECK_LABEL, checkStatus, fmt } from './model'

export interface Proposal { fp: string; kind: string; view: Record<string, unknown>; span?: [number, number] | undefined; playing: boolean }

/** A clip's content check: its flags and what you decide about them (leave, fix, dismiss, exclude),
 *  as in the original page. Clip Radar's own check, not TikTok's decision. */
export function ContentCheck({ clip: c, safety, flaggedTab, proposal, onAct, onPropose, onPreview, onCancel, onApply, onSeek, onEdit, onRemove }: {
  clip: Clip; safety: Pick<Safety, 'enabled' | 'auto' | 'notify'> | undefined; flaggedTab: boolean; proposal: Proposal | null
  onAct: (action: string, args?: Record<string, unknown>) => void
  onPropose: (fp: string, kind: string) => void; onPreview: () => void; onCancel: () => void; onApply: () => void
  onSeek: (t: number) => void; onEdit: () => void; onRemove: () => void
}) {
  const [show, setShow] = useState(false)
  const st = checkStatus(c, safety?.enabled !== false), kept = c.state === 'kept', stale = isStale(c)
  const flags = flagsOf(c), fixes = fixesOf(c), log = logOf(c)
  const lvl = c.safety?.level
  const tone = st === 'needs_review' ? (lvl === 'high' ? 'high' : lvl === 'medium' ? 'medium' : 'low') : st === 'clean' || st === 'corrected' ? 'ok' : st === 'excluded' ? 'medium' : 'low'
  const checking = st === 'unchecked' && !c.listened && safety?.auto !== false
  const quiet = st === 'off' || (st === 'clean' && !stale && !fixes.length && !log.length) || checking
  const summary = (
    <span className={`safe ${tone}`} data-testid="cc-status">
      {checking ? 'Checking…' : `${CHECK_LABEL[st]}${st === 'needs_review' ? ` · ${plural(c.safety?.open ?? 0, 'flag')}` : ''}${stale ? ' · changed since' : ''}`}
    </span>
  )
  if (quiet) return <div className="cc cc-quiet">Content: {summary}</div>
  if (safety?.notify === 'later' && !flaggedTab && st === 'needs_review' && !show) {
    return <div className="cc">{summary} <span className="muted">{plural(c.safety?.open ?? 0, 'flag')} waiting in the Flagged tab.</span> <button className="link" onClick={() => setShow(true)}>Review now</button></div>
  }
  const fps = new Set(flags.map(f => f.fp)), extra = fixes.filter(fx => !fps.has(fx.fp))
  return (
    <section className="cc" aria-label="Content check" data-testid="content-check">
      <div className="cc-head"><b>Content check</b> {summary}<span className="muted small">Clip Radar's own check, not TikTok's decision</span></div>
      {st === 'unchecked' && <div className="row wrap"><span>This clip hasn't been checked.</span><button className="btn sm" onClick={() => onAct('')}>Check now</button></div>}
      {stale && <div className="row wrap"><span>The clip changed since it was checked.</span><button className="btn sm" onClick={() => onAct('')}>Check again</button></div>}
      {st === 'excluded' && <p className="notice">Excluded from export. It stays here and won't render until you include it again.</p>}
      {(flags.length > 0 || extra.length > 0) && (
        <ul className="cc-flags">
          {flags.map(f => {
            const state = f.status === 'corrected' ? `Fixed: ${(FIX_LABEL[f.fixed ?? ''] ?? 'fixed').toLowerCase()}` : FLAG_STATE[f.status] ?? ''
            return (
              <li key={f.fp} className={`cc-flag ${f.status} sev${f.severity ?? 1}`}>
                <div className="cc-flag-h">
                  <b>{f.label}</b><span className="cc-text">“{f.text}”</span>
                  {f.source === 'speech'
                    ? <button className="link tnum" title="Play from here" onClick={() => onSeek(c.start + (f.start ?? 0) - 1)}>{fmtT(Math.max(0, f.start ?? 0))}</button>
                    : <span className="muted">{f.source === 'title' ? 'in the title' : 'in the post caption'}</span>}
                  {state && <span className="muted small">{state}</span>}
                </div>
                {!kept && (
                  <div className="row wrap">
                    {f.status === 'open' && <>
                      {(f.fixes ?? []).map(k => <button key={k} className="btn sm" onClick={() => onPropose(f.fp, k)}>{FIX_LABEL[k]}</button>)}
                      <button className="btn sm ghost" title="Not a problem, or a false alarm" onClick={() => onAct('dismiss', { fp: f.fp })}>Dismiss</button>
                    </>}
                    {f.status === 'corrected' && <button className="btn sm ghost" onClick={() => onAct('undo', { fix: f.fix_id ?? '' })}>Undo fix</button>}
                    {f.status !== 'open' && f.status !== 'corrected' && <button className="btn sm ghost" onClick={() => onAct('reopen', { fp: f.fp })}>Undo</button>}
                  </div>
                )}
                {proposal && proposal.fp === f.fp && (
                  <div className="cc-prop" data-testid="cc-proposal">
                    <p>{FIX_EXPLAIN[proposal.kind]}</p>
                    {(proposal.kind === 'cut_end' || proposal.kind === 'cut_start') && typeof proposal.view.start === 'number' && typeof proposal.view.end === 'number' &&
                      <p>New length {fmt(proposal.view.end - proposal.view.start)} (was {fmt(c.end - c.start)}), {fmt(proposal.view.start)} to {fmt(proposal.view.end)}.</p>}
                    {proposal.kind === 'mask_title' && <p>New title: “{String(proposal.view.title ?? '')}”</p>}
                    {proposal.kind === 'mask_post' && <p>New caption starts: “{String(proposal.view.postText ?? '').slice(0, 90)}”</p>}
                    <p className="hint">You can undo it. It lowers the risk but doesn't guarantee TikTok will allow the clip.</p>
                    <div className="row wrap">
                      {proposal.kind !== 'mask_post' && <button className="btn sm" onClick={onPreview}>{proposal.playing ? 'Stop preview' : 'Preview'}</button>}
                      <span className="grow" />
                      <button className="btn sm ghost" onClick={onCancel}>Cancel</button>
                      <button className="btn sm primary" onClick={onApply}>{FIX_LABEL[proposal.kind]}</button>
                    </div>
                  </div>
                )}
              </li>
            )
          })}
          {extra.map(fx => (
            <li key={fx.id} className="cc-flag corrected">
              <div className="cc-flag-h"><span className="cc-text">{fx.flag ?? 'Flag'}</span><span className="muted small">Fixed: {(FIX_LABEL[fx.kind] ?? '').toLowerCase()}</span></div>
              {!kept && <div className="row"><button className="btn sm ghost" onClick={() => onAct('undo', { fix: fx.id })}>Undo fix</button></div>}
            </li>
          ))}
        </ul>
      )}
      <div className="row wrap">
        {!kept && (c.safety?.open ?? 0) > 0 && <button className="btn sm" title="Keep the clip exactly as it is" onClick={() => onAct('leave')}>Leave as is</button>}
        {!kept && flags.some(f => f.source === 'speech' && f.status !== 'corrected') && <button className="btn sm" title="Jump to the flagged words in the Captions tab" onClick={onEdit}>Review and edit</button>}
        {c.check?.excluded
          ? <button className="btn sm" onClick={() => onAct('exclude', { on: false })}>Include in export</button>
          : <button className="btn sm" title="Keep it out of rendering without deleting it" onClick={() => onAct('exclude', { on: true })}>Exclude from export</button>}
        <button className="btn sm ghost danger-text" title="Take it out of this project" onClick={onRemove}>Remove…</button>
      </div>
      {flags.some(f => f.status === 'dismissed' || f.status === 'accepted') &&
        <button className="link" onClick={() => onAct('recheck')}>Check again from scratch (brings back dismissed flags)</button>}
      {log.length > 0 && (
        <details className="cc-log"><summary>History ({log.length})</summary>
          <ul>{log.slice().reverse().map((l, i) => <li key={i}><span className="muted">{ago(l.at)}</span> {l.text}</li>)}</ul>
        </details>
      )}
      {kept && <p className="hint">This clip is in the render queue. Remove it from the queue to change it.</p>}
    </section>
  )
}
