import { api } from '../../api/client'
import { Ok } from '../../api/schemas'

/** Open a web page in the default browser: Clip Radar opens it, so it works in the app window too. */
export async function openUrl(url: string, toast: (msg: string, kind?: '' | 'err') => void) {
  if (!url) return
  try {
    await api(Ok, '/api/open_url', { url })
  } catch (e) {
    toast(e instanceof Error ? e.message : String(e), 'err')
  }
}
