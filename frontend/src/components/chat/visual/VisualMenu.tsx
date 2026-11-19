import { useEffect, useRef, useState } from 'react'
import { ActionMenu } from '../../ui/ActionMenu'
import { Copy, Download } from '../../../ui/icons'
import {
  copyImage,
  downloadImage,
  exportBlocks,
  exportDrawing,
  fileName,
  type ImageFormat,
} from './visualExport'

/** The ⋯ menu on a visual: copy it as an image, or download it as SVG or PNG. A drawing on
 *  its own is exported by its frame; anything with standard blocks is captured from the page. */
export function VisualMenu({
  title,
  drawingOnly,
}: {
  title?: string | null
  drawingOnly: boolean
}) {
  const box = useRef<HTMLDivElement>(null)
  const [note, setNote] = useState<string | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => () => void (timer.current && clearTimeout(timer.current)), [])

  const flash = (text: string) => {
    setNote(text)
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => setNote(null), 2000)
  }

  const make = (format: ImageFormat): Promise<string> => {
    const visual = box.current?.closest('section.ivis') as HTMLElement | null
    if (!visual) return Promise.reject(new Error('no visual'))
    const frame = visual.querySelector<HTMLIFrameElement>('iframe.ivis-frame')
    return drawingOnly && frame ? exportDrawing(frame, format) : exportBlocks(visual, format)
  }

  const download = (format: ImageFormat) =>
    make(format).then(
      (url) => {
        downloadImage(url, fileName(title), format)
        flash('Downloaded')
      },
      () => flash("Couldn't make the image"),
    )

  const items = [
    {
      label: 'Copy as image',
      icon: <Copy size={14} aria-hidden="true" />,
      onSelect: () =>
        void copyImage(() => make('png')).then(
          () => flash('Copied'),
          () => flash("Couldn't copy"),
        ),
    },
    {
      label: 'Download as SVG',
      icon: <Download size={14} aria-hidden="true" />,
      onSelect: () => void download('svg'),
    },
    {
      label: 'Download as PNG',
      icon: <Download size={14} aria-hidden="true" />,
      onSelect: () => void download('png'),
    },
  ]

  return (
    <div ref={box} className="ivis-menu">
      {note && (
        <span className="ivis-menu-note" role="status">
          {note}
        </span>
      )}
      <ActionMenu items={items} label="Visual actions" title={title || 'Visual'} />
    </div>
  )
}
