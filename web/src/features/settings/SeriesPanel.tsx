import { useState } from 'react'
import { api } from '../../api/client'
import { type SeriesSettings, SeriesView } from '../../api/schemas'
import { Seg } from '../../components/Seg'
import { useConfirm } from '../../components/Confirm'
import { useToast } from '../../components/Toasts'
import { usePoll } from '../../hooks/usePoll'
import { plural } from '../../format'

const NAMING: Array<[string, string]> = [['before', 'Before the title'], ['after', 'After the title'], ['caption', 'Caption only']]
const NAMING_HINT: Record<string, string> = {
  before: 'On the video: "Jason × Emily Pt. 6: Emily steals his car".',
  after: 'On the video: "Emily steals his car | Jason × Emily Pt. 6".',
  caption: "The title on the video stays as it is. The part is only in the post caption (if that's on below).",
}

/** Settings › Series: clips posted as numbered parts of one storyline. All optional: nothing is in a series
 *  until you put it there (or turn on Add by itself). */
export function SeriesPanel() {
  const toast = useToast()
  const confirm = useConfirm()
  const poll = usePoll(() => api(SeriesView, '/api/series'), 60_000)
  const [pending, setPending] = useState<Partial<SeriesSettings>>({})
  const s = poll.data && { ...poll.data.settings, ...pending }
  async function post(body: object) {
    try { await api(SeriesView, '/api/series', body) } catch (e) { toast(e instanceof Error ? e.message : String(e), 'err') }
    await poll.refresh()
  }
  async function save(patch: Partial<SeriesSettings>) {
    setPending(p => ({ ...p, ...patch }))
    await post({ settings: patch })
    setPending({})
  }
  const tick = (k: keyof SeriesSettings, label: string, hint?: string) => (
    <label className="chk">
      <input type="checkbox" checked={!!s?.[k]} disabled={!s} onChange={e => void save({ [k]: e.target.checked })} />
      <span>{label}{hint && <small>{hint}</small>}</span>
    </label>
  )
  const list = poll.data?.list ?? []
  return (
    <section className="panel set" aria-labelledby="set-series" data-testid="set-series">
      <header><h2 id="set-series">Series</h2></header>
      <p className="muted">Post clips of one storyline as numbered parts with the same name, so viewers know there's more and follow for it. Optional: a clip is only in a series when you put it in one, from Review › Clip.</p>
      {tick('suggest', 'Suggest series', "Clip Radar notices who's in a clip (names said on stream and typed by chat). When someone keeps coming back, Review suggests a series. One click adds the clip as the next part.")}
      {tick('auto_add', 'Add clips to a series by themselves', 'A clip with someone from one of your series in it joins it as the next part. New series are only ever suggested.')}
      <div className="fld">Part name
        <Seg label="Part name" value={s?.naming ?? 'before'} options={NAMING} onChange={naming => void save({ naming })} />
        <span className="hint">{NAMING_HINT[s?.naming ?? 'before']}</span>
      </div>
      {tick('caption_line', 'Start the post caption with the part')}
      {tick('hashtag', "Add the series' hashtag to the post caption", 'For example #jasonxemily, so every part is one tap away.')}
      {tick('end_card', 'End card', 'Big text over the last 2.5 seconds of the clip.')}
      {s?.end_card && (
        <label className="fld">End card text
          <input className="input" maxLength={60} defaultValue={s.end_card_text} key={s.end_card_text}
                 onBlur={e => { if (e.target.value !== s.end_card_text) void save({ end_card_text: e.target.value }) }} />
          <span className="hint">{'{next}'} is the next part's number, {'{series}'} the series' name.</span>
        </label>
      )}
      <h3 className="small muted">Your series</h3>
      {!list.length && <p className="fine" data-testid="series-none">None yet.</p>}
      <ul className="series-list">
        {list.map(x => (
          <li key={x.id} className="row wrap" data-testid="series-row">
            <input className="input grow" aria-label="Series name" defaultValue={x.name} key={x.name} maxLength={60}
                   onBlur={e => { if (e.target.value.trim() && e.target.value !== x.name) void post({ id: x.id, name: e.target.value }) }} />
            <span className="muted small">{plural(x.count, 'part')} · next Pt. {x.next}{x.tag ? ` · ${x.tag}` : ''}</span>
            <button className="btn sm ghost danger" onClick={async () => {
              if (await confirm({ title: `Delete ${x.name}?`, ok: 'Delete', danger: true,
                body: "Clips already rendered keep their part in the title. Clips you queue from now on won't have one." })) void post({ id: x.id, delete: true })
            }}>Delete</button>
          </li>
        ))}
      </ul>
    </section>
  )
}
