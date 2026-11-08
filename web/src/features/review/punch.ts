// Layout › Punch-in in the live preview (4.47.1), ported from the render's engine.punch_filter.
// Keep the numbers in step with engine.py.

/** How far in (engine.PUNCH_ZOOM). */
export const PUNCH_ZOOM: Record<string, number> = { subtle: 0.08, strong: 0.14 }
/** Seconds: fast in, hold, slow out (engine.PUNCH_IN, PUNCH_HOLD, PUNCH_OUT). */
export const PUNCH_IN = 0.18, PUNCH_HOLD = 1.1, PUNCH_OUT = 0.7
/** Beats in one clip at most (engine.PUNCH_MAX). */
export const PUNCH_MAX = 4

const clamp01 = (x: number) => (x < 0 ? 0 : x > 1 ? 1 : x)
const ease = (x: number) => x * x * (3 - 2 * x)

/** 0..1 at t seconds into the clip: for each beat a smoothstep rise over PUNCH_IN ending on it, a hold, and a
 *  smoothstep fall over PUNCH_OUT; the largest across beats (the expression punch_filter gives zoompan).
 *  beats: sorted seconds into the clip, as the server sends them. No allocations: it runs every frame. */
export function punchEnvelope(beats: readonly number[], t: number): number {
  let e = 0
  const n = Math.min(beats.length, PUNCH_MAX)
  for (let i = 0; i < n; i++) {
    const b = beats[i]!
    // punch_filter writes these as numbers with 3 decimals
    const rise = clamp01((t - Math.round((b - PUNCH_IN) * 1000) / 1000) / PUNCH_IN)
    const fall = clamp01((Math.round((b + PUNCH_HOLD + PUNCH_OUT) * 1000) / 1000 - t) / PUNCH_OUT)
    const v = ease(rise) * ease(fall)
    if (v > e) e = v
  }
  return e
}

/** The zoom at t seconds into the clip: 1 (none) up to 1 + PUNCH_ZOOM[punch] ('1+amt*e' in punch_filter). */
export function punchZoom(punch: unknown, beats: readonly number[] | null | undefined, t: number): number {
  const amt = PUNCH_ZOOM[String(punch || 'off')]
  if (!amt || !beats || !beats.length) return 1
  return 1 + amt * punchEnvelope(beats, t)
}

/** Where the push centres vertically, as a share of the picture that zooms (the focus_y build_filter gives
 *  punch_filter): the facecam strip and Cam only 0.4, Whole frame the followed face's height (else 0.4),
 *  Blur fit and Square blur the middle of the finished frame. H: the export's height (a face in pixels). */
export function punchFocus(layout: string, faceY: number | null | undefined, H: number): number {
  if (layout === 'blur' || layout === 'square') return 0.5
  if (layout === 'full' && faceY != null && Number.isFinite(faceY)) return clamp01(faceY > 1 ? faceY / H : faceY)
  return 0.4
}
