import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ConfirmProvider } from '../../components/Confirm'
import { ToastProvider } from '../../components/Toasts'
import { GpuSupport } from './SystemPanels'

type Call = [string, Record<string, unknown> | undefined]
let calls: Call[]
let gpu: Record<string, unknown>

beforeEach(() => {
  calls = []
  gpu = { nvidia: 'NVIDIA GeForce RTX 3060', whisper_gpu: false, cuda_libs: false, pack: false, whisper_device: 'cpu',
          encoder: 'h264_nvenc', hw_decode: true, download_mb: 1300 }
  vi.stubGlobal('fetch', vi.fn(async (path: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : undefined
    calls.push([path, body])
    const answer = path === '/api/gpu/install' ? { job: 'j1' }
      : path === '/api/job/j1' ? { status: 'running', progress: 0.25, log: [], step: '' }
      : gpu
    return new Response(JSON.stringify(answer), { status: 200 })
  }))
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

const show = () => render(<ToastProvider><ConfirmProvider><GpuSupport /></ConfirmProvider></ToastProvider>)

describe('Settings › Performance › Graphics card', () => {
  it('says what runs where', async () => {
    show()
    const s = await screen.findByTestId('gpu-state')
    expect(s.textContent).toContain('NVIDIA GeForce RTX 3060')
    expect(s.textContent).toContain('encoded on the GPU (h264_nvenc)')
    expect(s.textContent).toContain('decoded on the GPU')
    expect(s.textContent).toContain('Whisper on the CPU')
  })

  it('offers GPU support for Whisper on an NVIDIA card without it, after asking', async () => {
    show()
    fireEvent.click(await screen.findByTestId('gpu-install'))
    expect(calls.some(([p]) => p === '/api/gpu/install')).toBe(false)       // asks first: 1.3 GB
    fireEvent.click(await screen.findByTestId('confirm-yes'))
    await waitFor(() => expect(calls.some(([p]) => p === '/api/gpu/install')).toBe(true))
    await waitFor(() => expect(screen.getByTestId('gpu-install').textContent).toMatch(/Downloading/))
  })

  it("doesn't offer it when Whisper already runs on the card, or there's no NVIDIA card", async () => {
    gpu = { ...gpu, whisper_gpu: true, cuda_libs: true, pack: true }
    show()
    expect((await screen.findByTestId('gpu-state')).textContent).toContain('Whisper on the NVIDIA GeForce RTX 3060')
    expect(screen.queryByTestId('gpu-install')).toBeNull()
    expect(screen.getByTestId('gpu-remove')).toBeTruthy()
    cleanup()
    gpu = { ...gpu, nvidia: '', whisper_gpu: false, pack: false, encoder: 'CPU' }
    show()
    expect((await screen.findByTestId('gpu-state')).textContent).toContain('No NVIDIA card found')
    expect(screen.queryByTestId('gpu-install')).toBeNull()
  })
})
