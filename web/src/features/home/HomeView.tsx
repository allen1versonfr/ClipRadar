import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import { JobStarted, Recent, type Status } from '../../api/schemas'
import { useToast } from '../../components/Toasts'
import { useJob } from '../../hooks/useJob'
import { usePoll } from '../../hooks/usePoll'
import { getPref } from '../../prefs'
import { fmtTime } from '../exports/renderStatus'
import { LiveCard } from './LiveCard'
import { RunningNow } from './RunningNow'
import { VodCard } from './VodCard'

const ago = (t: number) => {
  const s = Date.now() / 1000 - t
  return s < 90 ? 'just now' : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`
}

export function HomeView({ status, refreshStatus }: { status: Status | undefined; refreshStatus: () => Promise<void> }) {
  const [source, setSource] = useState(() => getPref('src', ''))
  const [reopen, setReopen] = useState(0)      // a Recent row opens its video again, as in the original page
  const pick = (src: string) => { setSource(src); setReopen(n => n + 1) }
  const recent = usePoll(useCallback(() => api(Recent, '/api/recent'), []), 30_000).data ?? []
  return (
    <section className="home" aria-labelledby="home-h">
      <ToolsBanner status={status} refreshStatus={refreshStatus} />
      <RunningNow />
      <h1 id="home-h">New project</h1>
      <div className="sources">
        <VodCard status={status} source={source} onSource={setSource} start={reopen} />
        <LiveCard status={status} />
      </div>
      {recent.length > 0 && (
        <section className="recent" aria-labelledby="recent-h">
          <h2 id="recent-h">Recent</h2>
          <table className="table" data-testid="recent-table">
            <thead><tr><th>Name</th><th>Source</th><th className="r">Length</th><th className="r">Last opened</th></tr></thead>
            <tbody>
              {recent.map(r => (
                <tr key={r.source} tabIndex={0} title="Open it again" data-testid="recent-row"
                    onClick={() => pick(r.source)} onKeyDown={e => { if (e.key === 'Enter') pick(r.source) }}>
                  <td title={r.title}>{r.title}{r.review && (r.review.kept || r.review.skipped)
                    ? <span className="muted"> · {r.review.kept} kept, {r.review.todo} to review</span> : null}</td>
                  <td title={r.source}>{r.source}</td>
                  <td className="r tnum">{r.duration ? fmtTime(r.duration) : ''}</td>
                  <td className="r">{r.updated ? ago(r.updated) : ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </section>
  )
}

/** FFmpeg, yt-dlp or TwitchDownloader missing: offer to download them (or say what to install). */
function ToolsBanner({ status, refreshStatus }: { status: Status | undefined; refreshStatus: () => Promise<void> }) {
  const toast = useToast()
  const [jobId, setJobId] = useState<string>()
  const { job, running } = useJob(jobId)
  useEffect(() => { if (job?.status === 'done') void refreshStatus() }, [job?.status, refreshStatus])
  if (!status) return null
  const missing: Array<[string, string]> = []
  if (!status.ffmpeg) missing.push(['ffmpeg', 'FFmpeg'])
  if (!status.ytdlp) missing.push(['ytdlp', 'yt-dlp'])
  if (!status.twitchdownloader) missing.push(['twitchdownloader', 'TwitchDownloader'])
  if (!missing.length) return null
  if (!status.can_download_tools && status.ffmpeg) return null

  async function download() {
    try {
      const r = await api(JobStarted, '/api/setup_tools', { want: missing.map(m => m[0]) })
      setJobId(r.job)
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), 'err')
    }
  }
  const names = missing.map(m => m[1]).join(', ')
  return (
    <div className="notice" role="status" data-testid="tools-banner">
      {status.can_download_tools ? <>
        <div><b>{names} {missing.length === 1 ? 'is' : 'are'} missing.</b> Clip Radar needs {missing.length === 1 ? 'it' : 'them'} to
          download and cut video (about {status.ffmpeg ? 40 : 130} MB).
          {running && job && <> Downloading… {Math.round(job.progress * 100)}%</>}
          {job?.status === 'error' && <span className="err-tx"> {job.error}</span>}</div>
        <button className="btn primary" disabled={running} onClick={() => void download()}>Download</button>
      </> : <div><b>FFmpeg is missing.</b> Install FFmpeg and add it to your PATH, then restart Clip Radar.</div>}
    </div>
  )
}
