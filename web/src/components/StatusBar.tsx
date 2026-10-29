import { useCallback } from 'react'
import { api } from '../api/client'
import { Queue, type Status, Update } from '../api/schemas'
import { usePoll } from '../hooks/usePoll'
import { plural } from '../format'

/** The bar at the bottom: what's running (a live recording, renders), and an update when there is one. */
export function StatusBar({ status, error, queue }: { status: Status | undefined; error: string | undefined; queue: Queue | undefined }) {
  const upd = usePoll(useCallback(() => api(Update, '/api/update'), []), 60_000).data
  if (error) return <footer className="status-bar"><span className="err-tx">{error}</span></footer>
  // each task links to where it's shown: a live session's Review, or Exports
  const tasks: Array<{ text: string; href: string }> = []
  for (const l of status?.lives ?? []) {
    const href = `#review/live/${l.id}`
    if (l.status === 'recording') tasks.push({ text: `Recording ${l.title || 'a live stream'}`, href })
    else if (l.status === 'waiting') tasks.push({ text: `Waiting for ${l.title || 'the stream'} to go live`, href })
    else if (l.status === 'starting' || l.status === 'reconnecting') tasks.push({ text: l.status === 'starting' ? 'Connecting to stream' : 'Reconnecting', href })
  }
  const active = (queue?.items ?? []).filter(q => q.status === 'queued' || q.status === 'rendering').length
  if (active) tasks.push({ text: `Rendering ${plural(active, 'clip')}`, href: '#exports' })
  return (
    <footer className="status-bar" data-testid="status-bar">
      <span className="sb-tasks" aria-live="polite">{tasks.length ? tasks.map(t => <a key={t.text} className="task" href={t.href}><i />{t.text}</a>)
        : <span className="muted">{status ? `Clip Radar ${status.version}` : ''}</span>}</span>
      {upd?.available && (
        <a className="sb-update" href="#settings/updates" data-testid="sb-update">
          {upd.state === 'downloading' ? `Downloading update ${Math.round(upd.progress * 100)}%`
            : upd.state === 'installing' ? 'Installing update…' : `Update to ${upd.latest}`}
        </a>
      )}
    </footer>
  )
}
