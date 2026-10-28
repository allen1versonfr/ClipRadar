import { useEffect, useState } from 'react'
import { api } from './api/client'
import { Status } from './api/schemas'
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

/** #review/<project id> is Review on that video */
function routeFromHash(): { view: ViewId; pid: string | undefined } {
  const [h = '', pid] = location.hash.replace(/^#\/?/, '').split('/')
  return { view: VIEWS.find(v => v.id === h)?.id ?? 'home', pid: pid && /^\w+$/.test(pid) ? pid : undefined }
}

const loadStatus = () => api(Status, '/api/status')

export function App() {
  const [route, setRoute] = useState(routeFromHash)
  const view = route.view
  const status = usePoll(loadStatus, 5000)

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
  return (
    <div className="app">
      <header className="top">
        <span className="brand">Clip Radar</span>
        <span className="preview-tag">New UI preview</span>
        <a className="btn ghost sm" href="/legacy/">Back to the current UI</a>
      </header>
      <nav className="rail" aria-label="Screens">
        {VIEWS.map(v => (
          <a key={v.id} href={`#${v.id}`} data-testid={`nav-${v.id}`} aria-current={v.id === view ? 'page' : undefined}
             className={v.id === view ? 'on' : ''}>{v.label}</a>
        ))}
      </nav>
      <main className="main">
        {current.id === 'home' ? <HomeView status={status.data} refreshStatus={status.refresh} />
          : current.id === 'review' ? <ReviewView pid={route.pid} />
          : current.id === 'exports' ? <ExportsView />
          : <SettingsView status={status.data} refreshStatus={status.refresh} />}
      </main>
      <footer className="status-bar">
        {status.error ? <span className="err-tx">{status.error}</span>
          : status.data ? <span className="muted">Clip Radar {status.data.version}</span> : null}
      </footer>
    </div>
  )
}
