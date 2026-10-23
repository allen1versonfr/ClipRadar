// Preferences kept in the browser, under the same localStorage keys as the original page (`vc_` + key,
// JSON), so both UIs share them: what you typed, the options you picked, your render style.

export function getPref<T>(key: string, fallback: T): T {
  try {
    const v = localStorage.getItem('vc_' + key)
    return v === null ? fallback : (JSON.parse(v) as T)
  } catch {
    return fallback
  }
}

export function setPref(key: string, value: unknown): void {
  try {
    localStorage.setItem('vc_' + key, JSON.stringify(value))
  } catch {
    // private mode or storage full: the choice just isn't remembered
  }
}

// The render options the original page starts from (DEFAULTS in src/ui/app.js), with your saved
// choices (vc_opts) on top. The server renders exactly what it's sent, so these must match:
// prefs.test.ts compares them with app.js and fails if one side changes without the other.
export const RENDER_DEFAULTS = {
  layout: 'split', cam: [0, 0, 0.25, 0.25], cam_height: 0.33, game_x: 0.5, game_zoom: 1.0,
  captions: true, caption_pos: 'auto', caption_color: '#ffe600', caption_size: 100, words_per_line: 3, uppercase: true,
  caption_style: 'pop', caption_font: 'classic', caption_anim: true, caption_motion: 'pop', caption_speed: 100,
  caption_emph: false, caption_filler: true, caption_punct: true, caption_spacing: 100, caption_offset: 0, cap_x: 0.5, cap_y: 0.7,
  title_style: 'box', title_font: 'classic', title_size: 100, title_x: 0.5, title_y: 0.05,
  whisper_model: 'small', language: '', hw_encode: true, aspect: '9:16', cap_outline: 100, cap_shadow: 100,
  track: 'auto', track_target: 'auto', focus_y: 0.4,
}

export type RenderOpts = typeof RENDER_DEFAULTS & Record<string, unknown>

export function renderOpts(): RenderOpts {
  return { ...RENDER_DEFAULTS, ...getPref<Record<string, unknown>>('opts', {}) }
}
