import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import { ExportPerf, Hotkey, JobStarted, Ok, Perf, Storage, type Status } from '../../api/schemas'
import { useConfirm } from '../../components/Confirm'
import { Seg } from '../../components/Seg'
import { useToast } from '../../components/Toasts'
import { fmtBytes } from '../../format'
import { useJob } from '../../hooks/useJob'
import { usePoll } from '../../hooks/usePoll'
import { getPref, setPref } from '../../prefs'

const errText = (e: unknown) => e instanceof Error ? e.message : String(e)
const ONE_TO_THREE = ['1', '2', '3']

/** Settings › Performance: how much runs at once (part of the export settings on the server). */
export function PerformancePanel() {
  const toast = useToast()
  const ex = usePoll(() => api(ExportPerf, '/api/export'), 60_000)
  const pc = usePoll(() => api(Perf, '/api/perf'), 15_000)
  // a choice shows at once, and the server's answer takes over once it's saved
  const [pending, setPending] = useState<Partial<ExportPerf['prefs']>>({})
  const pr = ex.data && { ...ex.data.prefs, ...pending }
  async function save(patch: Partial<ExportPerf['prefs']>) {
    setPending(p => ({ ...p, ...patch }))
    try {
      await api(ExportPerf, '/api/export', patch)
    } catch (e) {
      toast(errText(e), 'err')
    }
    await ex.refresh()
    setPending({})
  }
  const p = pc.data
  const speeds = Object.entries(p?.speeds ?? {}).map(([k, v]) => `${k.replace('libx264', 'CPU (x264)')} ${v}× real time`).join(', ')
  const select = (label: string, key: 'renders' | 'downloads' | 'lives') => (
    <label>{label}
      <select className="input" value={String(pr?.[key] ?? 1)} disabled={!pr} onChange={e => void save({ [key]: Number(e.target.value) })}>
        {ONE_TO_THREE.map(n => <option key={n} value={n}>{n}</option>)}
      </select>
    </label>
  )
  return (
    <section className="panel set" aria-labelledby="set-perf" data-testid="set-perf">
      <header><h2 id="set-perf">Performance</h2></header>
      <div className="form-grid">
        {select('Clips rendering at once', 'renders')}
        {select('Downloads at once', 'downloads')}
        {select('Live streams at once', 'lives')}
      </div>
      <div className="fld">While rendering
        <Seg label="While rendering" value={pr?.priority ?? 'responsive'} onChange={v => void save({ priority: v === 'fast' ? 'fast' : 'responsive' })}
             options={[['responsive', 'Keep the PC responsive'], ['fast', 'Finish faster']]} />
      </div>
      {p && (
        <p className="fine" data-testid="perf-info">
          This PC: {p.cpus} processor threads{p.mem_total ? ` · memory ${fmtBytes(p.mem_free)} free of ${fmtBytes(p.mem_total)}` : ''}
          {p.waiting_for_memory ? ' (extra renders wait until more is free)' : ''} · video encoder: {p.encoder}
          {speeds ? ` · renders so far: ${speeds}` : ' · render speed is measured as clips render, for the time estimates'}
        </p>
      )}
    </section>
  )
}

/** Settings › Live clipping: the clip hotkey. */
export function HotkeyPanel({ status, refreshStatus }: { status: Status | undefined; refreshStatus: () => Promise<void> }) {
  const toast = useToast()
  const h = status?.hotkey
  const [enabledNow, setEnabledNow] = useState<boolean>()
  async function toggle(enabled: boolean) {
    setEnabledNow(enabled)
    try {
      await api(Hotkey, '/api/hotkey', { enabled })
    } catch (e) {
      toast(errText(e), 'err')
    }
    await refreshStatus()
    setEnabledNow(undefined)
  }
  const state = !h ? '' : !h.supported ? 'Only available in the Windows app.' : h.error ? h.error
    : h.registered ? 'Ready. Press it while live clipping is running.' : 'Off'
  return (
    <section className="panel set" aria-labelledby="set-live" data-testid="set-live">
      <header><h2 id="set-live">Live clipping</h2></header>
      <label className="chk">
        <input type="checkbox" checked={enabledNow ?? h?.enabled ?? true} disabled={!h?.supported} onChange={e => void toggle(e.target.checked)} />
        <span>Clip hotkey <kbd className="kbd">{h?.keys ?? 'Ctrl+Alt+C'}</kbd><small data-testid="hk-state">{state}</small></span>
      </label>
      <p className="fine">Works from any window while live clipping is running, so you can keep watching the stream or playing. You'll hear a short sound when the clip is taken.</p>
    </section>
  )
}

const TOOLS: Array<[name: string, key: 'ffmpeg' | 'ytdlp' | 'twitchdownloader' | 'whisper' | 'laughter', what: string, get: string]> = [
  ['FFmpeg', 'ffmpeg', 'Cuts and renders video', 'ffmpeg'],
  ['yt-dlp', 'ytdlp', 'Downloads Twitch VODs and live streams', 'ytdlp'],
  ['TwitchDownloader', 'twitchdownloader', 'Gets chat replay for VODs', 'twitchdownloader'],
  ['Whisper', 'whisper', 'Captions, titles and Deep scan', ''],
  ['Laughter detector', 'laughter', 'Hears laughter, screams and cheering', 'laughter'],
]

/** Settings › Downloads and tools: download quality, browser cookies, and the tools Clip Radar uses. */
export function ToolsPanel({ status, refreshStatus }: { status: Status | undefined; refreshStatus: () => Promise<void> }) {
  const toast = useToast()
  const [maxh, setMaxh] = useState(() => getPref('maxh', '1080'))
  const [cookies, setCookies] = useState(() => getPref('cookies', ''))
  const [jobId, setJobId] = useState<string>()
  const setup = useJob(jobId)
  const canGet = !!status?.can_download_tools && !jobId

  useEffect(() => {
    if (!jobId || !setup.job || setup.job.status === 'running') return
    if (setup.job.status === 'done') toast('Tools are ready.')
    else if (setup.job.error) toast(setup.job.error, 'err')
    setJobId(undefined)
    void refreshStatus()
  }, [jobId, setup.job, refreshStatus, toast])

  async function get(want: string[]) {
    try {
      setJobId((await api(JobStarted, '/api/setup_tools', { want })).job)
      toast('Downloading tools…')
    } catch (e) {
      toast(errText(e), 'err')
    }
  }
  return (
    <section className="panel set" aria-labelledby="set-tools" data-testid="set-tools">
      <header><h2 id="set-tools">Downloads and tools</h2></header>
      <div className="form-grid two">
        <label>Download quality
          <select className="input" value={maxh} onChange={e => { setMaxh(e.target.value); setPref('maxh', e.target.value) }}>
            <option value="720">720p (faster)</option><option value="1080">1080p</option><option value="1440">1440p</option>
          </select>
        </label>
        <label>Use cookies from
          <select className="input" value={cookies} onChange={e => { setCookies(e.target.value); setPref('cookies', e.target.value) }}>
            <option value="">No browser</option><option value="firefox">Firefox</option><option value="chrome">Chrome</option>
            <option value="edge">Edge</option><option value="brave">Brave</option><option value="opera">Opera</option>
          </select>
          <span className="hint">Older way to reach subscriber-only VODs. Twitch sign-in above is easier and is used first.</span>
        </label>
      </div>
      <table className="table compact static" data-testid="tool-table">
        <tbody>
          {TOOLS.map(([name, key, what, k]) => {
            const ok = !!status?.[key]
            return (
              <tr key={key}>
                <td><span className="row"><span className={`src-dot ${ok ? 'on' : 'warn'}`} aria-hidden="true" />{name}</span></td>
                <td className="muted">{!status ? '' : ok ? what : 'Not installed'}</td>
                <td className="r">{!ok && k && status?.can_download_tools &&
                  <button className="btn sm" disabled={!canGet} onClick={() => void get([k])}>Download</button>}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
      {setup.job && setup.job.status === 'running' && (
        <div className="job" aria-live="polite">
          <span className="small">{setup.job.step || 'Downloading tools'} {Math.round(setup.job.progress * 100)}%</span>
          <div className="meter wide"><i style={{ width: `${Math.round(setup.job.progress * 100)}%` }} /></div>
        </div>
      )}
      <div className="row">
        <button className="btn" disabled={!canGet} onClick={() => void get(['ytdlp'])}>Update downloader</button>
        <span className="muted small">Use this if Twitch downloads start failing.</span>
      </div>
    </section>
  )
}

/** Settings › About, with Uninstall when Clip Radar was installed with Setup. */
export function AboutPanel() {
  const toast = useToast()
  const confirm = useConfirm()
  const storage = usePoll(() => api(Storage, '/api/storage'), 10 * 60_000)
  const [gone, setGone] = useState(false)
  async function uninstall() {
    if (!await confirm({ title: 'Uninstall Clip Radar?', ok: 'Uninstall', danger: true,
      body: 'Clip Radar will close and the Windows uninstaller will open. It asks before deleting your VODs and clips.' })) return
    try {
      await api(Ok, '/api/uninstall', {})
      setGone(true)
    } catch (e) {
      toast(errText(e), 'err')
    }
  }
  return (
    <section className="panel set" aria-labelledby="set-about" data-testid="set-about">
      <header><h2 id="set-about">About</h2></header>
      <div className="row">
        <span className="grow">Made for streamers. Not affiliated with Twitch or TikTok.</span>
        {storage.data?.uninstaller && !gone && <button className="btn danger" onClick={() => void uninstall()}>Uninstall…</button>}
      </div>
      {gone && <p className="notice" role="status">Uninstalling Clip Radar. Follow the uninstaller window.</p>}
      <p className="fine">If something goes wrong, the log file is at %LOCALAPPDATA%\ClipRadar\clipradar.log.</p>
    </section>
  )
}
