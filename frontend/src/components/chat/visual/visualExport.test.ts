// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { fileName, toBlob } from './visualExport'

describe('visual export helpers', () => {
  it('names the file after the title', () => {
    expect(fileName('Spending by category, November so far')).toBe(
      'spending-by-category-november-so-far',
    )
    expect(fileName(null)).toBe('chart')
    expect(fileName('!!!')).toBe('chart')
  })

  it('decodes data URLs in code — the app CSP blocks fetching them', async () => {
    const read = (b: Blob) =>
      new Promise<ArrayBuffer>((resolve) => {
        const r = new FileReader()
        r.onload = () => resolve(r.result as ArrayBuffer)
        r.readAsArrayBuffer(b)
      })
    const png = toBlob('data:image/png;base64,AAEC')
    expect(png.type).toBe('image/png')
    expect(Array.from(new Uint8Array(await read(png)))).toEqual([0, 1, 2])
    const svg = toBlob('data:image/svg+xml;charset=utf-8,%3Csvg%2F%3E')
    expect(svg.type).toBe('image/svg+xml')
    expect(new TextDecoder().decode(await read(svg))).toBe('<svg/>')
  })
})
