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

  it('carries the theme, the mode, the guide classes and the chat font', () => {
    const vars = { '--text-primary': '#111', '--border': '#ddd' }
    const doc = buildDrawingDocument('<svg></svg>', vars, 'Inter', 'light')
    expect(doc).toContain(':root{--text-primary:#111;--border:#ddd;}')
    expect(doc).toContain('<html data-mode="light">')
    expect(doc).toContain('[data-mode=light]{--series-1:#2a78d6')
    expect(doc).toContain('.s1{fill:var(--series-1)')
    expect(doc).toContain('.th{fill:var(--text-primary)')
    expect(doc).toContain('[data-mode=light] .c-teal')
    expect(doc).toContain('.iv-good{')
    expect(doc).toContain('font-family:Inter')
    expect(doc).not.toMatch(/font-weight:\s*[67]00/)
  })

  it('reports its height and takes theme updates through the bridge script', () => {
    const doc = buildDrawingDocument('<svg></svg>', {}, '')
    expect(doc).toContain(MSG_HEIGHT)
    expect(doc).toContain(MSG_THEME)
    expect(doc).toContain('<base target="_blank">')
  })
})
