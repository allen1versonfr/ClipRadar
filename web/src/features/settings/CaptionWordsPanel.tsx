import { useState } from 'react'
import { api } from '../../api/client'
import { ExportWords } from '../../api/schemas'
import { useToast } from '../../components/Toasts'
import { usePoll } from '../../hooks/usePoll'

/** "word = replacement" per line; an empty right side drops the word. Split at the first "=". */
export function parseReplace(txt: string): Array<[string, string]> {
  return txt.split('\n').map(l => {
    const i = l.indexOf('=')
    return (i < 0 ? [l, ''] : [l.slice(0, i), l.slice(i + 1)]).map(x => x.trim()) as [string, string]
  }).filter(([a]) => a)
}
export const replaceText = (r: Array<[string, string]>) => r.map(([a, b]) => `${a} = ${b}`).join('\n')
export const parseVocab = (txt: string) => txt.split(/[,\n]/).map(x => x.trim()).filter(Boolean)

/** Settings › Caption words: replacements in captions, and words Whisper should recognise.
 *  Saved when you leave a box (as in the current UI). */
export function CaptionWordsPanel() {
  const toast = useToast()
  const poll = usePoll(() => api(ExportWords, '/api/export'), 60_000)
  const [replace, setReplace] = useState<string>()
  const [vocab, setVocab] = useState<string>()
  const pr = poll.data?.prefs
  async function save(patch: object, done: () => void) {
    try {
      await api(ExportWords, '/api/export', patch)
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), 'err')
    }
    await poll.refresh()
    done()
  }
  return (
    <section className="panel set" aria-labelledby="set-words" data-testid="set-words">
      <header><h2 id="set-words">Caption words</h2></header>
      <label className="fld">Replace words in captions
        <textarea className="input" rows={3} disabled={!pr} placeholder="One per line: word = replacement (leave the right side empty to drop it)"
                  value={replace ?? replaceText(pr?.replace ?? [])} onChange={e => setReplace(e.target.value)}
                  onBlur={() => { if (replace !== undefined) void save({ replace: parseReplace(replace) }, () => setReplace(undefined)) }} />
      </label>
      <label className="fld">Words to recognise
        <textarea className="input" rows={2} disabled={!pr} placeholder="Names, places, game words, inside jokes… separated by commas"
                  value={vocab ?? (pr?.vocab ?? []).join(', ')} onChange={e => setVocab(e.target.value)}
                  onBlur={() => { if (vocab !== undefined) void save({ vocab: parseVocab(vocab) }, () => setVocab(undefined)) }} />
        <span className="hint">The streamer's name and the channel's emotes are added by themselves.</span>
      </label>
    </section>
  )
}
