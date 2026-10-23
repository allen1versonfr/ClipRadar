// Until Review is in the new UI, opening a video or a live session hands over to the original page,
// which opens it straight away (see openFromHash in src/ui/app.js).

export function go(url: string): void {
  location.assign(url)
}

export const reviewUrl = (pid: string) => `/legacy/#open=${encodeURIComponent(pid)}`
export const liveUrl = (id: string) => `/legacy/#live=${encodeURIComponent(id)}`
export const legacyViewUrl = (view: 'home' | 'review' | 'exports' | 'settings') => `/legacy/#view=${view}`
