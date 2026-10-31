import { useSyncExternalStore } from 'react'

// What the app's frame shows about the screens: Review tells it how many clips are left to review
// (the badge on Review in the menu, as in the original page).

/** Screen pixels per CSS pixel on a canvas: the display's, times the app's big-screen scale (--z in tokens.css). */
export const canvasScale = (): number =>
  (window.devicePixelRatio || 1) * (parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--z')) || 1)

let todo = 0
const listeners = new Set<() => void>()
export function setReviewTodo(n: number): void {
  if (n === todo) return
  todo = n
  listeners.forEach(f => f())
}
export const useReviewTodo = () => useSyncExternalStore(f => { listeners.add(f); return () => listeners.delete(f) }, () => todo)
