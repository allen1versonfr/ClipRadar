import { z } from 'zod'
import { api } from '../../api/client'

// Spelling fixes as you type a title or post caption: the same list and rules as src/spelling.py (the
// server's titles use it too), fetched once from /api/spelling. Only words on the list change, so slang,
// names and emotes stay; hashtags, @names and links are never touched.

export const Spelling = z.looseObject({ fixes: z.record(z.string(), z.string()).default({}) })
export type Fixes = Record<string, string>

let loading: Promise<Fixes> | null = null
/** The fix list, fetched once (an empty list when Clip Radar can't be reached: nothing is suggested). */
export function loadFixes(): Promise<Fixes> {
  loading ??= api(Spelling, '/api/spelling').then(r => r.fixes).catch(() => { loading = null; return {} })
  return loading
}

const cased = (src: string, fix: string) =>
  src.length > 1 && src === src.toUpperCase() ? fix.toUpperCase() : src[0] === src[0]!.toUpperCase() ? fix[0]!.toUpperCase() + fix.slice(1) : fix

/** [text with the fixes made, [was, now] pairs]: spelling.fix in src/spelling.py. */
export function fixSpelling(text: string, fixes: Fixes): [string, Array<[string, string]>] {
  const changes: Array<[string, string]> = []
  if (!text) return [text, changes]
  const one = (w: string) => {
    const k = w.toLowerCase()
    if (k === 'i') { if (w === 'i') { changes.push([w, 'I']); return 'I' } return w }
    const rep = fixes[k]
    if (!rep) return w
    const now = cased(w, rep)
    if (now !== w) changes.push([w, now])
    return now
  }
  const out = text.split(/(https?:\/\/\S+|www\.\S+)/).map((p, i) => (i % 2 ? p
    : p.replace(/(?<![\w#@/.'’])([A-Za-z]+)(?![\w'’]|\.\w)/g, (_m, w: string) => one(w)))).join('')
  const fixedI = out.replace(/(?<![\w#@])i(?=['’][a-z])['’][a-z]+/g, m => { changes.push([m, 'I' + m.slice(1)]); return 'I' + m.slice(1) })
  return [fixedI, changes]
}
