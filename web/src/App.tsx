import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api/client'
import { Ok, Queue, Status } from './api/schemas'
import { useConfirm } from './components/Confirm'
import { StatusBar } from './components/StatusBar'
import { useReviewTodo } from './shell'
import { usePoll } from './hooks/usePoll'
import { ExportsView } from './features/exports/ExportsView'
import { HomeView } from './features/home/HomeView'
import { ReviewView } from './features/review/ReviewView'
import { SettingsView } from './features/settings/SettingsView'

// The screens switch by #hash, so the app works the same at /next/ (while the original page stays
// at /) and at / (CLIPRADAR_UI=web), and a reload keeps the screen.
const VIEWS = [
  { id: 'home', label: 'Home' },
  { id: 'review', label: 'Review' },
  { id: 'exports', label: 'Exports' },
  { id: 'settings', label: 'Settings' },
] as const
type ViewId = (typeof VIEWS)[number]['id']

/** #review/<project id> is Review on that video, #review/live/<session id> on a live session */
function routeFromHash(): { view: ViewId; pid: string | undefined; live: boolean } {
  const parts = location.hash.replace(/^#\/?/, '').split('/')
  const live = parts[1] === 'live', pid = live ? parts[2] : parts[1]
  return { view: VIEWS.find(v => v.id === parts[0])?.id ?? 'home', pid: pid && /^\w+$/.test(pid) ? pid : undefined, live }
}

const loadStatus = () => api(Status, '/api/status')
const loadQueue = () => api(Queue, '/api/queue')

export function App() {
  const [route, setRoute] = useState(routeFromHash)
  const view = route.view
  const status = usePoll(loadStatus, 5000)
  const queue = usePoll(loadQueue, 3000)
  const todo = useReviewTodo()
  const confirm = useConfirm()
  const [stopped, setStopped] = useState(false)
  const busy = useRef(false)
  busy.current = !!status.data?.running

  /** Quit: asks first when something is running (it stops). The desktop window calls window.askQuit when
   *  it's closed while busy, and stays open until this answers. */
  const quit = useCallback(async (ask: boolean) => {
    if (ask && !await confirm({ title: 'Quit Clip Radar?', ok: 'Quit', danger: true,
      body: "Something is still running (a download, render or live recording). It will be stopped. Renders that didn't finish start again next time you open Clip Radar." })) return
    await api(Ok, '/api/quit', {}).catch(() => {})
    setStopped(true)
  }, [confirm])
  // live clipping running when the window opens (or the page reloads) on nothing in particular: show it
  const opened = useRef(false)
  useEffect(() => {
    if (opened.current || !status.data) return
    opened.current = true
    if (status.data.live && (!location.hash || location.hash === '#' || location.hash === '#home')) location.hash = `#review/live/${status.data.live}`
  }, [status.data])
  useEffect(() => {
    const w = window as unknown as { askQuit?: () => Promise<void> }
    w.askQuit = () => quit(true)
    return () => { delete w.askQuit }
  }, [quit])

  useEffect(() => {
    const onHash = () => setRoute(routeFromHash())
    addEventListener('hashchange', onHash)
    return () => removeEventListener('hashchange', onHash)
  }, [])
  useEffect(() => {
    // browser mode quits after 20 idle minutes without this, so it runs even while hidden
    const t = setInterval(() => { fetch('/api/ping').catch(() => {}) }, 60_000)
    return () => clearInterval(t)
  }, [])

  const current = VIEWS.find(v => v.id === view) ?? VIEWS[0]
  if (stopped) return <div className="stopped" role="status">Clip Radar has stopped. You can close this window.</div>
  const rendering = (queue.data?.items ?? []).filter(q => q.status === 'queued' || q.status === 'rendering').length
  const badge = (id: ViewId) => (id === 'review' ? todo : id === 'exports' ? rendering : 0)
  return (
    <div className="app">
      <header className="top">
        <span className="brand">Clip Radar</span>
        <span className="top-acts">
          <a className="btn ghost sm" href="/legacy/" title="Clip Radar's previous look, for a while. Your clips and settings are shared.">Previous UI</a>
          <button className="btn ghost sm" onClick={() => void quit(busy.current)} data-testid="quit">Quit</button>
        </span>
      </header>
      <nav className="rail" aria-label="Screens">
        {VIEWS.map(v => (
          <a key={v.id} href={`#${v.id}`} data-testid={`nav-${v.id}`} aria-current={v.id === view ? 'page' : undefined}
             className={v.id === view ? 'on' : ''}>
            {v.label}
            {badge(v.id) > 0 && <b className="badge" aria-label={v.id === 'review' ? `${badge(v.id)} to review` : `${badge(v.id)} rendering`}>{badge(v.id)}</b>}
          </a>
        ))}
      </nav>
      <main className="main">
        {current.id === 'home' ? <HomeView status={status.data} refreshStatus={status.refresh} />
          : current.id === 'review' ? <ReviewView pid={route.pid} live={route.live} whisper={status.data?.whisper !== false} />
          : current.id === 'exports' ? <ExportsView />
          : <SettingsView status={status.data} refreshStatus={status.refresh} section={route.pid} />}
      </main>
      <StatusBar status={status.data} error={status.error} queue={queue.data} />
    </div>
  )
}
