import { useCallback } from 'react'
import { api } from '../../api/client'
import { JobStarted, Ok, Sources, type Source } from '../../api/schemas'
import { useToast } from '../../components/Toasts'
import { go, liveUrl, reviewUrl } from '../../handoff'
import { usePoll } from '../../hooks/usePoll'
import { fmtLeft, fmtTime } from '../exports/renderStatus'

const LIVE_ON = ['recording', 'reconnecting', 'starting', 'waiting']
const ST: Record<string, string> = {
  running: 'Working', done: 'Ready', error: 'Failed', cancelled: 'Cancelled', paused: 'Paused', starting: 'Connecting',
  waiting: 'Waiting for the stream', recording: 'Recording', reconnecting: 'Reconnecting', ended: 'Stream ended', stopped: 'Stopped',
}
const plural = (n: number, w: string) => `${n} ${n === 1 ? w : w + 's'}`

/** The line under a source's name (as in the original page). */
export function sourceDetail(x: Source): string {
  if (x.type === 'live') {
    const st = x.status === 'recording' ? `Recording ${fmtTime(x.recorded)}` : ST[x.status] ?? x.status
    return `${st} · ${plural(x.moments, 'moment')}${x.chat_state === 'quiet' || x.chat_state === 'offline' ? ' · no chat' : ''}`
  }
  if (x.status === 'running') return `${x.step} · ${Math.round(x.progress * 100)}%${x.eta != null ? ' · ' + fmtLeft(x.eta) : ''}`
  return x.error || ST[x.status] || x.status
}

export function RunningNow() {
  const toast = useToast()
  const load = useCallback(() => api(Sources, '/api/sources'), [])
  const poll = usePoll(load, 1500)
  const items = poll.data?.items ?? []
  if (!items.length) return null

  async function act(path: string, body: object) {
    try {
      if (path === '/api/sources/resume') await api(JobStarted, path, body)
      else await api(Ok, path, body)
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), 'err')
    }
    await poll.refresh()
  }

  const lim = poll.data?.limits
  return (
    <section className="panel src-dash" aria-labelledby="run-h" data-testid="running-now">
      <header>
        <h2 id="run-h">Running now</h2>
        {lim && <span className="hint">Up to {plural(lim.downloads, 'download')} and {plural(lim.lives, 'live stream')} at once (Settings › Performance)</span>}
      </header>
      {items.map(x => {
        const live = x.type === 'live'
        const on = live ? LIVE_ON.includes(x.status) : x.status === 'running'
        return (
          <div key={`${x.type}-${x.id}`} className={`src-row ${x.status}`} data-testid="source-row">
            <span className={`src-dot${on ? ' on' : ''}${live ? ' live' : ''}`} aria-hidden="true" />
            <div className="src-main">
              <b title={x.source}>{x.title || x.source}</b>
              <small>{x.label} · {sourceDetail(x)}</small>
              {!live && x.status === 'running' && <span className="meter wide"><i style={{ width: `${Math.round(x.progress * 100)}%` }} /></span>}
            </div>
            <span className="src-acts">
              {live ? <>
                <button className="btn sm" onClick={() => go(liveUrl(x.id))}>Open</button>
                {on ? <button className="btn sm ghost" onClick={() => act(`/api/live/${x.id}/stop`, {})}>Stop</button>
                  : <button className="link" onClick={() => act('/api/sources/forget', { live: x.id })}>Hide</button>}
              </> : x.status === 'running' ? <>
                <button className="btn sm ghost" title="Stop for now; Resume carries on where it left off" onClick={() => act('/api/sources/pause', { job: x.id })}>Pause</button>
                <button className="link" onClick={() => act('/api/cancel', { job: x.id })}>Cancel</button>
              </> : x.status === 'done' ? <>
                <button className="btn sm" onClick={() => go(reviewUrl(x.pid))}>Open</button>
                <button className="link" onClick={() => act('/api/sources/forget', { job: x.id })}>Hide</button>
              </> : <>
                <button className="btn sm" onClick={() => act('/api/sources/resume', { job: x.id })}>{x.status === 'error' ? 'Try again' : 'Resume'}</button>
                <button className="link" onClick={() => act('/api/sources/forget', { job: x.id })}>Hide</button>
              </>}
            </span>
          </div>
        )
      })}
    </section>
  )
}
