/** The full stream being saved while a live stream is clipped (5.5), shown wherever the stream is: Running now, the
 *  live session, Targets. vod: the session's vod state; active: the stream is recording right now. */
export function RecBadge({ vod, active, compact = false }: { vod: string; active: boolean; compact?: boolean }) {
  const b = ((): [string, string, string] | null => {
    switch (vod) {
      case 'keeping':
        return active ? ['rec', compact ? 'REC · full stream' : 'REC · Saving the full stream',
          'The whole stream is being recorded and saved. It becomes one video in Export VOD when the stream ends. About 3 GB an hour at 1080p.']
          : ['rec idle', 'Will save the full stream', 'Recording starts when the stream does, and the whole of it is kept.']
      case 'saving': return ['rec busy', 'Saving the full stream as one video…', 'The stream ended: its recording is being joined into one video.']
      case 'saved': return ['rec done', 'Full stream saved', 'The whole stream is saved as one video, ready in Export VOD.']
      case 'stopped': return ['rec warn', 'Full stream stopped saving', 'The disk is nearly full, so the rest of the stream isn\'t being kept. Moments are still clipped.']
      case 'failed': return ['rec err', 'Full stream not saved', 'Saving the full stream failed: the live session says why.']
      default: return null
    }
  })()
  if (!b) return null
  return <span className={`rec-badge ${b[0]}`} title={b[2]} data-testid="rec-badge"><i aria-hidden="true" />{b[1]}</span>
}
