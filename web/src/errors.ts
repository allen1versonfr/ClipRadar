// Failures in the UI go to Clip Radar's error log (errors.jsonl, Settings › About) with the server's own,
// so they can be fixed later: script errors, promises nobody caught, and answers a screen couldn't use.
// Sent with fetch directly (not api()), so a failure here can never report itself again.

export function reportError(message: string, detail = ''): void {
  try {
    void fetch('/api/client_error', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, detail, page: location.hash || location.pathname, ui: 'web' }),
    }).catch(() => {})
  } catch {
    // no server to tell: nothing more to do
  }
}

let installed = false

/** Report every error nobody caught, once per page. */
export function installErrorReporting(): void {
  if (installed) return
  installed = true
  window.addEventListener('error', e => {
    const err = e.error as unknown
    reportError(e.message || 'Script error', err instanceof Error ? (err.stack ?? '') : `${e.filename}:${e.lineno}:${e.colno}`)
  })
  window.addEventListener('unhandledrejection', e => {
    const r = e.reason as unknown
    reportError(r instanceof Error ? r.message : String(r), r instanceof Error ? (r.stack ?? '') : '')
  })
}
