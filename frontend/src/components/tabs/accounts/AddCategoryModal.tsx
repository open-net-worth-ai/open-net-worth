/**
 * AddCategoryModal.tsx — Create a new category from within the transaction flow.
 *
 * The `kind` prop is the transaction direction that opened this (income = money in,
 * expense = money out); it pre-selects the Type, which the user can override to any of
 * the four category kinds. A category is a pure transaction LABEL (envelope ⊥ category,
 * RFC envelope-category-full-split.md) — it mints no budget envelope and belongs to no
 * budget group, so this modal collects only the label and kind.
 */

import { useState, useEffect } from 'react'
import type { ReactNode } from 'react'
import { Modal } from '../../ui/Modal'
import { Button } from '../../ui/Button'
import { FormField } from '../../ui/FormField'
import { TextField } from '../../ui/TextField'

import { KIND_LABEL, KIND_ORDER, type CatKind } from './categoryKinds'

export type { CatKind }

const KIND_PLACEHOLDER: Record<CatKind, string> = {
  expense: 'e.g. Pet care',
  income: 'e.g. Bonus',
  transfer: 'e.g. Reimbursement',
  investment: 'e.g. Dividends',
}
const KIND_NOTE: Record<CatKind, ReactNode> = {
  expense: (
    <>
      This is <b>money out (−)</b> — a label for spending.
    </>
  ),
  income: (
    <>
      This is <b>money in (+)</b> — a label for income.
    </>
  ),
  transfer: (
    <>
      This is a <b>transfer</b> label — money moved between your accounts.
    </>
  ),
  investment: (
    <>
      This is an <b>investment</b> label — buys, sells, dividends.
    </>
  ),
}

export interface AddCategoryModalProps {
  open: boolean
  prefill?: string
  /** The transaction direction that opened this dialog — pre-selects the Type. */
  kind?: CatKind
  onClose: () => void
  onSave: (cat: { name: string; kind: CatKind }) => void
}

export function AddCategoryModal({ open, prefill, kind, onClose, onSave }: AddCategoryModalProps) {
  const [name, setName] = useState('')
  const [selectedKind, setSelectedKind] = useState<CatKind>(kind ?? 'expense')

  useEffect(() => {
    if (open) {
      setName(prefill || '')
      setSelectedKind(kind ?? 'expense')
    }
  }, [open, prefill, kind])

  const valid = name.trim().length > 0

  function save() {
    if (!valid) return
    onSave({ name: name.trim(), kind: selectedKind })
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      size="sm"
      title="Add category"
      footer={
        <>
          <Button size="sm" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button size="sm" variant="primary" disabled={!valid} onClick={save}>
            Save
          </Button>
        </>
      }
    >
      <div className="acct-form">
        <div className="full">
          <div className="acct-refund-note">{KIND_NOTE[selectedKind]}</div>
        </div>
        <div className="full">
          <FormField label="Type" htmlFor="cat-kind">
            <div className="acct-edit-seg">
              {KIND_ORDER.map((k) => (
                <button
                  key={k}
                  type="button"
                  className={`acct-edit-seg-btn${selectedKind === k ? ` on ${k}` : ''}`}
                  onClick={() => setSelectedKind(k)}
                >
                  {KIND_LABEL[k]}
                </button>
              ))}
            </div>
          </FormField>
        </div>
        <div className="full">
          <FormField label="Category name" htmlFor="cat-name">
            <TextField
              id="cat-name"
              autoFocus
              value={name}
              placeholder={KIND_PLACEHOLDER[selectedKind]}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault()
                  save()
                }
              }}
            />
          </FormField>
        </div>
      </div>
    </Modal>
  )
}
