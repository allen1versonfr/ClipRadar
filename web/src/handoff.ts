// Opening a video or a live session from Home in Review.

export function go(url: string): void {
  location.assign(url)
}

export const reviewUrl = (pid: string) => `#review/${encodeURIComponent(pid)}`
export const liveUrl = (id: string) => `#review/live/${encodeURIComponent(id)}`