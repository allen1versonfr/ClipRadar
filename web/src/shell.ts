import { useSyncExternalStore } from 'react'

// What the app's frame shows about the screens: Review tells it how many clips are left to review
// (the badge on Review in the menu, as in the original page).

let todo = 0
const listeners = new Set<() => void>()
export function setReviewTodo(n: number): void {
  if (n === todo) return
  todo = n
  listeners.forEach(f => f())
}
export const useReviewTodo = () => useSyncExternalStore(f => { listeners.add(f); return () => listeners.delete(f) }, () => todo)
