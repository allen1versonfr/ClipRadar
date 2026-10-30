import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import { ToastProvider } from '../../components/Toasts'
import { RENDER_DEFAULTS, type RenderOpts } from '../../prefs'
import { LayoutPanel } from './StylePanels'

afterEach(cleanup)

let last: RenderOpts
function Harness() {
  const [opts, setOpts] = useState<RenderOpts>({ ...RENDER_DEFAULTS } as RenderOpts)
  last = opts
  return <LayoutPanel opts={opts} set={p => setOpts(o => ({ ...o, ...p }) as RenderOpts)} pid="p" frameAt={0}
                      framePresets={[]} trackNote="" onSavePresets={async () => {}} />
}

describe('Layout › Facecams', () => {
  it('adds a facecam for each person on camera, up to 4, and removes one', () => {
    render(<ToastProvider><Harness /></ToastProvider>)
    expect(screen.getAllByTestId('rv-cambox')).toHaveLength(1)
    fireEvent.click(screen.getByTestId('rv-add-cam'))
    expect(screen.getAllByTestId('rv-cambox')).toHaveLength(2)
    expect(last.cams).toEqual([[0.75, 0, 0.25, 0.25]])               // a corner the first one doesn't use
    expect(screen.getByRole('button', { name: 'Cam 2' }).getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByText(/side by side above the game/)).toBeTruthy()
    fireEvent.click(screen.getByTestId('rv-add-cam'))
    fireEvent.click(screen.getByTestId('rv-add-cam'))
    expect(last.cams).toHaveLength(3)
    expect(screen.queryByTestId('rv-add-cam')).toBeNull()             // 4 is the most
    fireEvent.click(screen.getByRole('button', { name: 'Cam 1' }))
    fireEvent.click(screen.getByTestId('rv-remove-cam'))              // removing the first: the next one takes its place
    expect(last.cam).toEqual([0.75, 0, 0.25, 0.25])
    expect(last.cams).toHaveLength(2)
  })
})
