import { type ReactNode, useEffect, useMemo, useRef, useState } from 'react'
import type { Status } from '../../api/schemas'
import { WatermarkPanel } from '../watermark/WatermarkPanel'
import { CampaignsPanel } from './CampaignsPanel'
import { CaptionWordsPanel } from './CaptionWordsPanel'
import { ContentCheckPanel } from './ContentCheckPanel'
import { ExportFormatPanel } from './ExportFormatPanel'
import { HashtagsPanel } from './HashtagsPanel'
import { LearningPanel } from './LearningPanel'
import { StoragePanel } from './StoragePanel'
import { TitlesPanel } from './TitlesPanel'
import { AboutPanel, HotkeyPanel, PerformancePanel, ToolsPanel } from './SystemPanels'
import { TikTokPanel } from './TikTokPanel'
import { TwitchPanel } from './TwitchPanel'
import { UpdatesPanel } from './UpdatesPanel'
import { SECTIONS, GROUPS, matches } from './sections'

/** Settings: every section on one page, in four groups, with a menu that follows where you are and a search
 *  across them. #settings/<section> (twitch, updates, …) opens it at that section. */
export function SettingsView({ status, refreshStatus, section }: { status: Status | undefined; refreshStatus: () => Promise<void>; section?: string | undefined }) {
  const [tagsKey, setTagsKey] = useState(0)
  const [wmKey, setWmKey] = useState(0)          // Campaigns changed: the active campaign's watermark may be the one shown
  const [cpKey, setCpKey] = useState(0)          // Watermark changed or imported a campaign
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(section ?? SECTIONS[0]!.id)
  const [text, setText] = useState<Record<string, string>>({})     // what each section says, for the search
  const wraps = useRef(new Map<string, HTMLElement>())

  const panels: Record<string, ReactNode> = {
    export: <ExportFormatPanel />,
    watermark: <WatermarkPanel reloadKey={wmKey} onCampaigns={() => setCpKey(k => k + 1)} />,
    titles: <TitlesPanel onHashtags={() => setTagsKey(k => k + 1)} />,
    words: <CaptionWordsPanel />,
    tags: <HashtagsPanel reloadKey={tagsKey} />,
    safety: <ContentCheckPanel />,
    tiktok: <TikTokPanel />,
    campaigns: <CampaignsPanel reloadKey={cpKey} onChange={() => setWmKey(k => k + 1)} />,
    learn: <LearningPanel />,
    twitch: <TwitchPanel />,
    live: <HotkeyPanel status={status} refreshStatus={refreshStatus} />,
    perf: <PerformancePanel />,
    tools: <ToolsPanel status={status} refreshStatus={refreshStatus} />,
    storage: <StoragePanel refreshStatus={refreshStatus} />,
    updates: <UpdatesPanel version={status?.version} />,
    about: <AboutPanel />,
  }

  const go = (id: string) => {
    wraps.current.get(id)?.scrollIntoView({ block: 'start' })
    setActive(id)
  }
  useEffect(() => { if (section) go(section) }, [section])

  // the menu follows the section you're reading
  useEffect(() => {
    if (typeof IntersectionObserver === 'undefined') return
    const seen = new Map<string, number>()
    const io = new IntersectionObserver(entries => {
      for (const e of entries) seen.set((e.target as HTMLElement).dataset.section!, e.isIntersecting ? e.boundingClientRect.top : Infinity)
      const top = [...seen].filter(([, y]) => y !== Infinity).sort((a, b) => a[1] - b[1])[0]
      if (top) setActive(top[0])
    }, { rootMargin: '0px 0px -60% 0px' })
    wraps.current.forEach(el => io.observe(el))
    return () => io.disconnect()
  }, [])

  // searching reads each section's own words too, so "opacity" finds Watermark
  useEffect(() => {
    if (!query) return
    setText(Object.fromEntries([...wraps.current].map(([id, el]) => [id, el.textContent ?? ''])))
  }, [query])
  const shown = useMemo(() => new Set(SECTIONS.filter(s => matches(s, query, text[s.id] ?? '')).map(s => s.id)), [query, text])

  return (
    <section className="settings" data-testid="settings" aria-labelledby="settings-title">
      <nav className="set-nav" aria-label="Settings sections">
        <h1 id="settings-title">Settings</h1>
        <input className="input" type="search" placeholder="Search settings" aria-label="Search settings" value={query}
               onChange={e => setQuery(e.target.value)} data-testid="set-search" />
        {GROUPS.map(g => {
          const items = SECTIONS.filter(s => s.group === g && shown.has(s.id))
          return items.length > 0 && (
            <div key={g} className="set-nav-group">
              <span className="set-nav-label">{g}</span>
              {items.map(s => (
                <a key={s.id} href={`#settings/${s.id}`} aria-current={active === s.id ? 'true' : undefined} className={active === s.id ? 'on' : ''}
                   onClick={e => { e.preventDefault(); history.replaceState(null, '', `#settings/${s.id}`); go(s.id) }}>{s.title}</a>
              ))}
            </div>
          )
        })}
      </nav>
      <div className="set-body">
        {query && !shown.size && <p className="empty" data-testid="set-no-match">No settings match “{query}”.</p>}
        {GROUPS.map(g => {
          const items = SECTIONS.filter(s => s.group === g)
          const any = items.some(s => shown.has(s.id))
          return (
            <div key={g} className="set-group" role="group" aria-label={g} hidden={!any}>
              <p className="set-group-label" aria-hidden="true">{g}</p>
              {items.map(s => (
                <div key={s.id} className="set-section" data-section={s.id} hidden={!shown.has(s.id)}
                     ref={el => { if (el) wraps.current.set(s.id, el); else wraps.current.delete(s.id) }}>
                  {panels[s.id]}
                </div>
              ))}
            </div>
          )
        })}
      </div>
    </section>
  )
}
