import type { LiveView } from '../../api/schemas'
import { fmt } from './model'

// Review on a live session: what its header says (the original page's updateLiveHead and liveStatusText).

export const LIVE_ON = ['recording', 'reconnecting', 'starting', 'waiting']
export const LIVE_LABEL: Record<string, string> = { starting: 'Connecting', waiting: 'Waiting for stream', recording: 'Live',
  reconnecting: 'Reconnecting', ended: 'Stream ended', stopped: 'Stopped', error: 'Error' }
const LAT_LABEL: Record<string, string> = { ingest: 'recording', detect: 'spotting', context: 'waiting for the reaction',
  cut: 'cutting', title: 'captions and title', render: 'rendering' }

export interface Bit { text: string; tone?: 'warn' | 'muted'; title?: string }

/** The line under the live controls: chat, moments forming, how soon a clip is ready, what was passed on. */
export function liveStatus(L: LiveView): Bit[] {
  const bits: Bit[] = []
  const chat = ({ offline: "Chat not connected: clipping from the stream's sound",
    quiet: "Chat went quiet: clipping from the stream's sound until it's back" } as Record<string, string>)[L.chat_state]
  if (chat) bits.push({ text: chat, tone: 'warn' })
  else if (L.chat_state === 'ok') bits.push({ text: `Chat delay ${Math.round(L.chat_delay)} s${L.delay_auto ? (L.delay_sure ? ' (worked out)' : ' (working it out)') : ''}` })
  for (const f of L.forming.slice(-2)) bits.push({ text: `Moment forming at ${fmt(f.at)} · score ${f.quality}` })
  const lat = L.latency
  if (lat.total != null) {
    const parts = Object.keys(LAT_LABEL).filter(k => lat[k] != null).map(k => `${LAT_LABEL[k]} ${lat[k]} s`).join(' · ')
    bits.push({ text: `Ready about ${Math.round(lat.total)} s after it happens`, title: parts })
  } else if (lat.context != null) {
    bits.push({ text: `Cut about ${Math.round((lat.ingest ?? 0) + (lat.detect ?? 0) + lat.context + (lat.cut ?? 0))} s after it happens` })
  }
  const sk = L.skipped, passed = (sk.confidence ?? 0) + (sk.crowd ?? 0) + (sk.learned ?? 0)
  if (passed || sk.repeat) bits.push({ text: `Passed on ${passed + (sk.repeat ?? 0)}`,
    title: `Under your lowest score: ${sk.confidence ?? 0} · only one or two people in chat: ${sk.crowd ?? 0} · unlike your picks: ${sk.learned ?? 0} · repeats: ${sk.repeat ?? 0}` })
  return bits
}
