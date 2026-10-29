import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { App } from './App'
import { ConfirmProvider } from './components/Confirm'
import { ToastProvider } from './components/Toasts'

afterEach(() => { cleanup(); vi.unstubAllGlobals(); location.hash = '' })

function serve(running: boolean, calls: string[]) {
  vi.stubGlobal('fetch', vi.fn(async (path: string) => {
    calls.push(path)
    const answer = path === '/api/status' ? { version: '9.9.9', api: 1, ui: 'web', running, lives: [{ id: 's1', title: 'chan', status: 'recording' }] }
      : path === '/api/queue' ? { items: [{ id: 'q', status: 'rendering', stage: 'rendering', history: [] }], eta: null }
      : path === '/api/update' ? { available: true, latest: '10.0.0', state: 'idle' }
      : path === '/api/recent' || path === '/api/sources' ? [] : { ok: true }
    return new Response(JSON.stringify(path === '/api/sources' ? { items: [] } : answer), { status: 200 })
  }))
}
const app = () => render(<ToastProvider><ConfirmProvider><App /></ConfirmProvider></ToastProvider>)

describe('the app frame', () => {
  it('closing the window while something runs asks first (window.askQuit), and Quit stops Clip Radar', async () => {
    const calls: string[] = []
    serve(true, calls)
    app()
    await waitFor(() => expect(screen.getByTestId('status-bar').textContent).toContain('Rendering 1 clip'))
    expect(screen.getByTestId('status-bar').textContent).toContain('Recording chan')
    expect(screen.getByTestId('sb-update').textContent).toBe('Update to 10.0.0')
    expect(within(screen.getByTestId('nav-exports')).getByText('1')).toBeTruthy()          // the badge
    const asked = (window as unknown as { askQuit: () => Promise<void> }).askQuit()
    fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Cancel' }))
    await asked
    expect(calls).not.toContain('/api/quit')
    fireEvent.click(screen.getByTestId('quit'))
    fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Quit' }))
    expect(await screen.findByText('Clip Radar has stopped. You can close this window.')).toBeTruthy()
    expect(calls).toContain('/api/quit')
  })
})
