// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { watchInputModality } from './inputModality'

describe('watchInputModality', () => {
  it('tracks the last input modality on the root', () => {
    const root = document.createElement('div')
    watchInputModality(root)
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab' }))
    expect(root.dataset.input).toBe('keyboard')
    window.dispatchEvent(new Event('pointerdown'))
    expect(root.dataset.input).toBe('pointer')
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'c', metaKey: true }))
    expect(root.dataset.input).toBe('pointer')
  })
})
