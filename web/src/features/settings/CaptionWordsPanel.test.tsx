import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ToastProvider } from '../../components/Toasts'
import { CaptionWordsPanel, parseReplace, parseVocab, replaceText } from './CaptionWordsPanel'

let prefs: Record<string, unknown>
let saved: Array<Record<string, unknown>>

beforeEach(() => {
  saved = []
  prefs = { replace: [['gonna', 'going to']], vocab: ['Kai', 'Mafia'] }
  vi.stubGlobal('fetch', vi.fn(async (_path: string, init?: RequestInit) => {
    if (init?.body) {
      const b = JSON.parse(String(init.body)) as Record<string, unknown>
      saved.push(b)
      Object.assign(prefs, b)
    }
    return new Response(JSON.stringify({ prefs }), { status: 200 })
  }))
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('Caption words', () => {
  it('shows the replacements and words, and saves each when you leave its box', async () => {
    render(<ToastProvider><CaptionWordsPanel /></ToastProvider>)
    const rep = await screen.findByPlaceholderText(/word = replacement/)
    await waitFor(() => expect((rep as HTMLTextAreaElement).value).toBe('gonna = going to'))
    fireEvent.change(rep, { target: { value: 'gonna = going to\nuhh =\nlol' } })
    expect(saved).toEqual([])
    fireEvent.blur(rep)
    await waitFor(() => expect(saved).toEqual([{ replace: [['gonna', 'going to'], ['uhh', ''], ['lol', '']] }]))
    const voc = screen.getByPlaceholderText(/Names, places/)
    expect((voc as HTMLTextAreaElement).value).toBe('Kai, Mafia')
    fireEvent.change(voc, { target: { value: 'Kai, Mafia,\nSpringfield' } })
    fireEvent.blur(voc)
    await waitFor(() => expect(saved.at(-1)).toEqual({ vocab: ['Kai', 'Mafia', 'Springfield'] }))
  })
})

describe('parsing', () => {
  it('splits a replacement at the first "=", and drops blank lines', () => {
    expect(parseReplace('a = b\n\n  = x\nx=y=z\ndrop')).toEqual([['a', 'b'], ['x', 'y=z'], ['drop', '']])
    expect(replaceText([['a', 'b'], ['c', '']])).toBe('a = b\nc = ')
    expect(parseVocab(' one,two\n, three ,')).toEqual(['one', 'two', 'three'])
  })
})
