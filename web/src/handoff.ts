// Opening a video or a live session from Home: Review in this UI. The original page's screens are
// still reachable at /legacy/ (legacyViewUrl), for what only it has.

export function go(url: string): void {
  location.assign(url)
}

export const reviewUrl = (pid: string) => `#review/${encodeURIComponent(pid)}`
export const liveUrl = (id: string) => `#review/live/${encodeURIComponent(id)}`
export const legacyViewUrl = (view: 'home' | 'review' | 'exports' | 'settings') => `/legacy/#view=${view}`
