import type { Status } from '../../api/schemas'
import { legacyViewUrl } from '../../handoff'
import { StoragePanel } from './StoragePanel'
import { AboutPanel, HotkeyPanel, PerformancePanel, ToolsPanel } from './SystemPanels'
import { TwitchPanel } from './TwitchPanel'
import { UpdatesPanel } from './UpdatesPanel'

/** The sections still only in the current UI; ported one at a time (docs/react-migration.md). */
export const NOT_PORTED = ['Export format and watermark', 'Campaigns', 'Caption words', 'Titles and captions', 'Hashtags',
  'Content check', 'Clip ranking']

export function SettingsView({ status, refreshStatus }: { status: Status | undefined; refreshStatus: () => Promise<void> }) {
  return (
    <section className="settings" data-testid="settings">
      <h1>Settings</h1>
      <div className="panel more-set" data-testid="more-settings">
        <p><b>More settings are in the current UI:</b> {NOT_PORTED.join(', ')}. They work as before, and anything you change there applies here too.</p>
        <a className="btn" href={legacyViewUrl('settings')}>Open them in the current UI</a>
      </div>
      <PerformancePanel />
      <TwitchPanel />
      <HotkeyPanel status={status} refreshStatus={refreshStatus} />
      <ToolsPanel status={status} refreshStatus={refreshStatus} />
      <StoragePanel refreshStatus={refreshStatus} />
      <UpdatesPanel version={status?.version} />
      <AboutPanel />
    </section>
  )
}
