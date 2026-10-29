import { useEffect, useState } from 'react'
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
import { TwitchPanel } from './TwitchPanel'
import { UpdatesPanel } from './UpdatesPanel'

/** #settings/<section> (twitch, updates, …) opens Settings at that section. */
export function SettingsView({ status, refreshStatus, section }: { status: Status | undefined; refreshStatus: () => Promise<void>; section?: string | undefined }) {
  useEffect(() => {
    if (section) document.getElementById(`set-${section}`)?.closest('section')?.scrollIntoView({ block: 'start' })
  }, [section])
  const [tagsKey, setTagsKey] = useState(0)
  const [wmKey, setWmKey] = useState(0)          // Campaigns changed: the active campaign's watermark may be the one shown
  const [cpKey, setCpKey] = useState(0)          // Watermark changed or imported a campaign
  return (
    <section className="settings" data-testid="settings">
      <h1>Settings</h1>
      <ExportFormatPanel />
      <WatermarkPanel reloadKey={wmKey} onCampaigns={() => setCpKey(k => k + 1)} />
      <CampaignsPanel reloadKey={cpKey} onChange={() => setWmKey(k => k + 1)} />
      <CaptionWordsPanel />
      <PerformancePanel />
      <TitlesPanel onHashtags={() => setTagsKey(k => k + 1)} />
      <HashtagsPanel reloadKey={tagsKey} />
      <ContentCheckPanel />
      <TwitchPanel />
      <LearningPanel />
      <HotkeyPanel status={status} refreshStatus={refreshStatus} />
      <ToolsPanel status={status} refreshStatus={refreshStatus} />
      <StoragePanel refreshStatus={refreshStatus} />
      <UpdatesPanel version={status?.version} />
      <AboutPanel />
    </section>
  )
}
