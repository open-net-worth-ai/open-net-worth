// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { buildDrawingDocument, MSG_HEIGHT, MSG_THEME } from './drawingFrame'

describe('buildDrawingDocument', () => {
  it('puts the drawing in as written — scripts, styles and links included', () => {
    const code =
      '<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>' +
      '<style>.x{fill:red}</style><a href="https://example.test">src</a><svg></svg>'
    const doc = buildDrawingDocument(code, {}, 'Inter')
    expect(doc).toContain(code)
  })

  it('carries the theme variables, the iv- classes and the chat font', () => {
    const doc = buildDrawingDocument('<svg></svg>', { '--ink': '#111', '--good': '#0a0' }, 'Inter')
    expect(doc).toContain(':root{--ink:#111;--good:#0a0;}')
    expect(doc).toContain('.iv-good{fill:var(--good)')
    expect(doc).toContain('font-family:Inter')
  })

  it('reports its height and takes theme updates through the bridge script', () => {
    const doc = buildDrawingDocument('<svg></svg>', {}, '')
    expect(doc).toContain(MSG_HEIGHT)
    expect(doc).toContain(MSG_THEME)
    expect(doc).toContain('<base target="_blank">')
  })
})
