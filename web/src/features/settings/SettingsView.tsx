import { useState } from 'react'
import type { Status } from '../../api/schemas'
import { legacyViewUrl } from '../../handoff'
import { CaptionWordsPanel } from './CaptionWordsPanel'
import { ContentCheckPanel } from './ContentCheckPanel'
import { HashtagsPanel } from './HashtagsPanel'
import { LearningPanel } from './LearningPanel'
import { StoragePanel } from './StoragePanel'
import { TitlesPanel } from './TitlesPanel'
import { AboutPanel, HotkeyPanel, PerformancePanel, ToolsPanel } from './SystemPanels'
import { TwitchPanel } from './TwitchPanel'
import { UpdatesPanel } from './UpdatesPanel'

/** The sections still only in the current UI; ported one at a time (docs/react-migration.md). */
export const NOT_PORTED = ['Export format and watermark', 'Campaigns']

export function SettingsView({ status, refreshStatus }: { status: Status | undefined; refreshStatus: () => Promise<void> }) {
  const [tagsKey, setTagsKey] = useState(0)
  return (
    <section className="settings" data-testid="settings">
      <h1>Settings</h1>
      <div className="panel more-set" data-testid="more-settings">
        <p><b>More settings are in the current UI:</b> {NOT_PORTED.join(', ')}. They work as before, and anything you change there applies here too.</p>
        <a className="btn" href={legacyViewUrl('settings')}>Open them in the current UI</a>
      </div>
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
