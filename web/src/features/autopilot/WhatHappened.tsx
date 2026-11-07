import { useEffect, useId, useState } from 'react'
import { z } from 'zod'
import { api } from '../../api/client'
import { AutoEntry } from '../../api/schemas'
import { happened } from './happened'

/** What Clip Radar did with this clip on its own, newest last. Nothing at all when there's nothing to say. */
export function WhatHappened({ entries }: { entries: readonly AutoEntry[] | undefined }) {
  const id = useId()
  const lines = happened(entries)
  if (!lines.length) return null
  return (
    <section className="happened" aria-labelledby={id} data-testid="what-happened">
      <h3 id={id}>What happened</h3>
      <ul>
        {lines.map(l => <li key={l.key} className={l.tone || undefined}><span className="muted">{l.at}</span> {l.text}</li>)}
      </ul>
    </section>
  )
}

const Record = z.array(AutoEntry)

/** The same for a clip in Review: its own record and its renders', from the server (/api/autopilot/<pid>/<cid>).
 *  `version` changes when something may have happened (the clip was titled or held, its render moved on). */
export function ClipHappened({ pid, cid, version }: { pid: string; cid: number; version: string }) {
  const [entries, setEntries] = useState<AutoEntry[]>()
  useEffect(() => {
    let on = true
    api(Record, `/api/autopilot/${pid}/${cid}`).then(r => { if (on) setEntries(r) }, () => { if (on) setEntries(undefined) })
    return () => { on = false }
  }, [pid, cid, version])
  return <WhatHappened entries={entries} />
}
