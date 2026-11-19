import { useEffect, useState } from 'react'
import { z } from 'zod'
import { api } from '../../api/client'
import { useConfirm } from '../../components/Confirm'
import { useToast } from '../../components/Toasts'

const Remote = z.looseObject({
  on: z.boolean().default(false), running: z.boolean().default(false), port: z.number().default(7870), code: z.string().default(''),
  urls: z.array(z.string()).default([]), devices: z.number().default(0), error: z.string().default(''), here: z.boolean().default(true),
})
type Remote = z.infer<typeof Remote>

/** App › Server mode (5.2): Clip Radar in Safari (or any browser) on another device on your network, signed in with an
 *  access code. Switched, and the code read, only here on the PC. */
export function ServerModePanel() {
  const toast = useToast()
  const confirm = useConfirm()
  const [r, setR] = useState<Remote>()
  const [port, setPort] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { api(Remote, '/api/remote').then(v => { setR(v); setPort(String(v.port)) }).catch(() => {}) }, [])
  async function save(patch: Record<string, unknown>) {
    setBusy(true)
    try { const v = await api(Remote, '/api/remote', patch); setR(v); setPort(String(v.port)); if (v.error) toast(v.error, 'err') }
    catch (e) { toast(e instanceof Error ? e.message : String(e), 'err') }
    setBusy(false)
  }
  async function newCode() {
    if (!await confirm({ title: 'Make a new access code?', ok: 'New code',
      body: 'Every device signed in now is signed out, and needs the new code to get back in.' })) return
    await save({ new_code: true })
  }
  return (
    <section className="panel set" aria-labelledby="set-server" data-testid="set-server">
      <header><h2 id="set-server">Server mode</h2></header>
      <p>Use Clip Radar from Safari (or any browser) on your iPhone, iPad or another computer on the same Wi-Fi. Clip Radar keeps
        running on this PC; the other device opens it at the address below and signs in once with the access code.</p>
      {!r ? <p className="muted">Loading…</p> : !r.here ? <p className="muted">Server mode is switched on the PC itself.</p> : <>
        <label className="chk"><input type="checkbox" checked={r.on} disabled={busy} onChange={e => void save({ on: e.target.checked })} data-testid="set-server-on" />
          <span><b>Server mode</b><span className="hint">{r.on ? 'On: devices on your network can open Clip Radar with the access code.' : 'Off: only this PC can open Clip Radar.'}</span></span></label>
        {r.error && <p className="banner err" role="alert">{r.error}</p>}
        {r.running && <>
          <div className="fld">Open this in Safari on the other device
            {r.urls.length ? r.urls.map(u => <code key={u} className="server-url" data-testid="set-server-url">{u}</code>)
              : <span className="hint">This PC doesn't seem to be on a network right now.</span>}
          </div>
          <div className="fld">Access code
            <code className="server-code" data-testid="set-server-code">{r.code.slice(0, 4)} {r.code.slice(4)}</code>
            <span className="hint">{r.devices ? `${r.devices} device${r.devices === 1 ? '' : 's'} signed in. ` : ''}A device stays signed in for 30 days.{' '}
              <button className="link" onClick={() => void newCode()} data-testid="set-server-newcode">Make a new code</button> to sign them all out.</span>
          </div>
          <p className="hint">The first time, Windows may ask whether Clip Radar can use your network: allow it on private networks.
            The connection isn't encrypted (plain http), so use it on your home network, not on public Wi-Fi.</p>
        </>}
        <label className="fld inline">Port
          <input className="input" inputMode="numeric" value={port} style={{ width: '7em' }} onChange={e => setPort(e.target.value.replace(/\D/g, ''))}
                 onBlur={() => { if (port && Number(port) !== r.port) void save({ port: Number(port) }) }} data-testid="set-server-port" />
        </label>
      </>}
    </section>
  )
}
