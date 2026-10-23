import { useState } from 'react'
import { api } from '../../api/client'
import { Ok, Queue, type QueueItem } from '../../api/schemas'
import { usePoll } from '../../hooks/usePoll'
import { useNow } from '../../hooks/useNow'
import { useToast } from '../../components/Toasts'
import { attemptLine, statusText, summary } from './renderStatus'
import { fmtTime } from './renderStatus'

const loadQueue = () => api(Queue, '/api/queue')

export function ExportsView() {
  const queue = usePoll(loadQueue, 1500)
  const items = queue.data?.items ?? []
  const active = items.some(q => q.status === 'queued' || q.status === 'rendering')
  const now = useNow(1000, active)
  const [selId, setSelId] = useState<string>()
  const toast = useToast()
  // newest first, like the original page
  const rows = items.slice().reverse()
  const sel = rows.find(q => q.id === selId) ?? rows[0]
  const failed = items.filter(q => q.status === 'error').length

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
          <button className="btn" data-testid="retry-all" onClick={() => act('/api/queue/retry', { id: 'all' }, 'Rendering again…')}>
            {failed > 1 ? `Retry ${failed} failed` : 'Retry failed'}
          </button>
        )}
        <button className="btn ghost" onClick={() => act('/api/open_folder', { folder: 'root' })}>Open clips folder</button>
      </header>
      {queue.error && <p className="banner err" role="alert">{queue.error}</p>}
      {!queue.data && !queue.error && <p className="muted pad">Loading…</p>}
      {queue.data && !items.length && (
        <p className="empty" data-testid="exports-empty">Nothing rendered yet. Keep a clip in Review and it renders here.</p>
      )}
      {items.length > 0 && (
        <div className="ex-split">
          <table className="ex-table" data-testid="exports-table">
            <thead>
              <tr><th aria-label="Thumbnail" /><th>Clip</th><th className="r">Length</th><th>Status</th><th aria-label="Remove" /></tr>
            </thead>
            <tbody>
              {rows.map(q => (
                <Row key={q.id} q={q} now={now} selected={q.id === sel?.id} onSelect={() => setSelId(q.id)}
                     onRemove={() => act('/api/queue/remove', { id: q.id })}
                     onRetry={() => act('/api/queue/retry', { id: q.id }, 'Rendering again…')} />
              ))}
            </tbody>
          </table>
          {sel && <Detail q={sel} onRetry={() => act('/api/queue/retry', { id: sel.id }, 'Rendering again…')}
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
        <button className="ibtn" aria-label={removeLabel} title={removeLabel}
                onClick={e => { e.stopPropagation(); onRemove() }}>×</button>
      </td>
    </tr>
  )
}

function Detail({ q, onRetry, onShow }: { q: QueueItem; onRetry: () => void; onShow: () => void }) {
  const [more, setMore] = useState(false)
  const attempts = q.history.map(attemptLine).join('\n')
  const details = [q.detail && q.detail !== q.error ? q.detail : '', attempts].filter(Boolean).join('\n\n')
  return (
    <aside className="ex-detail" data-testid="export-detail" aria-label="Selected clip">
      <div className="phone">
        {q.status === 'done' && q.url
          ? <video key={q.url} src={q.url} controls preload="metadata" />
          : <div className="phone-empty">{q.status === 'error' ? 'Not rendered' : q.status === 'queued' ? 'Waiting to render' : 'Rendering…'}</div>}
      </div>
      <div className="file">{q.name ?? ''}</div>
      {q.status === 'done' && <button className="btn" onClick={onShow}>Show in folder</button>}
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
      <p className="muted small">Posting to TikTok, Discord and campaigns is in the current UI for now.</p>
    </aside>
  )
}
