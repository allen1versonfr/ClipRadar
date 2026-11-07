import type { AutoEntry } from '../../api/schemas'

// "What happened": what Clip Radar did with a clip on its own (src/autopilot.py's record, auto_log), as short lines.
// Bookkeeping that ran as usual (timing, the ready sound, the storage check) isn't shown; a switch that was
// off isn't either. Anything held back or failed is, with why.

export interface Line { key: string; at: string; text: string; tone: '' | 'warn' | 'err' }

const STEP: Record<string, string> = {
  title_moment: 'Titling', latency_title: 'Timing the title', hands_free_render: 'Hands-free render',
  ready_sound: 'Ready sound', discord: 'Discord', cleanup_vods: 'Storage cleanup', latency_render: 'Timing the render',
}
// what a step that ran says; null: not worth a line
const RAN: Record<string, string | null> = {
  title_moment: 'Sent to be captioned and titled', hands_free_render: 'Queued to render on its own (hands-free)',
  discord: "Sent to the campaign's Discord", latency_title: null, ready_sound: null, cleanup_vods: null, latency_render: null,
}
const HELD: Record<string, string> = {
  hands_free_render: 'Not rendered on its own', discord: 'Not sent to Discord on its own',
}

const label = (step: string) => STEP[step] ?? step.replace(/_/g, ' ').replace(/^./, s => s.toUpperCase())
const clock = (t: number) => new Date(t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
const why = (e: AutoEntry) => (e.why ? `: ${e.why.replace(/\.$/, '')}` : '')

function line(e: AutoEntry): Omit<Line, 'key' | 'at'> | null {
  switch (e.outcome) {
    case 'ran': {
      const text = e.step in RAN ? RAN[e.step] : label(e.step)
      return text ? { text, tone: '' } : null
    }
    case 'held': return { text: `${HELD[e.step] ?? `${label(e.step)} held back`}${why(e)}`, tone: 'warn' }
    case 'failed': return { text: `${label(e.step)} failed${why(e)}`, tone: 'err' }
    case 'skipped': return e.why ? { text: `${label(e.step)} skipped${why(e)}`, tone: '' } : null
    default: return null
  }
}

/** The lines to show, oldest first (newest last). Empty: nothing to show. */
export function happened(entries: readonly AutoEntry[] | undefined): Line[] {
  return (entries ?? []).slice().sort((a, b) => a.t - b.t).flatMap((e, i) => {
    const l = line(e)
    return l ? [{ key: `${e.t}-${e.step}-${i}`, at: clock(e.t), ...l }] : []
  })
}
