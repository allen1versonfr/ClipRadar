import { describe, expect, it } from 'vitest'
import { glide, wheelPixels, zoomFactor } from './Timeline'

describe('timeline scrolling', () => {
  it('reads wheel movement in pixels whatever the device reports', () => {
    expect(wheelPixels({ deltaX: 0, deltaY: 3, deltaMode: 1 }, 800)).toEqual({ x: 0, y: 48 })     // lines (Firefox mice)
    expect(wheelPixels({ deltaX: 1, deltaY: 0, deltaMode: 2 }, 800)).toEqual({ x: 800, y: 0 })   // pages
    expect(wheelPixels({ deltaX: 2, deltaY: -7, deltaMode: 0 }, 800)).toEqual({ x: 2, y: -7 })   // pixels (trackpads)
  })
  it('zooms in proportion to how far the wheel turned, the same both ways', () => {
    expect(zoomFactor(0)).toBe(1)
    expect(zoomFactor(100)).toBeGreaterThan(1)
    expect(zoomFactor(100) * zoomFactor(-100)).toBeCloseTo(1)
    expect(zoomFactor(4)).toBeLessThan(1.01)                    // a trackpad pinch step is a small step
    expect(zoomFactor(10_000)).toBe(zoomFactor(240))            // a huge jump is capped
  })
  it('glides toward where the view is going and lands exactly', () => {
    const from = { a: 0, b: 60 }, to = { a: 30, b: 90 }
    const one = glide(from, to, 16, 800)
    expect(one.a).toBeGreaterThan(0)
    expect(one.a).toBeLessThan(30)
    expect(one.b - one.a).toBeCloseTo(60)                       // a pan keeps the zoom all the way
    let v = from
    for (let i = 0; i < 60; i++) v = glide(v, to, 16, 800)     // about a second
    expect(v).toEqual(to)
    expect(glide(from, to, 0, 800)).toEqual(from)
  })
})
