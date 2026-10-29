import { readdirSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

// Windows (where Clip Radar is built) can't tell Overview.tsx from overview.ts: two such files break the
// build there while it passes everywhere else. Imports are matched without the extension, so it's the
// name that counts.
function names(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap(e => (e.isDirectory() ? names(join(dir, e.name)) : [join(dir, e.name)]))
}

describe('source file names', () => {
  it("no two differ only in capital letters or extension (Windows can't tell them apart)", () => {
    const seen = new Map<string, string>(), clash: string[] = []
    for (const f of names(join(__dirname))) {
      const key = f.replace(/\.(test\.)?(tsx?|css)$/, m => (m.startsWith('.test.') ? '.test' : '')).toLowerCase()
      const prev = seen.get(key)
      if (prev && prev !== f) clash.push(`${prev} / ${f}`)
      seen.set(key, f)
    }
    expect(clash).toEqual([])
  })
})
