// @vitest-environment jsdom
//
// Owner, 2026-10-31: dragging a file onto the Lucius side panel should be caught anywhere in the
// panel, not only over the composer at its foot. The panel owns the drop zone (ChatPanel wires
// `useFileDropZone` to its <aside>) and hands files to the composer through `addFiles`; the
// composer's own zone is off there so the two never show two masks.

import { cleanup, fireEvent, render } from '@testing-library/react'
import { createRef } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { LuciusComposer } from './Composer'
import type { LuciusComposerHandle } from './Composer'
import { FileDropMask } from './FileDropMask'
import { useFileDropZone } from './useFileDropZone'

vi.mock('../../../state/chatComposer', () => ({
  useChatComposer: () => ({
    pendingMentions: [],
    respondingAgent: null,
    pendingSkills: [],
    setPendingSkills: vi.fn(),
    clearPendingMentions: vi.fn(),
  }),
}))
vi.mock('../../../state/screenSnapshot', () => ({
  useScreenSnapshot: () => ({ enabled: false, pinnedHandleIds: [] }),
}))
vi.mock('../../../lucius/screenContract', () => ({
  getScreenSnapshot: () => ({ title: 'Test', facts: [], raw: '', handles: [] }),
  captureAndSerialize: () => null,
  allHandles: () => [],
}))
vi.mock('../../../lucius/identity', () => ({
  GLYPH_SVG: {},
  TYPE_MARKER_SVG: {},
  hSvg: () => '',
  Spark: () => null,
  Identity: () => null,
  IdentityInner: () => null,
}))
vi.mock('../../../hooks/useMentionables', () => ({
  useMentionables: () => ({ items: [], loading: false }),
}))

vi.mock('../../../api/client', () => ({ authFetch: () => new Promise<Response>(() => {}) }))
URL.createObjectURL = () => 'blob:test'
URL.revokeObjectURL = () => {}

function Panel({ composerRef }: { composerRef: React.RefObject<LuciusComposerHandle | null> }) {
  const drop = useFileDropZone((files) => composerRef.current?.addFiles(files))
  return (
    <aside className="lx-side" {...drop.handlers}>
      {drop.over && <FileDropMask className="lx-dropmask--side" />}
      <div className="lx-side-scroll">
        <p className="transcript">An earlier reply</p>
      </div>
      <LuciusComposer
        ref={composerRef}
        onSend={() => {}}
        visibleIds={new Set<string>()}
        screenAwarenessDisabled
        fileDrop={false}
      />
    </aside>
  )
}

function mount() {
  const ref = createRef<LuciusComposerHandle>()
  render(<Panel composerRef={ref} />)
  return ref
}

const csv = () => new File(['a,b\n1,2\n'], 'rent.csv', { type: 'text/csv' })
const fileTransfer = (files: File[] = []) => ({ types: ['Files'], files })
const masks = () => document.querySelectorAll('.lx-dropmask')
const chips = () => document.querySelectorAll('.lx-att')

afterEach(cleanup)

describe('the side panel is the file drop target', () => {
  it('shows one panel-wide mask when a file is dragged over the transcript', () => {
    mount()
    fireEvent.dragOver(document.querySelector('.transcript')!, { dataTransfer: fileTransfer() })
    expect(masks()).toHaveLength(1)
    expect(masks()[0]!.classList.contains('lx-dropmask--side')).toBe(true)
  })

  it('shows the same single mask when the drag is over the composer', () => {
    mount()
    fireEvent.dragOver(document.querySelector('.lx-compose')!, { dataTransfer: fileTransfer() })
    expect(masks()).toHaveLength(1)
    expect(document.querySelector('.lx-compose.dragfile')).toBeNull()
  })

  it('attaches a file dropped on the transcript, far from the composer', () => {
    mount()
    const target = document.querySelector('.transcript')!
    fireEvent.dragOver(target, { dataTransfer: fileTransfer() })
    fireEvent.drop(target, { dataTransfer: fileTransfer([csv()]) })
    expect(chips()).toHaveLength(1)
    expect(masks()).toHaveLength(0)
  })

  it('keeps the mask while the drag moves between children of the panel', () => {
    mount()
    const side = document.querySelector('.lx-side')!
    const transcript = document.querySelector('.transcript')!
    const files = { dataTransfer: fileTransfer() }
    fireEvent.dragEnter(side, files)
    // Into a child: the browser fires dragenter on the child, then dragleave on the panel.
    fireEvent.dragEnter(transcript, files)
    fireEvent.dragLeave(side, files)
    expect(masks()).toHaveLength(1)
    fireEvent.dragLeave(transcript, files)
    expect(masks()).toHaveLength(0)
  })

  it('ignores a drag that carries no files', () => {
    mount()
    fireEvent.dragOver(document.querySelector('.transcript')!, {
      dataTransfer: { types: ['text/plain'], files: [] },
    })
    expect(masks()).toHaveLength(0)
  })
})
