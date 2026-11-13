// The asset's photo on its page (owner 2026-09-28: "we are not utilizing the photo properly …
// too small … user can't tap on it because it will trigger edit mode"). A photo is shown big —
// beside the name on desktop, the page's cover on a phone — and tapping it OPENS it; changing
// or removing it lives in that viewer (and in ⋯). With no photo the SAME place holds the
// category's icon on its tint, and tapping it adds one ("the layout shouldn't change when there
// is no image").
import { useRef, useState, type ChangeEvent } from 'react'
import type { AssetCategory, OwnedAsset } from '../../../types/assets'
import { Button, ConfirmDialog, Modal } from '../../ui'
import * as api from './api'
import { catOf } from './assetsData'
import { SaveError, useEditorSave } from './editorKit'
import { Icons } from './icons'

/** A hidden image picker: `pick()` opens it, a chosen file goes to `onPhoto`. */
export function usePhotoPicker(onPhoto: (file: File) => void) {
  const ref = useRef<HTMLInputElement>(null)
  const input = (
    <input
      ref={ref}
      type="file"
      accept="image/*"
      hidden
      onChange={(e: ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0]
        // Cleared at once, so choosing the same file again still fires a change.
        e.target.value = ''
        if (file) onPhoto(file)
      }}
    />
  )
  return { input, pick: () => ref.current?.click() }
}

export interface HeaderPhotoProps {
  a: OwnedAsset
  cats: AssetCategory[]
  /** Phone: the photo is the page's cover, full width above the name. */
  cover?: boolean
  onOpen(): void
  /** No photo yet: tapping its place picks one. */
  onAdd(): void
}

/** The photo, big, opening on tap — or, with none, the same place holding the category. */
export function HeaderPhoto({ a, cats, cover, onOpen, onAdd }: HeaderPhotoProps) {
  const place = cover ? 'as-dphoto as-dcover' : 'as-dphoto'
  if (!a.photo_ref) {
    const c = catOf(cats, a.category)
    const Ic = Icons[a.category] ?? Icons.other!
    return (
      <button
        type="button"
        className={`${place} as-dphoto-empty`}
        style={{
          background: `color-mix(in srgb, ${c.color} 12%, var(--bg-2))`,
          borderColor: `color-mix(in srgb, ${c.color} 30%, var(--line))`,
          color: c.color,
        }}
        onClick={onAdd}
      >
        {Ic({ width: 30, height: 30 })}
        <span className="as-dphoto-add">Add a photo</span>
      </button>
    )
  }
  return (
    <button
      type="button"
      className={place}
      aria-label={`Open the photo of ${a.name}`}
      onClick={onOpen}
    >
      <img loading="lazy" src={api.photoUrl(a.photo_ref)} alt="" />
    </button>
  )
}

export interface PhotoViewerProps {
  a: OwnedAsset
  onClose(): void
  onChange(): void
  onRemoved(): void
}

/** The photo at full size, with its two edits: change it, or remove it (its file goes too). */
export function PhotoViewer({ a, onClose, onChange, onRemoved }: PhotoViewerProps) {
  const { saving, save, dialogError } = useEditorSave()
  const [confirming, setConfirming] = useState(false)
  async function remove() {
    if (await save(() => api.removePhoto(a.id))) onRemoved()
  }
  return (
    <>
      <Modal
        className="as-modal"
        open
        onClose={onClose}
        title={a.name}
        size="lg"
        footer={
          <>
            <Button
              variant="ghost"
              size="sm"
              className="as-footer-danger"
              onClick={() => setConfirming(true)}
            >
              Remove photo
            </Button>
            <Button variant="ghost" size="sm" onClick={onClose}>
              Close
            </Button>
            <Button variant="secondary" size="sm" onClick={onChange}>
              Change photo
            </Button>
          </>
        }
      >
        <div className="as-photo-view">
          {a.photo_ref && <img src={api.photoUrl(a.photo_ref)} alt={a.name} />}
        </div>
      </Modal>
      {confirming && (
        <ConfirmDialog
          data={{
            title: 'Remove this photo?',
            body: (
              <>
                Its file is deleted too, and that can't be undone.
                <SaveError message={dialogError([])} />
              </>
            ),
            confirmLabel: saving ? 'Removing…' : 'Remove',
            danger: true,
            confirmDisabled: saving,
          }}
          onConfirm={() => void remove()}
          onCancel={() => setConfirming(false)}
        />
      )}
    </>
  )
}
