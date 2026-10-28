import { z } from 'zod'

// What this UI reads from the server, checked at runtime. Objects are "loose": the server adds fields
// over time and older screens must keep working, so only the fields used here are required.
// The server's side of these shapes: queue_public() and the render lifecycle in src/server.py.

export const STAGES = ['queued', 'validating', 'preparing', 'rendering', 'verifying', 'done', 'error', 'cancelled'] as const
export type Stage = (typeof STAGES)[number]

export const Attempt = z.looseObject({
  n: z.number(),
  started: z.number(),
  ended: z.number().optional(),
  outcome: z.enum(['running', 'done', 'error', 'cancelled', 'interrupted']),
  stage: z.string().optional(),
  kind: z.string().optional(),
  error: z.string().optional(),
  encoder: z.string().optional(),
  exit: z.number().optional(),
  fallback: z.string().optional(),
  layout_used: z.string().optional(),
  // the layout's own failure, kept when a fallback layout made the clip
  first_failure: z.looseObject({
    layout: z.string().default(''),
    exit: z.number().nullable().optional(),
    reason: z.string().default(''),
    filter: z.string().default(''),
  }).optional(),
})
export type Attempt = z.infer<typeof Attempt>

export const QueueItem = z.looseObject({
  id: z.string(),
  status: z.enum(['queued', 'rendering', 'done', 'error', 'cancelled']),
  // items saved before 4.13 have no stage: the status stands in for it
  stage: z.enum(STAGES).optional(),
  stage_at: z.number().optional(),
  step: z.string().default(''),
  progress: z.number().default(0),
  eta: z.number().nullable().optional(),
  title: z.string().default(''),
  rank: z.number().optional(),
  source_title: z.string().default(''),
  duration: z.number().default(0),
  thumb: z.string().default(''),
  name: z.string().optional(),
  folder: z.string().optional(),
  url: z.string().optional(),
  error: z.string().default(''),
  detail: z.string().default(''),
  error_kind: z.string().optional(),
  retryable: z.boolean().optional(),
  warning: z.string().optional(),
  captions_skipped: z.boolean().optional(),
  srt: z.boolean().optional(),
  history: z.array(Attempt).default([]),
  pid: z.string().optional(),
  clip_id: z.number().optional(),
})
export type QueueItem = z.infer<typeof QueueItem>

export const Queue = z.looseObject({
  items: z.array(QueueItem),
  eta: z.number().nullable(),
  workers: z.number().optional(),
})
export type Queue = z.infer<typeof Queue>

export const Status = z.looseObject({
  version: z.string(),
  api: z.number(),
  ui: z.string(),
  ffmpeg: z.boolean().optional(),
  ytdlp: z.boolean().optional(),
  whisper: z.boolean().optional(),
  laughter: z.boolean().optional(),
  twitchdownloader: z.boolean().optional(),
  can_download_tools: z.boolean().optional(),
  live: z.string().default(''),
  hotkey: z.lazy(() => Hotkey).optional(),
})
export type Status = z.infer<typeof Status>

/** A background job (/api/job/{id}): only its public fields (server.job_public). */
export const Job = z.looseObject({
  id: z.string(),
  kind: z.string(),
  status: z.enum(['running', 'done', 'error', 'cancelled', 'paused']),
  step: z.string().default(''),
  progress: z.number().default(0),
  eta: z.number().nullable().optional(),
  log: z.array(z.string()).default([]),
  error: z.string().nullable().optional(),
  result: z.unknown().optional(),
})
export type Job = z.infer<typeof Job>

export const JobStarted = z.looseObject({ job: z.string(), already: z.boolean().optional() })
/** Just enough of a project or live session to hand it to Review. */
export const Ref = z.looseObject({ pid: z.string().optional(), id: z.string().optional(), title: z.string().optional() })

export const Source = z.looseObject({
  type: z.enum(['vod', 'live']),
  id: z.string(),
  status: z.string(),
  title: z.string().default(''),
  source: z.string().default(''),
  label: z.string().default(''),
  step: z.string().default(''),
  progress: z.number().default(0),
  eta: z.number().nullable().optional(),
  pid: z.string().default(''),
  error: z.string().default(''),
  recorded: z.number().default(0),
  moments: z.number().default(0),
  chat_state: z.string().default(''),
})
export type Source = z.infer<typeof Source>
export const Sources = z.looseObject({
  items: z.array(Source),
  limits: z.looseObject({ downloads: z.number(), lives: z.number() }),
})

export const RecentItem = z.looseObject({
  title: z.string(),
  source: z.string(),
  updated: z.number().default(0),
  duration: z.number().default(0),
  review: z.looseObject({ kept: z.number().default(0), skipped: z.number().default(0), todo: z.number().default(0) })
    .nullable().optional(),
})
export const Recent = z.array(RecentItem)
export type RecentItem = z.infer<typeof RecentItem>

export const Twitch = z.looseObject({
  signed_in: z.boolean().default(false),
  login: z.string().default(''),
  turbo: z.boolean().nullable().optional(),
  error: z.string().default(''),
  /** sign-in in a Twitch window needs the app window; in a browser only a pasted token works */
  window_login: z.boolean().default(false),
})
export const Picked = z.looseObject({ path: z.string().default('') })

export const Ok = z.looseObject({ ok: z.boolean().optional(), retried: z.number().optional() })

/** The clip hotkey (/api/hotkey, and status.hotkey). */
export const Hotkey = z.looseObject({
  supported: z.boolean().default(false),
  enabled: z.boolean().default(true),
  registered: z.boolean().default(false),
  keys: z.string().default('Ctrl+Alt+C'),
  error: z.string().default(''),
})
export type Hotkey = z.infer<typeof Hotkey>

/** Updates (/api/update and its actions): the updater's public state. */
export const Update = z.looseObject({
  current: z.string().default(''),
  state: z.enum(['idle', 'checking', 'downloading', 'ready', 'installing', 'error']).catch('idle'),
  available: z.boolean().default(false),
  latest: z.string().default(''),
  notes: z.string().default(''),
  page: z.string().default(''),
  progress: z.number().default(0),
  error: z.string().default(''),
  /** when it last checked (epoch seconds; 0 = never) */
  checked: z.number().default(0),
  can_install: z.boolean().default(false),
  token: z.boolean().default(false),
  token_hint: z.string().default(''),
  auto: z.boolean().default(true),
})
export type Update = z.infer<typeof Update>

export const Version = z.looseObject({
  version: z.string(),
  published: z.string().default(''),
  notes: z.string().default(''),
  page: z.string().default(''),
  current: z.boolean().default(false),
  newer: z.boolean().default(false),
  archive: z.boolean().default(false),
  installable: z.boolean().default(false),
})
export const Versions = z.looseObject({
  versions: z.array(Version).default([]),
  error: z.string().default(''),
  can_install: z.boolean().default(false),
})

/** This PC, for Settings › Performance (/api/perf). */
export const Perf = z.looseObject({
  cpus: z.number().default(0),
  mem_free: z.number().default(0),
  mem_total: z.number().default(0),
  waiting_for_memory: z.boolean().default(false),
  encoder: z.string().default(''),
  /** render speed per encoder, × real time */
  speeds: z.record(z.string(), z.number()).default({}),
})

/** Only the Performance part of the export settings (/api/export); the rest isn't ported yet. */
export const ExportPerf = z.looseObject({
  prefs: z.looseObject({
    renders: z.number().default(1),
    downloads: z.number().default(1),
    lives: z.number().default(2),
    priority: z.enum(['responsive', 'fast']).catch('responsive'),
  }),
})
export type ExportPerf = z.infer<typeof ExportPerf>

export const Storage = z.looseObject({ uninstaller: z.boolean().default(false) })

/** Settings › Storage: where each kind of file goes (/api/locations). */
export const Location = z.looseObject({
  key: z.string(),
  label: z.string(),
  path: z.string().default(''),
  /** the folder, or a description such as "Inside each project folder" */
  where: z.string().default(''),
  set: z.boolean().default(false),
  ok: z.boolean().default(true),
  problem: z.string().default(''),
  free: z.number().nullable().default(null),
  low: z.boolean().default(false),
  bytes: z.number().nullable().default(null),
})
export const Locations = z.looseObject({
  locations: z.array(Location).default([]),
  errors: z.record(z.string(), z.string()).default({}),
})
export const Cleared = z.looseObject({ freed: z.number().default(0), locations: z.array(Location).default([]) })

const Count = z.looseObject({ count: z.number().default(0), bytes: z.number().default(0) })
export const StorageUse = z.looseObject({
  vods: Count,
  clips: Count,
  workroot: z.string().default(''),
  uninstaller: z.boolean().default(false),
})
export const Deleted = z.looseObject({
  deleted: z.number().default(0),
  freed: z.number().default(0),
  failed: z.array(z.string()).default([]),
})

/** Settings › Storage › Clean up automatically (/api/cleanup). */
export const Cleanup = z.looseObject({
  config: z.looseObject({
    temp: z.boolean().default(true),
    skipped: z.boolean().default(false),
    vods: z.boolean().default(false),
    vod_days: z.number().default(7),
  }),
  last: z.looseObject({
    at: z.number().default(0),
    freed: z.number().default(0),
    removed: z.array(z.string()).default([]),
    failed: z.array(z.string()).default([]),
  }).default({ at: 0, freed: 0, removed: [], failed: [] }),
})
export type Cleanup = z.infer<typeof Cleanup>

/** Settings › Clip ranking: what was learned per streamer (/api/learning). */
const Trim = z.looseObject({ start: z.number().default(0), end: z.number().default(0), n: z.number().default(0) })
export type Trim = z.infer<typeof Trim>
const Reason = z.looseObject({ label: z.string(), n: z.number() })
export const Learning = z.looseObject({
  enabled: z.boolean().default(true),
  profiles: z.array(z.looseObject({
    streamer: z.string(),
    decisions: z.number().default(0),
    kept: z.number().default(0),
    posted: z.number().default(0),
    accept: z.number().nullable().default(null),
    added: z.number().default(0),
    trim: Trim.nullable().default(null),
    reasons: z.array(Reason).default([]),
  })).default([]),
  total: z.number().default(0),
  min_decisions: z.number().default(8),
})
export type Learning = z.infer<typeof Learning>

/** "What it learned" for one streamer (/api/learning/explain). */
export const Explain = z.looseObject({
  status: z.string().default(''),
  status_text: z.string().default(''),
  facts: z.looseObject({
    decisions: z.number().default(0),
    kept: z.number().default(0),
    skipped: z.number().default(0),
    posted: z.number().default(0),
    added: z.number().default(0),
    reasons: z.array(Reason).default([]),
    trim: Trim.nullable().default(null),
  }),
  compare: z.array(z.looseObject({ label: z.string(), kept: z.number(), skipped: z.number(), says: z.string() })).default([]),
  learned: z.array(z.looseObject({ label: z.string(), direction: z.string(), strength: z.string() })).default([]),
  rules: z.array(z.string()).default([]),
  method: z.string().default(''),
})
export type Explain = z.infer<typeof Explain>

/** Settings › Content check (/api/safety): what's checked, how it's censored, how flags are reviewed. */
export const Safety = z.looseObject({
  enabled: z.boolean().default(true),
  categories: z.record(z.string(), z.boolean()).default({}),
  category_info: z.array(z.looseObject({ key: z.string(), label: z.string(), severity: z.number().default(1) })).default([]),
  censor: z.enum(['off', 'mask', 'mute', 'bleep']).catch('off'),
  censor_categories: z.array(z.string()).default([]),
  hold: z.boolean().default(true),
  custom: z.array(z.string()).default([]),
  auto: z.boolean().default(true),
  notify: z.enum(['now', 'later']).catch('now'),
  sensitivity: z.enum(['all', 'skip_mild', 'serious']).catch('all'),
  export_flagged: z.enum(['ask', 'block', 'allow']).catch('ask'),
  remember_dismissed: z.boolean().default(true),
  auto_recheck: z.boolean().default(true),
})
export type Safety = z.infer<typeof Safety>

/** Settings › Hashtags (/api/hashtags): file-name tags and post tags, set separately, with a preview. */
const Tags = z.array(z.string()).default([])
const PerStreamer = z.record(z.string(), z.array(z.string())).default({})
export const Hashtags = z.looseObject({
  config: z.looseObject({
    file: z.looseObject({ enabled: z.boolean().default(true), streamer_tag: z.boolean().default(true), general: Tags,
      streamers: PerStreamer, max: z.number().default(4) }),
    post: z.looseObject({ auto: z.boolean().default(true), global: Tags, streamers: PerStreamer,
      platforms: z.record(z.string(), z.array(z.string())).default({}), max: z.number().default(8) }),
  }),
  suggestions: Tags,
  preview: z.looseObject({ file: z.string().default(''), post: z.record(z.string(), z.array(z.string())).default({}),
    post_on: z.boolean().default(true) }),
})
export type Hashtags = z.infer<typeof Hashtags>

/** Settings › Titles and captions (/api/titles). */
export const TitlePrefs = z.looseObject({
  pick: z.enum(['a', 'b']).catch('a'),
  hashtags: z.boolean().default(true),
  chat_titles: z.boolean().default(true),
})
export type TitlePrefs = z.infer<typeof TitlePrefs>

/** Settings › Caption words: the part of the export settings (/api/export) that changes caption text. */
export const ExportWords = z.looseObject({
  prefs: z.looseObject({
    replace: z.array(z.tuple([z.string(), z.string()])).default([]),
    vocab: z.array(z.string()).default([]),
  }),
})

/** Settings › Export format (/api/export): presets, the settings they give, and your changes on top. */
const Choice = z.looseObject({ key: z.string(), label: z.string(), note: z.string().default('') })
/** One watermark layer (watermarks.MARK_DEFAULTS). */
export const Mark = z.looseObject({
  kind: z.enum(['text', 'image']).catch('text'), text: z.string().default(''), image: z.string().default(''),
  pos: z.enum(['tl', 'tr', 'bl', 'br', 'c', 'free']).catch('tl'), x: z.number().default(0.5), y: z.number().default(0.5),
  size: z.number().default(100), opacity: z.number().default(70), margin: z.number().default(40), rotation: z.number().default(0),
  color: z.string().default('#FFFFFF'), font: z.string().default('classic'), outline: z.boolean().default(true),
  box: z.boolean().default(false), box_color: z.string().default('#141414'), start: z.number().default(0), end: z.number().default(0),
})
export type Mark = z.infer<typeof Mark>
export const WmPreset = z.looseObject({ name: z.string(), marks: z.array(Mark).default([]) })

/** A clipping campaign (campaigns.clean_one). */
export const Campaign = z.looseObject({
  id: z.string(), name: z.string().default(''), org: z.string().default(''), link: z.string().default(''), notes: z.string().default(''),
  caption: z.string().default(''), caption_where: z.enum(['end', 'start']).catch('end'), tags: z.array(z.string()).default([]),
  min_len: z.number().default(0), max_len: z.number().default(0),
  watermark: z.looseObject({ use: z.boolean().default(false), marks: z.array(Mark).default([]) }).default({ use: false, marks: [] }),
  discord: z.looseObject({
    webhook: z.string().default(''), auto: z.boolean().default(false), file: z.boolean().default(true), message: z.string().default(''),
  }).default({ webhook: '', auto: false, file: true, message: '' }),
})
export type Campaign = z.infer<typeof Campaign>
export const Campaigns = z.looseObject({
  list: z.array(Campaign).default([]), active: z.string().default(''),
  default_message: z.string().default('**{title}**\n{post_url}'), upload_mb: z.number().default(10),
  tokens: z.array(z.string()).default([]),
})
export type Campaigns = z.infer<typeof Campaigns>

export const WmImage = z.looseObject({ image: z.string() })
export const Shared = z.looseObject({ path: z.string().default(''), name: z.string().default('') })
export const Imported = z.discriminatedUnion('kind', [
  z.looseObject({ kind: z.literal('image'), image: z.string() }),
  z.looseObject({ kind: z.literal('watermark'), name: z.string(), marks: z.array(Mark).default([]) }),
  z.looseObject({ kind: z.literal('campaign'), name: z.string() }),
])

export const ExportFormat = z.looseObject({
  describe: z.string().default(''),
  presets: z.array(Choice).default([]),
  quality: z.array(Choice).default([]),
  tokens: z.array(z.string()).default([]),
  preview_name: z.string().default(''),
  settings: z.looseObject({
    scale: z.number().default(1), fps: z.number().default(0), vcodec: z.string().default('h264'),
    crf: z.number().default(20), vbitrate: z.number().default(0), abitrate: z.number().default(192),
    container: z.string().default('mp4'), loudness: z.number().default(-14),
  }),
  prefs: z.looseObject({
    preset: z.string().default('tiktok'), quality: z.string().default('balanced'),
    custom: z.record(z.string(), z.unknown()).default({}),
    subtitles: z.enum(['burn', 'burn_srt', 'srt']).catch('burn'),
    sounds: z.boolean().default(true), denoise: z.boolean().default(false), voice: z.boolean().default(false),
    template: z.string().default('{title}'),
    watermark: z.looseObject({ on: z.boolean().default(false), marks: z.array(Mark).default([]) }).default({ on: false, marks: [] }),
    wm_presets: z.array(WmPreset).default([]),
    replace: z.array(z.tuple([z.string(), z.string()])).default([]),
    cap_presets: z.array(z.looseObject({ name: z.string(), opts: z.record(z.string(), z.unknown()) })).default([]),
    frame_presets: z.array(z.looseObject({ name: z.string(), opts: z.record(z.string(), z.unknown()) })).default([]),
  }),
})
export type ExportFormat = z.infer<typeof ExportFormat>

/** A content-check flag on a clip (safety.py). */
export const Flag = z.looseObject({ label: z.string().default(''), text: z.string().default(''), status: z.string().default('open') })

/** A caption word (start and end in seconds of the video). */
export const Word = z.looseObject({ start: z.number(), end: z.number(), word: z.string().default('') })

/** A suggested clip, as /api/project and /api/clips send it (server.py; what you did to it is saved with the VOD). */
export const Clip = z.looseObject({
  id: z.number(), pid: z.string(), start: z.number(), end: z.number(),
  kind: z.string().default('hype'), state: z.enum(['todo', 'kept', 'skipped']).catch('todo'),
  title: z.string().default(''), titles: z.array(z.string()).default([]),
  description: z.string().optional(), hashtags: z.array(z.string()).optional(), emoji: z.string().default(''), post: z.string().default(''),
  reason: z.string().default(''), quality: z.number().default(0), vibe: z.string().default(''),
  thumb: z.string().default(''), vod: z.string().default(''), listened: z.boolean().default(false),
  manual: z.boolean().optional(), held: z.string().default(''),
  userTrim: z.boolean().optional(), userTitle: z.boolean().optional(), userPost: z.boolean().optional(),
  postText: z.string().default(''), gain: z.number().default(0), wordsEdited: z.boolean().optional(),
  words: z.array(Word).default([]), words_edit: z.array(Word).optional(), wordsOrig: z.array(Word).optional(),
  fps: z.number().default(30), orig: z.tuple([z.number(), z.number()]).optional(),
  safety: z.looseObject({ enabled: z.boolean().optional(), status: z.string().optional(), level: z.string().optional(),
    flags: z.array(Flag).default([]), open: z.number().default(0) }).optional(),
  check: z.looseObject({ excluded: z.boolean().default(false), fixes: z.array(z.unknown()).default([]) }).optional(),
})
export type Clip = z.infer<typeof Clip>

export const Project = z.looseObject({
  pid: z.string(), title: z.string().default(''), duration: z.number().default(0), vod: z.string().default(''),
  has_chat: z.boolean().default(false), chat_count: z.number().default(0),
  chat_stats: z.looseObject({ chatters: z.number().default(0) }).default({ chatters: 0 }),
  estimate: z.looseObject({ strong: z.number().nullish(), good: z.number().nullish() }).default({}),
  clips: z.array(Clip).default([]), gems: z.array(Clip).default([]),
  restored: z.boolean().default(false), pick_note: z.string().default(''),
  review: z.looseObject({ kept: z.number().default(0), skipped: z.number().default(0), todo: z.number().default(0) }).default({ kept: 0, skipped: 0, todo: 0 }),
  detect_text: z.string().default(''),
  origin: z.looseObject({ link: z.string().optional() }).default({}),
})
export type Project = z.infer<typeof Project>

export const ClipsUpdate = z.looseObject({ clips: z.array(Clip).default([]), gems: z.array(Clip).default([]), listening: z.boolean().default(false) })

export const PreviewCopy = z.looseObject({ ready: z.boolean().default(false), job: z.string().optional() })

/** What the editing timeline draws for a stretch of the video (/api/strip): loudness, laughter etc., chat. */
export const Strip = z.looseObject({
  a: z.number(), b: z.number(), hz: z.number().default(10), start: z.number().default(0),
  wave: z.array(z.number()).default([]), events: z.record(z.string(), z.array(z.number())).default({}),
  chat: z.array(z.number()).default([]),
})
export type Strip = z.infer<typeof Strip>

/** Layout › Follow the face: where the crop's centre is over the clip ([seconds in, share of width]). */
export const TrackPath = z.looseObject({
  path: z.array(z.tuple([z.number(), z.number()])).default([]), face_y: z.number().nullish(), face_h: z.number().nullish(),
  reason: z.string().default(''), coverage: z.number().default(0),
})

/** Layout › Auto-frame (/api/framing/<project>): the layout that fits, or why there's nothing to suggest. */
export const FramingSuggest = z.looseObject({
  layout: z.string().default(''), cam: z.array(z.number()).length(4).optional(), track: z.string().optional(),
  reason: z.string().default(''), confidence: z.number().default(1),
})

/** /api/check: the content check's answer for the clips it looked at. */
export const Rechecked = z.looseObject({
  clips: z.array(z.looseObject({ id: z.number(), safety: Clip.shape.safety, check: Clip.shape.check })).default([]),
})
