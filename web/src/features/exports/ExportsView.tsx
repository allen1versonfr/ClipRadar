import { useCallback, useState } from 'react'
import { api } from '../../api/client'
import { Campaigns, Ok, Queue, QueueCleared, type QueueItem } from '../../api/schemas'
import { handoffCell } from './postStatus'
import { SubmitPanel, TikTokPanel, useCopy } from './Posting'
import { usePoll } from '../../hooks/usePoll'
import { useNow } from '../../hooks/useNow'
import { useConfirm } from '../../components/Confirm'
import { useToast } from '../../components/Toasts'
import { attemptLine, statusText, summary } from './renderStatus'
import { fmtTime } from './renderStatus'
import { getPref, setPref } from '../../prefs'

/** The Exports preview plays at the level you set for the Review preview (vc_volume, vc_muted), and its own
 *  controls set that same level, as in the original page. */
function atPreviewLevel(v: HTMLVideoElement | null) {
  if (!v) return
  v.volume = Math.min(1, Math.max(0, Number(getPref('volume', 1)) || 0))
  v.muted = !!getPref('muted', false)
}

const loadQueue = () => api(Queue, '/api/queue')

/** Which video a render came from: its project, or its title for items saved without one. */
export const sourceKey = (q: QueueItem) => q.pid || q.source_title || ''

/** Exports › Source: each video in the queue, with how many of its clips are there, in the order they
 *  were first queued. Clipping several videos at once no longer mixes their clips in one list. */
export function sources(items: QueueItem[]): Array<{ key: string; label: string; count: number }> {
  const out = new Map<string, { key: string; label: string; count: number }>()
  for (const q of items) {
    const k = sourceKey(q)
    const s = out.get(k) ?? { key: k, label: q.source_title || 'Untitled video', count: 0 }
    s.count++
    out.set(k, s)
  }
  return [...out.values()]
}

export function ExportsView() {
  const queue = usePoll(loadQueue, 1500)
  const all = queue.data?.items ?? []
  const srcs = sources(all)
  const [srcPick, setSrcPick] = useState<string>(() => getPref('exportsSource', ''))
  // a source that has left the queue shows everything again
  const src = srcs.some(s => s.key === srcPick) ? srcPick : ''
  const items = src ? all.filter(q => sourceKey(q) === src) : all
  const active = items.some(q => q.status === 'queued' || q.status === 'rendering')
  const now = useNow(1000, active)
  const [selId, setSelId] = useState<string>()
  const toast = useToast()
  const confirm = useConfirm()
  // newest first, like the original page
  const rows = items.slice().reverse()
  const sel = rows.find(q => q.id === selId) ?? rows[0]
  const failed = items.filter(q => q.status === 'error').length
  const camps = usePoll(useCallback(() => api(Campaigns, '/api/campaigns'), []), 30_000).data
  const campaign = camps?.list.find(c => c.id === camps.active)

  /** Exports › Clear queue: the list emptied (files stay), waiting renders cancelled, the one rendering left to finish. */
  async function clearQueue() {
    const waiting = items.filter(q => q.status === 'queued').length
    const running = items.filter(q => q.status === 'rendering').length
    const what = src ? `this video's ${items.length} clip${items.length === 1 ? '' : 's'}` : `all ${items.length} clip${items.length === 1 ? '' : 's'}`
    const body = [`Takes ${what} off the list. Rendered files stay in your clips folder.`,
      waiting ? `${waiting} waiting to render ${waiting === 1 ? 'is' : 'are'} cancelled.` : '',
      running ? `${running === 1 ? 'The clip' : `The ${running} clips`} rendering now ${running === 1 ? 'finishes' : 'finish'}; cancel ${running === 1 ? 'it' : 'them'} with × if you don't want ${running === 1 ? 'it' : 'them'}.` : ''].filter(Boolean).join(' ')
    if (!await confirm({ title: 'Clear the queue?', body, ok: 'Clear queue', danger: waiting > 0 })) return
    try {
      const r = await api(QueueCleared, '/api/queue/clear', src ? { pid: src } : {})
      toast(`Cleared ${r.removed} clip${r.removed === 1 ? '' : 's'}.`)
      setSelId(undefined)
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), 'err')
    }
    await queue.refresh()
  }

  async function act(path: string, body: object, done?: string) {
    try {
      await api(Ok, path, body)
      if (done) toast(done)
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), 'err')
    }
    await queue.refresh()
  }

  return (
    <section className="exports" aria-labelledby="ex-title">
      <header className="view-head">
        <h1 id="ex-title">Exports</h1>
        <span className="muted" data-testid="exports-summary">{summary(items, queue.data?.eta ?? null)}</span>
        {failed > 0 && (
          <button className="btn" data-testid="retry-all" onClick={() => act('/api/queue/retry', src ? { id: 'all', pid: src } : { id: 'all' }, 'Rendering again…')}>
            {failed > 1 ? `Retry ${failed} failed` : 'Retry failed'}
          </button>
        )}
        {srcs.length > 1 && (
          <label className="ex-source">Source
            <select className="input" data-testid="exports-source" value={src}
                    onChange={e => { setSrcPick(e.target.value); setPref('exportsSource', e.target.value); setSelId(undefined) }}>
              <option value="">All sources ({all.length})</option>
              {srcs.map(s => <option key={s.key} value={s.key}>{s.label} ({s.count})</option>)}
            </select>
          </label>
        )}
        {items.length > 0 && <button className="btn ghost" data-testid="clear-queue" onClick={() => void clearQueue()}>Clear queue</button>}
        <button className="btn ghost" onClick={() => act('/api/open_folder', { folder: 'root' })}>Open clips folder</button>
      </header>
      {campaign && <a className="notice" href="#settings/campaigns" data-testid="ex-campaign">Campaign: {campaign.name}. Clips you keep now follow its rules. Change it in Settings › Campaigns.</a>}
      {queue.error && <p className="banner err" role="alert">{queue.error}</p>}
      {!queue.data && !queue.error && <p className="muted pad">Loading…</p>}
      {queue.data && !items.length && (
        <p className="empty" data-testid="exports-empty">Nothing rendered yet. Keep a clip in Review and it renders here.</p>
      )}
      {items.length > 0 && (
        <div className="ex-split">
          <table className="ex-table" data-testid="exports-table">
            <thead>
              <tr><th aria-label="Thumbnail" /><th>Clip</th><th className="r">Length</th><th>Status</th><th>Posted</th><th aria-label="Remove" /></tr>
            </thead>
            <tbody>
              {rows.map(q => (
                <Row key={q.id} q={q} now={now} selected={q.id === sel?.id} onSelect={() => setSelId(q.id)}
                     onRemove={() => act('/api/queue/remove', { id: q.id })}
                     onRetry={() => act('/api/queue/retry', { id: q.id }, 'Rendering again…')} />
              ))}
            </tbody>
          </table>
          {sel && <Detail q={sel} campaigns={camps} onChanged={queue.refresh}
                          onRetry={() => act('/api/queue/retry', { id: sel.id }, 'Rendering again…')}
                          onRemove={() => act('/api/queue/remove', { id: sel.id })}
                          onShow={() => act('/api/open_folder', { folder: sel.folder ?? 'root' })} />}
        </div>
      )}
    </section>
  )
}

interface RowProps {
  q: QueueItem
  now: number
  selected: boolean
  onSelect: () => void
  onRemove: () => void
  onRetry: () => void
}

function Row({ q, now, selected, onSelect, onRemove, onRetry }: RowProps) {
  const st = statusText(q, now)
  const removeLabel = q.status === 'done' ? 'Remove from list (the file stays)' : q.status === 'error' ? 'Remove' : 'Cancel'
  return (
    <tr className={selected ? 'sel' : ''} data-testid="export-row" data-id={q.id} data-status={q.status}
        onClick={onSelect} aria-selected={selected}>
      <td><span className="vt">{q.thumb && <img src={q.thumb} alt="" />}</span></td>
      <td>
        <div className="t1">{q.title || `Clip ${q.rank ?? ''}`.trim()}</div>
        <div className="t2">{q.source_title}</div>
      </td>
      <td className="r tnum">{fmtTime(q.duration)}</td>
      <td>
        <div className="st-cell">
          <span className={`st ${q.status}`} data-testid="export-status">{st.label}</span>
          {q.status === 'rendering' && (
            st.fraction === null
              ? <span className="meter busy" role="progressbar" aria-label={st.label}><i /></span>
              : <span className="meter" role="progressbar" aria-valuemin={0} aria-valuemax={100}
                      aria-valuenow={Math.round(st.fraction * 100)} aria-label={st.label}>
                  <i style={{ width: `${Math.round(st.fraction * 100)}%` }} />
                </span>
          )}
          {q.status === 'error' && <span className="why" title={q.error}>{q.error}</span>}
          {q.warning && q.status !== 'error' && <span className="why warn" title={q.warning}>{q.warning}</span>}
          {(q.status === 'error' || q.captions_skipped) && (
            <button className="btn sm" data-testid="export-retry" onClick={e => { e.stopPropagation(); onRetry() }}>Retry</button>
          )}
        </div>
      </td>
      <td>
        {(() => { const h = handoffCell(q); return <span className={`tt-cell ${h.tone}`} title={h.title}>{h.text}</span> })()}
        {q.discord && !q.discord.error && <span className="tt-cell ok" title={`Sent to ${q.campaign?.name ?? 'the campaign'}'s Discord`}>Discord ✓</span>}
      </td>
      <td>
        <button className="ibtn" aria-label={removeLabel} title={removeLabel}
                onClick={e => { e.stopPropagation(); onRemove() }}>×</button>
      </td>
    </tr>
  )
}

function Detail({ q, campaigns, onChanged, onRetry, onRemove, onShow }: {
  q: QueueItem; campaigns: Campaigns | undefined; onChanged: () => Promise<void>; onRetry: () => void; onRemove: () => void; onShow: () => void
}) {
  const [more, setMore] = useState(false)
  const copy = useCopy()
  const attempts = q.history.map(attemptLine).join('\n')
  const details = [q.detail && q.detail !== q.error ? q.detail : '', attempts].filter(Boolean).join('\n\n')
  // a clip that came out with a warning (a layout that fell back): what failed first, for a bug report
  const ff = q.history.at(-1)?.first_failure
  const firstFailure = ff ? `${ff.layout} failed, exit ${ff.exit}:\n${ff.reason}\n\nFilter graph:\n${ff.filter}` : ''
  const body = (
    <>
      <div className="phone">
        {q.status === 'done' && q.url
          ? <video key={q.url} src={q.url} controls preload="metadata" data-testid="ex-video" ref={atPreviewLevel}
                   onVolumeChange={e => { setPref('volume', e.currentTarget.volume); setPref('muted', e.currentTarget.muted) }} />
          : <div className="phone-empty">{q.status === 'error' ? 'Not rendered' : q.status === 'queued' ? 'Waiting to render' : 'Rendering…'}</div>}
      </div>
      <div className="file">{q.name ?? ''}</div>
      <div className="row wrap">
        {q.status === 'done' && <button className="btn sm" onClick={onShow}>Show in folder</button>}
        {(q.post || q.title) && <button className="btn sm" onClick={() => void copy(q.post || q.title)}>Copy caption</button>}
        {q.link && <button className="btn sm" title="A link to the original video at this moment, to credit the source"
                           onClick={() => void copy(q.link!, 'Source link')}>Copy source link</button>}
        <button className="btn sm ghost" onClick={onRemove}>{q.status === 'done' ? 'Remove from list' : q.status === 'error' ? 'Remove' : 'Cancel'}</button>
      </div>
      {q.status === 'done' && q.warning && firstFailure && (
        <div className="row">
          <button className="btn ghost" aria-expanded={more} onClick={() => setMore(!more)}>{more ? 'Hide details' : 'Details'}</button>
          {more && <pre className="details" data-testid="export-details">{`${attempts}\n\n${firstFailure}`}</pre>}
        </div>
      )}
      {q.status === 'error' && (
        <div className="err-box" role="alert">
          <p data-testid="export-error">{q.error}</p>
          {q.retryable === false && <p className="muted">Retrying won't help until that's fixed.</p>}
          <div className="row">
            <button className="btn primary" onClick={onRetry}>Retry</button>
            {details && <button className="btn ghost" aria-expanded={more} onClick={() => setMore(!more)}>{more ? 'Hide details' : 'Details'}</button>}
          </div>
          {more && <pre className="details" data-testid="export-details">{details}</pre>}
        </div>
      )}
      <SubmitPanel q={q} campaigns={campaigns} onChanged={onChanged} />
    </>
  )
  return (
    <aside className="ex-detail" data-testid="export-detail" aria-label="Selected clip">
      {/* a rendered clip: its TikTok buttons at the top, the caption they use at the bottom */}
      {q.status === 'done' ? <TikTokPanel q={q} onChanged={onChanged}>{body}</TikTokPanel> : body}
    </aside>
  )
}
