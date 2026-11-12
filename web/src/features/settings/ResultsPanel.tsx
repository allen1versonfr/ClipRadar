import { useRef, useState } from 'react'
import { api } from '../../api/client'
import { type ResultGroup, Results } from '../../api/schemas'
import { useConfirm } from '../../components/Confirm'
import { useToast } from '../../components/Toasts'
import { usePoll } from '../../hooks/usePoll'
import { ago, plural } from '../../format'

const fmtN = (n: number) => n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e4 ? `${Math.round(n / 1e3)}K` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}K` : String(n)

/** The file as base64, for the JSON body (an export is a few KB). */
function base64(file: File): Promise<string> {
  return new Promise((ok, no) => {
    const r = new FileReader()
    r.onload = () => ok(String(r.result).split(',')[1] ?? '')
    r.onerror = () => no(new Error("That file couldn't be read."))
    r.readAsDataURL(file)
  })
}

/** "Adin is 14× Jason's median": how the best group compares with the next, when there are two. */
export function headline(groups: ResultGroup[]): string {
  const [a, b] = groups
  if (!a || !b || !b.median || a.median < b.median * 1.5) return ''
  return `${a.name}: ${fmtN(a.median)} views per video, ${Math.round(a.median / b.median * 10) / 10}× ${b.name} (${fmtN(b.median)}).`
}

function Groups({ title, groups, testid }: { title: string; groups: ResultGroup[]; testid: string }) {
  if (!groups.length) return null
  return (
    <div className="table-scroll">
      <table className="table compact static" data-testid={testid}>
        <caption className="small muted">{title}</caption>
        <thead><tr><th>Group</th><th className="r">Videos</th><th className="r">Median views</th><th className="r">Likes</th><th className="r">Shares</th><th className="r">Comments / 1K</th></tr></thead>
        <tbody>
          {groups.map(g => (
            <tr key={g.name}><td>{g.name}</td><td className="r tnum">{g.videos}</td><td className="r tnum">{fmtN(g.median)}</td>
              <td className="r tnum">{g.like_rate}%</td><td className="r tnum">{g.share_rate}%</td><td className="r tnum">{g.comments_per_1k}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Settings › TikTok results: TikTok Studio's Content export, read here, matched to the clips it came from, shown by
 *  streamer, series and kind, and given to ranking. */
export function ResultsPanel() {
  const toast = useToast()
  const confirm = useConfirm()
  const poll = usePoll(() => api(Results, '/api/results'), 60_000)
  const [busy, setBusy] = useState(false)
  const [last, setLast] = useState<Results['last']>()
  const pick = useRef<HTMLInputElement>(null)
  const r = poll.data

  async function importFile(file: File) {
    setBusy(true)
    try {
      const out = await api(Results, '/api/results/import', { name: file.name, data: await base64(file) })
      setLast(out.last)
      await poll.refresh()
    } catch (e) { toast(e instanceof Error ? e.message : String(e), 'err') }
    setBusy(false)
    if (pick.current) pick.current.value = ''
  }
  return (
    <section className="panel set" aria-labelledby="set-results" data-testid="set-results">
      <header className="row"><h2 id="set-results" className="grow">TikTok results</h2>
        <button className="btn primary" disabled={busy} data-testid="results-import" onClick={() => pick.current?.click()}>{busy ? 'Reading…' : 'Import TikTok export'}</button>
        <input ref={pick} type="file" accept=".csv,.xlsx,.xls,text/csv" hidden data-testid="results-file"
               onChange={e => { const f = e.target.files?.[0]; if (f) void importFile(f) }} />
      </header>
      <p className="muted">How your posted clips actually did. In TikTok Studio, open Analytics › Content and export the data, then import the file here. It's read on this PC; nothing is sent anywhere.</p>
      <p className="fine">Each video is matched to the clip it came from (by its TikTok link if you pasted it in Exports, otherwise by its caption). Clip ranking then learns from it: a clip that did much better than that streamer's usual counts several times as much, and one that flopped counts as a skip.</p>
      {last && <p className="notice" role="status" data-testid="results-last">{plural(last.videos, 'video')} read, {last.matched} matched to your clips, {plural(last.learned, 'clip')} given to ranking.{last.learned === 0 && last.matched > 0 ? ' Ranking needs at least 3 matched clips of a streamer to compare them.' : ''}</p>}
      {r && !r.videos && <p className="fine" data-testid="results-none">Nothing imported yet.</p>}
      {r && r.videos > 0 && (
        <>
          <p data-testid="results-summary">{plural(r.videos, 'video')}, {fmtN(r.total[0]?.views ?? 0)} views · {r.matched} matched to clips · imported {ago(r.imported)}</p>
          {headline(r.by_streamer) && <p className="ex-status" data-testid="results-headline">{headline(r.by_streamer)}</p>}
          <Groups title="By streamer" groups={r.by_streamer} testid="results-streamer" />
          <Groups title="By series" groups={r.by_series} testid="results-series" />
          <Groups title="Arguments and drama against everything else" groups={r.by_kind} testid="results-kind" />
          <details>
            <summary>Every video ({r.list.length})</summary>
            <div className="table-scroll">
              <table className="table compact static" data-testid="results-list">
                <thead><tr><th>Video</th><th className="r">Views</th><th className="r">Likes</th><th className="r">Comments</th><th className="r">Shares</th><th>Clip</th></tr></thead>
                <tbody>{r.list.map((v, i) => (
                  <tr key={i}><td>{v.title || '(no caption)'}</td><td className="r tnum">{fmtN(v.views)}</td><td className="r tnum">{fmtN(v.likes)}</td>
                    <td className="r tnum">{v.comments}</td><td className="r tnum">{v.shares}</td><td className="muted">{v.clip || 'not matched'}</td></tr>
                ))}</tbody>
              </table>
            </div>
          </details>
          <div><button className="btn sm ghost danger" onClick={async () => {
            if (await confirm({ title: 'Forget the TikTok results?', ok: 'Forget', danger: true,
              body: 'The imported numbers are removed from this screen. What ranking already learned from them stays (Settings › Clip ranking can forget that).' })) {
              await api(Results, '/api/results/forget', {}).catch(() => {}); setLast(undefined); await poll.refresh()
            }
          }}>Forget results</button></div>
        </>
      )}
    </section>
  )
}
