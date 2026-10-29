// Settings' sections, in the order they're shown, in four groups, with the words people look for them by.

export const GROUPS = ['Clips', 'Posting', 'Finding clips', 'App'] as const
export type Group = (typeof GROUPS)[number]
export interface Section { id: string; title: string; group: Group; words: string }

export const SECTIONS: Section[] = [
  { id: 'export', title: 'Export format', group: 'Clips', words: 'preset quality resolution size codec h264 h265 vp9 fps frame rate bitrate captions srt subtitles loudness volume noise voice file name template' },
  { id: 'watermark', title: 'Watermark', group: 'Clips', words: 'logo text image overlay position opacity handle channel name' },
  { id: 'titles', title: 'Titles and captions', group: 'Clips', words: 'title a b streamer name chat post caption hashtags description' },
  { id: 'words', title: 'Caption words', group: 'Clips', words: 'replacements spelling vocabulary names emotes whisper' },
  { id: 'tags', title: 'Hashtags', group: 'Clips', words: 'hashtags tags file name post caption streamer' },
  { id: 'safety', title: 'Content check', group: 'Clips', words: 'flags slurs swearing censor bleep mute safety tiktok rules' },
  { id: 'tiktok', title: 'TikTok', group: 'Posting', words: 'tiktok studio upload post press post browser edge chrome' },
  { id: 'campaigns', title: 'Campaigns', group: 'Posting', words: 'campaign clipping server discord webhook rules submit' },
  { id: 'learn', title: 'Clip ranking', group: 'Finding clips', words: 'ranking learning picks keeps skips trims forget tuned' },
  { id: 'twitch', title: 'Twitch sign-in', group: 'Finding clips', words: 'twitch sign in login turbo ads token subscriber' },
  { id: 'live', title: 'Live clipping', group: 'Finding clips', words: 'live hotkey shortcut keyboard clip last 30 seconds' },
  { id: 'perf', title: 'Performance', group: 'App', words: 'renders downloads at once speed gpu encoder nvenc priority responsive faster' },
  { id: 'tools', title: 'Downloads and tools', group: 'App', words: 'ffmpeg yt-dlp twitchdownloader whisper laughter download quality cookies browser' },
  { id: 'storage', title: 'Storage', group: 'App', words: 'folders disk space cleanup delete vods clips projects' },
  { id: 'updates', title: 'Updates', group: 'App', words: 'update version new release automatic token' },
  { id: 'about', title: 'About', group: 'App', words: 'about version log uninstall previous ui old look legacy' },
]

/** A section matches a search when every word typed is in its title, its keywords or what it says. */
export function matches(s: Section, query: string, text = ''): boolean {
  const q = query.trim().toLowerCase()
  if (!q) return true
  const hay = `${s.title} ${s.group} ${s.words} ${text}`.toLowerCase()
  return q.split(/\s+/).every(w => hay.includes(w))
}
