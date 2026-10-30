import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import { ErrorLog, ExportPerf, Gpu, Hotkey, JobStarted, Ok, Perf, Storage, type Status } from '../../api/schemas'
import { useConfirm } from '../../components/Confirm'
import { Seg } from '../../components/Seg'
import { useToast } from '../../components/Toasts'
import { fmtBytes } from '../../format'
import { useJob } from '../../hooks/useJob'
import { usePoll } from '../../hooks/usePoll'
import { getPref, setPref } from '../../prefs'

const errText = (e: unknown) => e instanceof Error ? e.message : String(e)
const ONE_TO_THREE = ['1', '2', '3']
/** Downloads at once can also be 0: no limit (the default). */
const DOWNLOADS = [['0', 'No limit'], ['1', '1'], ['2', '2'], ['3', '3']]

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
        {(key === 'downloads' ? DOWNLOADS : ONE_TO_THREE.map(n => [n, n])).map(([v, t]) => <option key={v} value={v}>{t}</option>)}
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
      <GpuSupport />
    </section>
  )
}

/** Settings › Performance › Graphics card: what runs on it, and the GPU support download for Whisper (4.36). */
export function GpuSupport() {
  const toast = useToast()
  const ask = useConfirm()
  const g = usePoll(() => api(Gpu, '/api/gpu'), 30_000)
  const refreshGpu = g.refresh
  const [jobId, setJobId] = useState<string>()
  const dl = useJob(jobId)
  useEffect(() => {
    if (!jobId || !dl.job || dl.job.status === 'running') return
    if (dl.job.status === 'done') toast('GPU support is installed: Whisper runs on the graphics card.')
    else if (dl.job.error) toast(dl.job.error, 'err')
    setJobId(undefined)
    void refreshGpu()
  }, [jobId, dl.job, refreshGpu, toast])
  const d = g.data
  if (!d) return null
  async function install() {
    if (!await ask({ title: 'Download GPU support?', ok: 'Download',
      body: `NVIDIA's CUDA libraries for Whisper, about ${(d!.download_mb / 1000).toFixed(1)} GB, once. Titles, captions and Deep scan then run on your ${d!.nvidia}, several times faster.` })) return
    try {
      setJobId((await api(JobStarted, '/api/gpu/install', {})).job)
    } catch (e) {
      toast(errText(e), 'err')
    }
  }
  async function remove() {
    if (!await ask({ title: 'Remove GPU support?', ok: 'Remove', danger: true, body: 'Whisper goes back to the CPU. You can download it again any time.' })) return
    try {
      await api(Gpu, '/api/gpu/remove', {})
    } catch (e) {
      toast(errText(e), 'err')
    }
    await g.refresh()
  }
  const whisper = d.whisper_gpu ? `on the ${d.nvidia}` : 'on the CPU'
  return (
    <div className="fld" data-testid="gpu">Graphics card
      <p className="fine" data-testid="gpu-state">
        {d.nvidia || 'No NVIDIA card found'} · video {d.encoder && d.encoder !== 'CPU' ? `encoded on the GPU (${d.encoder})` : d.encoder === 'CPU' ? 'encoded on the CPU (no GPU encoder found)' : 'encoder checked at the first render'}
        {d.hw_decode ? ', decoded on the GPU when it can' : ', decoded on the CPU'} · Whisper {whisper}
      </p>
      {d.nvidia && !d.whisper_gpu && (
        <p className="row">
          <button className="btn primary" type="button" disabled={!!jobId} onClick={() => void install()} data-testid="gpu-install">
            {jobId ? `Downloading… ${Math.round((dl.job?.progress ?? 0) * 100)}%` : 'Download GPU support for Whisper'}
          </button>
          <span className="fine">About {(d.download_mb / 1000).toFixed(1)} GB, once. Whisper on your {d.nvidia} makes titles, captions and Deep scan several times faster.</span>
        </p>
      )}
      {d.pack && <p className="row"><button className="btn" type="button" onClick={() => void remove()} data-testid="gpu-remove">Remove GPU support</button></p>}
    </div>
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
            <option value="0">Best available</option>
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
      <ErrorLogBlock />
      <div className="row wrap">
        <span className="grow">The previous look of Clip Radar is still here for a while, with the same clips and settings.</span>
        <a className="btn" href="/legacy/" data-testid="open-previous-ui">Open the previous UI</a>
      </div>
    </section>
  )
}

/** Every failure Clip Radar has recorded (errors.jsonl), to attach to a bug report or look into later. */
function ErrorLogBlock() {
  const toast = useToast()
  const log = usePoll(() => api(ErrorLog, '/api/errors'), 60_000)
  const items = log.data?.items ?? []
  async function copy() {
    try {
      await navigator.clipboard.writeText(items.map(e => JSON.stringify(e)).join('\n'))
      toast(`Copied ${items.length} error${items.length === 1 ? '' : 's'}`)
    } catch {
      toast("Couldn't copy. Open the logs folder and attach errors.jsonl instead.", 'err')
    }
  }
  async function open() {
    try { await api(Ok, '/api/open_folder', { folder: 'logs' }) } catch (e) { toast(errText(e), 'err') }
  }
  return (
    <div className="fld" data-testid="error-log">Error log
      <p className="fine">
        Clip Radar writes down every failure (downloads, renders, live clipping, the app itself) with what it was
        doing, so problems can be fixed later. {log.data && (items.length
          ? `${items.length === 50 ? 'The last 50' : items.length} recorded${log.data.session ? `, ${log.data.session} since Clip Radar started` : ''}.`
          : 'Nothing has gone wrong so far.')}
        {log.data?.path && <> File: <code className="errpath">{log.data.path}</code> (with clipradar.log, everything the app prints).</>}
      </p>
      {items.length > 0 && (
        <ul className="errlist" data-testid="error-list">
          {items.slice(0, 5).map((e, i) => <li key={i}><span className="muted">{e.at} · {e.where}</span> {e.message}</li>)}
        </ul>
      )}
      <div className="row wrap">
        <button className="btn sm" onClick={() => void open()}>Open logs folder</button>
        {items.length > 0 && <button className="btn sm" data-testid="copy-errors" onClick={() => void copy()}>Copy recent errors</button>}
      </div>
    </div>
  )
}
