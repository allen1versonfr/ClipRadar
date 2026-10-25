import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import { JobStarted, Twitch } from '../../api/schemas'
import { useConfirm } from '../../components/Confirm'
import { useToast } from '../../components/Toasts'
import { useJob } from '../../hooks/useJob'
import { usePoll } from '../../hooks/usePoll'

const loadTwitch = () => api(Twitch, '/api/twitch')

export function TwitchPanel() {
  const toast = useToast()
  const confirm = useConfirm()
  const poll = usePoll(loadTwitch, 60_000)
  const t = poll.data
  const refresh = poll.refresh
  const [token, setToken] = useState('')
  const [msg, setMsg] = useState('')
  const [loginJob, setLoginJob] = useState<string>()
  const login = useJob(loginJob)

  useEffect(() => {                           // the Twitch window closed: signed in, or why not
    if (!loginJob || !login.job || login.job.status === 'running') return
    if (login.job.status === 'done') {
      setMsg('')
      toast('Signed in to Twitch.')
    } else setMsg(login.job.error ?? '')
    setLoginJob(undefined)
    void refresh()
  }, [loginJob, login.job, refresh, toast])

  async function startLogin() {
    try {
      setLoginJob((await api(JobStarted, '/api/twitch/login', {})).job)
      setMsg('Sign in in the Twitch window…')
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), 'err')
    }
  }
  async function logout() {
    if (!await confirm({ title: 'Sign out of Twitch?', ok: 'Sign out',
      body: 'Live recordings will skip ads by leaving them out instead of getting the ad-free stream.' })) return
    try {
      await api(Twitch, '/api/twitch/logout', {})
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), 'err')
    }
    await poll.refresh()
  }
  async function saveToken() {
    setMsg('Checking with Twitch…')
    try {
      const r = await api(Twitch, '/api/twitch/token', { token: token.trim() })
      setToken('')
      setMsg('')
      toast(`Signed in to Twitch as ${r.login}.`)
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e))
    }
    await poll.refresh()
  }

  const note = msg || t?.error || (t?.signed_in && t.turbo === false
    ? "Ads still play on channels you aren't subbed to; Clip Radar leaves them out of the recording." : '')
  return (
    <section className="panel set" aria-labelledby="set-twitch" data-testid="set-twitch">
      <header className="row">
        <h2 id="set-twitch" className="grow">Twitch sign-in</h2>
        <span className={`src-dot ${t?.signed_in ? (t.error ? 'warn' : 'on') : ''}`} aria-hidden="true" />
        <span className="muted" data-testid="tw-state">
          {!t ? '' : t.signed_in ? `${t.login || 'Signed in'}${t.turbo ? ' · Turbo' : t.turbo === false ? ' · no Turbo' : ''}` : 'Not signed in'}
        </span>
      </header>
      <p className="muted">Twitch puts ads into live streams for viewers without Turbo. Clip Radar spots those ad breaks and leaves them out of recordings either way. Sign in with an account that has Turbo (or a sub to the channel) and Twitch sends the ad-free stream instead, so recording never pauses for an ad.</p>
      <div className="row wrap">
        {t?.signed_in
          ? <button className="btn" onClick={() => void logout()}>Sign out</button>
          : <button className="btn" disabled={!t?.window_login || !!loginJob} onClick={() => void startLogin()}
                    title={t?.window_login ? "Opens Twitch's own login page in a small window" : 'Needs the Clip Radar app window; paste a token below instead'}>
              Sign in with Twitch
            </button>}
        {note && <span className="muted small" aria-live="polite">{note}</span>}
      </div>
      {!t?.signed_in && (
        <details className="adv" open={(t && !t.window_login) || undefined}>
          <summary>Paste a sign-in token instead</summary>
          <p className="fine">On twitch.tv, signed in, press F12, open Application › Cookies › https://www.twitch.tv and copy the value of <b>auth-token</b>. It's stored securely for your account and only sent to Twitch. Also used for subscriber-only VODs.</p>
          <div className="row">
            <input className="input grow" type="password" placeholder="auth-token value" autoComplete="off" spellCheck={false} aria-label="Twitch sign-in token"
                   value={token} onChange={e => setToken(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && token.trim()) void saveToken() }} />
            <button className="btn" disabled={!token.trim()} onClick={() => void saveToken()}>Save</button>
          </div>
        </details>
      )}
    </section>
  )
}
