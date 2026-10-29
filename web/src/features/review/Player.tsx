import { forwardRef, type ReactNode, useEffect, useImperativeHandle, useRef, useState } from 'react'
import { api } from '../../api/client'
import { Job, PreviewCopy } from '../../api/schemas'
import { getPref, setPref } from '../../prefs'
import { fmt } from './model'

export interface PlayerHandle {
  toggle: () => void; seekBy: (s: number) => void; toggleMute: () => void
  /** the video element itself, for the timeline */
  video: () => HTMLVideoElement | null
  /** whether you muted the preview (a fix being played mutes it for a moment on top of that) */
  userMuted: () => boolean
  /** the preview volume you chose, 0 to 1 */
  level: () => number
}

// which videos needed the smaller copy (the window can't play HEVC or AV1): 'making', 'ready' or 'failed'
const copies: Record<string, string> = {}

/** The clip's stretch of the video, looping, with the preview volume the original page uses (vc_volume,
 *  vc_muted). A video the window can't play switches to Clip Radar's 480p copy (/api/preview). */
export const Player = forwardRef<PlayerHandle, { src: string; pid: string; clipKey: string; start: number; end: number; autoPlay?: boolean
    /** drawn instead of the plain video (the clip preview), which then only plays the sound and frames */
    children?: ReactNode }>(
  function Player({ src, pid, clipKey, start, end, autoPlay = true, children }, ref) {
    const v = useRef<HTMLVideoElement>(null)
    const [t, setT] = useState(start)
    const [paused, setPaused] = useState(true)
    const [vol, setVol] = useState(() => Math.min(1, Math.max(0, Number(getPref('volume', 1)) || 0)))
    const [muted, setMuted] = useState(() => !!getPref('muted', false))
    const [copy, setCopy] = useState(copies[src] ?? '')
    const [making, setMaking] = useState<number>()
    const range = useRef({ start, end })
    range.current = { start, end }
    const url = copy === 'ready' ? `${src}?preview=1` : src

    useImperativeHandle(ref, () => ({
      toggle: () => { const el = v.current; if (el) void (el.paused ? el.play().catch(() => {}) : el.pause()) },
      seekBy: s => { const el = v.current; if (el) el.currentTime = Math.max(start, Math.min(end, el.currentTime + s)) },
      toggleMute: () => setMuted(m => !m),
      video: () => v.current,
      userMuted: () => muted,
      level: () => vol,
    }), [start, end, muted, vol])

    const startRef = useRef(start)
    startRef.current = start
    useEffect(() => {                         // a new clip (not a trim of this one): start at its beginning
      const el = v.current
      if (!el) return
      const go = () => {
        try { el.currentTime = startRef.current } catch { /* not loaded yet: loadedmetadata does it */ }
        if (autoPlay) void el.play().catch(() => {})
      }
      if (el.readyState >= 1) go()
      else el.addEventListener('loadedmetadata', go, { once: true })
      return () => el.removeEventListener('loadedmetadata', go)
    }, [clipKey, url, autoPlay])
    useEffect(() => {
      const el = v.current
      if (!el) return
      el.volume = vol
      el.muted = muted
      setPref('volume', vol)
      setPref('muted', muted)
    }, [vol, muted])

    async function makeCopy() {
      if (copies[src]) return
      copies[src] = 'making'
      setMaking(0)
      try {
        const r = await api(PreviewCopy, '/api/preview', { pid })
        if (!r.ready && r.job) {
          for (;;) {
            const j = await api(Job, `/api/job/${r.job}`)
            setMaking(j.progress)
            if (j.status === 'done') break
            if (j.status !== 'running') throw new Error(j.error || 'The preview copy stopped.')
            await new Promise(res => setTimeout(res, 700))
          }
        }
        copies[src] = 'ready'
      } catch {
        copies[src] = 'failed'
      }
      setCopy(copies[src]!)
      setMaking(undefined)
    }
    function onTime() {
      const el = v.current
      if (!el) return
      const { start: a, end: b } = range.current
      if (!el.paused && (el.currentTime >= b - 0.04 || el.currentTime < a - 1)) el.currentTime = a     // playing: loop the clip
      setT(el.currentTime)
    }
    function onLoaded() {
      const el = v.current
      if (el && !el.videoWidth && copy !== 'ready') void makeCopy()           // sound but no picture
    }
    const len = Math.max(0.1, end - start)
    const level = muted ? 0 : vol
    return (
      <div className="rv-player">
        <div className={children ? 'rv-stage' : 'rv-video'}>
          {children}
          <video ref={v} src={url} playsInline preload="auto" data-testid="rv-video" style={children ? { display: 'none' } : undefined}
                 onTimeUpdate={onTime} onPlay={() => setPaused(false)} onPause={() => setPaused(true)}
                 onLoadedData={onLoaded} onError={() => { if (copy !== 'ready') void makeCopy() }}
                 onClick={() => { const el = v.current; if (el) void (el.paused ? el.play().catch(() => {}) : el.pause()) }} />
          {making !== undefined && <p className="rv-making">This video needs a copy the window can play. Making it… {Math.round(making * 100)}%</p>}
          {copy === 'failed' && <p className="rv-making">This video can't be previewed here. Renders still use the original.</p>}
        </div>
        <div className="rv-controls">
          <button className="btn sm" aria-label={paused ? 'Play' : 'Pause'} onClick={() => { const el = v.current; if (el) void (el.paused ? el.play().catch(() => {}) : el.pause()) }}>
            {paused ? '▶' : '❚❚'}
          </button>
          <input type="range" className="grow" aria-label="Position in the clip" min={0} max={len} step={0.05}
                 value={Math.min(len, Math.max(0, t - start))}
                 aria-valuetext={`${fmt(t - start)} of ${fmt(len)}`}
                 onChange={e => { const el = v.current; if (el) el.currentTime = start + Number(e.target.value) }} />
          <span className="tnum muted small">{fmt(t - start)} / {fmt(len)}</span>
          <button className="btn sm ghost" aria-label={level === 0 ? 'Unmute' : 'Mute'} onClick={() => {
            if (muted || vol === 0) { setMuted(false); if (vol === 0) setVol(0.5) } else setMuted(true)
          }}>{level === 0 ? 'Unmute' : 'Mute'}</button>
          <input type="range" className="rv-vol" aria-label="Preview volume" min={0} max={100} step={1}
                 value={Math.round(level * 100)} aria-valuetext={muted ? `Muted (${Math.round(vol * 100)}%)` : `${Math.round(vol * 100)}%`}
                 onChange={e => { const n = Number(e.target.value) / 100; setVol(n); setMuted(n === 0 ? muted : false) }} />
        </div>
      </div>
    )
  })
