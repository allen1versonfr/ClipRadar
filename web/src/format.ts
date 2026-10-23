// Number and time wording shared by the screens, matching the original page's (src/ui/app.js).

export const fmtBytes = (b: number) =>
  b >= 1e9 ? (b / 1e9).toFixed(1) + ' GB' : b >= 1e6 ? Math.round(b / 1e6) + ' MB' : b > 0 ? Math.max(1, Math.round(b / 1e3)) + ' KB' : '0 MB'

export const plural = (n: number, word: string, many = word + 's') => `${n} ${n === 1 ? word : many}`

/** "just now", "5 min ago", "3 h ago", "2 d ago" for an epoch time in seconds */
export function ago(t: number, now = Date.now() / 1000): string {
  const s = now - t
  return s < 90 ? 'just now' : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`
}
