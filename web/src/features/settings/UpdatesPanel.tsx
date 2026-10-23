import { useState } from 'react'
import { api } from '../../api/client'
import { Update, Versions } from '../../api/schemas'
import { useConfirm } from '../../components/Confirm'
import { useToast } from '../../components/Toasts'
import { ago } from '../../format'
import { usePoll } from '../../hooks/usePoll'
import { openUrl } from './openUrl'

const loadUpdate = () => api(Update, '/api/update')
const BUSY = ['checking', 'downloading', 'installing']

/** What the updater is doing, in a sentence, and how it should look. */
export function updateLine(u: Update, now?: number): { text: string; cls: '' | 'ok' | 'new' | 'err' } {
  if (u.state === 'checking') return { text: 'Checking for updates…', cls: '' }
  if (u.state === 'downloading') return { text: `Downloading version ${u.latest}… ${Math.round(u.progress * 100)}%`, cls: 'new' }
  if (u.state === 'installing') return { text: `Installing version ${u.latest}. Clip Radar will close and reopen.`, cls: 'new' }
  if (u.state === 'error') return { text: u.error, cls: 'err' }
  if (u.available) return { text: `Version ${u.latest} is available${u.state === 'ready' ? ' and downloaded' : ''}.`, cls: 'new' }
  if (u.checked) return { text: `You're up to date. Checked ${ago(u.checked, now)}.`, cls: 'ok' }
  return { text: 'Not checked yet.', cls: '' }
}

export function UpdatesPanel({ version }: { version: string | undefined }) {
  const toast = useToast()
  const confirm = useConfirm()
  const [ms, setMs] = useState(60_000)
  const poll = usePoll(loadUpdate, ms)
  const u = poll.data
  const busy = !!u && BUSY.includes(u.state)
  // as the original page: quickly while it checks, downloads or installs; every few seconds until the
  // first automatic check has run; otherwise once a minute
  const want = busy ? 700 : u && !u.checked && u.auto ? 4000 : 60_000
  if (want !== ms) setMs(want)
  const [token, setToken] = useState('')
  const [autoNow, setAutoNow] = useState<boolean>()     // shown at once, until the server has it

  async function act(path: string, body: unknown, done?: (u: Update) => void) {
    try {
      const r = await api(Update, path, body)
      done?.(r)
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), 'err')
    }
    await poll.refresh()
  }
  async function install(ver?: string) {
    if (!u) return
    if (!u.can_install) return void openUrl(u.page, toast)
    const ok = await confirm({
      title: `${ver ? 'Install' : 'Update to'} Clip Radar ${ver ?? u.latest}?`, ok: ver ? 'Install' : 'Update now',
      body: `Clip Radar will download ${ver ? 'this version' : 'the update'}, close, install it and open again. Your clips, projects and settings are kept.`,
    })
    if (ok) await act('/api/update/install', ver ? { version: ver } : {})
  }
  async function saveToken(t: string) {
    await act('/api/update/token', { token: t }, r => {
      setToken('')
      if (t) toast(r.error ? r.error : 'Token saved. Update checks can see the private repository now.', r.error ? 'err' : '')
      else toast('Token removed.')
    })
  }

  const line = u ? updateLine(u) : { text: poll.error || 'Not checked yet.', cls: poll.error ? 'err' as const : '' as const }
  return (
    <section className="panel set" aria-labelledby="set-updates" data-testid="set-updates">
      <header><h2 id="set-updates">Updates</h2></header>
      <div className="row upd-row">
        <div className="grow">
          <div className="upd-title">Clip Radar <span className="tnum">{version ?? u?.current}</span></div>
          <div className={`upd-state ${line.cls}`} data-testid="upd-state" aria-live="polite">{line.text}</div>
        </div>
        <button className="btn" disabled={!u || busy} onClick={() => void act('/api/update/check', {})}>Check for updates</button>
        {u?.available && (
          <button className="btn primary" disabled={busy} onClick={() => void install()}>
            {!u.can_install ? 'Download from GitHub' : u.state === 'downloading' ? 'Downloading…' : u.state === 'installing' ? 'Installing…' : 'Update now'}
          </button>
        )}
      </div>
      {u?.state === 'downloading' && <div className="meter wide" role="progressbar" aria-label="Downloading the update"
        aria-valuenow={Math.round(u.progress * 100)} aria-valuemin={0} aria-valuemax={100}><i style={{ width: `${Math.round(u.progress * 100)}%` }} /></div>}
      {u?.available && u.notes && (
        <div className="upd-notes">
          <div className="row"><b className="grow">What's new in {u.latest}</b><button className="link" onClick={() => void openUrl(u.page, toast)}>Release notes on GitHub</button></div>
          <div className="upd-notes-body">{u.notes}</div>
        </div>
      )}
      <AllVersions onInstall={v => void install(v)} />
      <label className="chk">
        <input type="checkbox" checked={autoNow ?? u?.auto ?? true} disabled={!u} onChange={e => {
          const auto = e.target.checked
          setAutoNow(auto)
          void act('/api/update/settings', { auto }).finally(() => setAutoNow(undefined))
        }} />
        <span>Check for updates automatically</span>
      </label>
      <details className="adv" open={u?.token || /private/i.test(u?.error ?? '') || undefined}>
        <summary>Private repository {u?.token && <span className="muted">· token {u.token_hint} saved</span>}</summary>
        <p className="fine">If Clip Radar's GitHub repository is private, updates need a read-only access token. On GitHub: Settings › Developer settings › Fine-grained tokens › Generate. Pick only the vodclipper repository and give it <b>Contents: Read-only</b>. It's stored encrypted for your Windows account.</p>
        <div className="row">
          <input className="input grow" type="password" placeholder="github_pat_…" autoComplete="off" spellCheck={false} aria-label="GitHub access token"
                 value={token} onChange={e => setToken(e.target.value)} />
          <button className="btn" disabled={!token.trim()} onClick={() => void saveToken(token.trim())}>Save</button>
          {u?.token && <button className="btn" onClick={() => void saveToken('')}>Remove</button>}
        </div>
      </details>
    </section>
  )
}

/** Every release, newest first: newer ones can be installed, older ones are an archive. Loaded when opened. */
function AllVersions({ onInstall }: { onInstall: (v: string) => void }) {
  const toast = useToast()
  const [open, setOpen] = useState(false)
  const v = usePoll(() => api(Versions, '/api/update/versions'), 10 * 60_000, open)
  return (
    <details className="adv" onToggle={e => setOpen(e.currentTarget.open)}>
      <summary>All versions</summary>
      <p className="fine">Every version of Clip Radar (and VodClipper before it). You can install any version newer than yours; older ones are kept on GitHub as an archive.</p>
      {!v.data ? <p className="muted small">{v.error || 'Loading…'}</p>
        : !v.data.versions.length ? <p className="muted small">{v.data.error || 'No versions found.'}</p>
        : (
          <ul className="ver-list" data-testid="ver-list">
            {v.data.versions.map(x => (
              <li key={x.version} className={x.current ? 'cur' : ''}>
                <details>
                  <summary>
                    <b className="tnum">{x.version}</b>
                    {x.current ? <span className="ver-tag cur">Installed</span> : x.archive ? <span className="ver-tag">Archive</span> : x.newer ? <span className="ver-tag new">Newer</span> : null}
                    <span className="muted small">{x.published ? new Date(x.published).toLocaleDateString() : ''}</span>
                    <span className="grow" />
                    {x.installable ? <button className="btn sm" onClick={e => { e.preventDefault(); onInstall(x.version) }}>Install</button>
                      : x.newer && !v.data?.can_install ? <button className="btn sm ghost" onClick={e => { e.preventDefault(); void openUrl(x.page, toast) }}>Download</button> : null}
                  </summary>
                  <div className="ver-notes">{x.notes || 'No notes.'}</div>
                  <button className="link" onClick={() => void openUrl(x.page, toast)}>On GitHub</button>
                </details>
              </li>
            ))}
          </ul>
        )}
    </details>
  )
}
