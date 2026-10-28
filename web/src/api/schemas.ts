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
