import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { Seg } from './Seg'
import { Help, Tip } from './Tip'

afterEach(cleanup)

describe('a setting explained when the pointer rests on it (5.7.4)', () => {
  it("each option's explanation goes with it, and is read out with it", () => {
    render(<Seg label="Quality" value="fast" onChange={() => {}}
                options={[['fast', 'Fast', 'Quickest to make.'], ['high', 'Best', 'The sharpest picture.']]} />)
    const fast = screen.getByRole('radio', { name: 'Fast' })
    const tip = document.getElementById(fast.getAttribute('aria-describedby')!)
    expect(tip?.getAttribute('role')).toBe('tooltip')
    expect(tip?.textContent).toBe('Quickest to make.')
  })
  it('an option without one is left as it is', () => {
    render(<Seg label="Size" value="a" onChange={() => {}} options={[['a', 'A'], ['b', 'B']]} />)
    expect(screen.getByRole('radio', { name: 'A' }).getAttribute('aria-describedby')).toBeNull()
    expect(screen.queryByRole('tooltip')).toBeNull()
  })
  it('a "?" for a setting whose control carries none', () => {
    render(<><span>Size</span><Help label="Size" text="Original copies the picture." /></>)
    const dot = screen.getByRole('button', { name: 'What this does' })
    // its name doesn't repeat the setting's: looking the setting up by name finds the setting, not the "?"
    expect(screen.queryByLabelText(/Size/)).toBeNull()
    expect(document.getElementById(dot.getAttribute('aria-describedby')!)?.textContent).toBe('Original copies the picture.')
  })
  it('no text: just the control', () => {
    render(<Tip text=""><button>Go</button></Tip>)
    expect(screen.getByRole('button', { name: 'Go' }).parentElement?.className ?? '').not.toContain('tip')
  })
})
