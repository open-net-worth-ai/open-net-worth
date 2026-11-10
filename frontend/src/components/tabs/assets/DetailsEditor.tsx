// Edit details — the category's guided details first, each labelled with its own prompt, then
// Insured (never for a policy — it IS the insurance), when a policy matures, and any other
// details as free label/value rows. Saves exactly {facts, insured, maturity_date} (RFC §3.4).
// A fact the category doesn't guide is kept under "Other details", never dropped. The page's
// "+ Custom detail" chip opens it with a new row already there, its label taking the cursor.
import { useId, useState } from 'react'
import type { OwnedAsset } from '../../../types/assets'
import { Button, DateField, FormField, Modal, Stack, Switch, Text, TextField } from '../../ui'
import * as api from './api'
import { showsInsured } from './assetCategoryRules'
import { joinFacts, splitFacts, type Fact, type GuidedFact } from './detailsFacts'
import { EditorFooter, SaveError, useEditorSave } from './editorKit'
import { Icons } from './icons'
import { X } from '../../../ui/icons'

const FIELDS = ['maturity_date'] as const

/** Rows change through updaters applied to the newest rows — never a copy from the last
 *  render, which an autofill filling several fields in one task would overwrite. */
type Update<T> = (update: (current: T) => T) => void

/** Saving drops a detail missing its label or its value — so half of one is refused, beside
 *  the half that's missing, instead of vanishing. A row left wholly empty is simply dropped. */
function halfFilled([label, value]: Fact): string | undefined {
  const hasLabel = label.trim() !== ''
  const hasValue = value.trim() !== ''
  if (hasLabel && !hasValue) return 'Add a value, or remove this detail.'
  if (!hasLabel && hasValue) return 'Add a label, or remove this detail.'
  return undefined
}

const rowField = (i: number) => `other-${i}`

interface OtherDetailRowProps {
  row: Fact
  /** What's said beside the row — about its missing half — if anything. */
  error: string | undefined
  /** A row just added: its label takes the cursor as it appears. */
  focus: boolean
  onSet(side: 0 | 1, text: string): void
  onRemove(): void
}

function OtherDetailRow({ row, error, focus, onSet, onRemove }: OtherDetailRowProps) {
  const [label, value] = row
  const errorId = `${useId()}-error`
  const missingLabel = Boolean(error) && label.trim() === ''
  const missingValue = Boolean(error) && !missingLabel
  return (
    <>
      <div>
        <TextField
          aria-label="Detail label"
          autoFocus={focus}
          value={label}
          maxLength={api.TEXT_CAPS.fact}
          placeholder="Label"
          invalid={missingLabel}
          aria-describedby={missingLabel ? errorId : undefined}
          onChange={(e) => onSet(0, e.target.value)}
        />
        <TextField
          aria-label="Detail value"
          value={value}
          maxLength={api.TEXT_CAPS.fact}
          placeholder="Value"
          invalid={missingValue}
          aria-describedby={missingValue ? errorId : undefined}
          onChange={(e) => onSet(1, e.target.value)}
        />
        <button
          type="button"
          className="as-fact-x"
          aria-label="Remove this detail"
          onClick={onRemove}
        >
          <X size={14} aria-hidden="true" />
        </button>
      </div>
      {error && (
        <p className="ui-field__error" id={errorId} role="alert">
          {error}
        </p>
      )}
    </>
  )
}

interface OtherDetailsProps {
  rows: Fact[]
  /** What's said beside each row, if anything. */
  errors: (string | undefined)[]
  /** The row last added, whose label takes the cursor when it appears; -1 for none. */
  focusRow: number
  onChange: Update<Fact[]>
  onAdd(): void
  /** A row was edited — or, with no row, one was removed and the rest moved up. */
  onEdited(row?: number): void
}

function OtherDetails({ rows, errors, focusRow, onChange, onAdd, onEdited }: OtherDetailsProps) {
  const set = (i: number, side: 0 | 1, text: string) => {
    onChange((all) =>
      all.map((row, at) => (at === i ? ((side ? [row[0], text] : [text, row[1]]) as Fact) : row)),
    )
    onEdited(i)
  }
  const remove = (i: number) => {
    onChange((all) => all.filter((_, at) => at !== i))
    onEdited()
  }
  return (
    <Stack gap="--space-3" className="as-details-other">
      <Text as="h4" size="sm" weight={600}>
        Other details
      </Text>
      {rows.length > 0 && (
        <div className="as-facts-ed">
          {rows.map((row, i) => (
            <OtherDetailRow
              key={i}
              row={row}
              error={errors[i]}
              focus={i === focusRow}
              onSet={(side, text) => set(i, side, text)}
              onRemove={() => remove(i)}
            />
          ))}
        </div>
      )}
      <div>
        <Button
          variant="secondary"
          size="sm"
          iconLeft={Icons.plus!({ width: 13, height: 13 })}
          onClick={onAdd}
        >
          Add a detail
        </Button>
      </div>
    </Stack>
  )
}

interface GuidedFieldsProps {
  guided: GuidedFact[]
  onGuided: Update<GuidedFact[]>
  policy: boolean
  insured: boolean
  onInsured(insured: boolean): void
  matures: string
  onMatures(date: string): void
  maturesError: string | undefined
}

/** The category's own prompts, then Insured — or, for a policy, when it matures. */
function GuidedFields(props: GuidedFieldsProps) {
  const { guided, onGuided, policy } = props
  const id = useId()
  return (
    <div className="as-form">
      {guided.map(({ prompt: [label, key, placeholder], value }, i) => (
        <FormField key={key} label={label} htmlFor={`${id}-${key}`}>
          <TextField
            id={`${id}-${key}`}
            value={value}
            maxLength={api.TEXT_CAPS.fact}
            placeholder={placeholder}
            onChange={(e) => {
              const typed = e.target.value
              onGuided((all) => all.map((g, at) => (at === i ? { ...g, value: typed } : g)))
            }}
          />
        </FormField>
      ))}
      {policy ? (
        <FormField label="Matures on" htmlFor={`${id}-matures`} error={props.maturesError}>
          <DateField
            id={`${id}-matures`}
            invalid={!!props.maturesError}
            value={props.matures}
            onChange={(e) => props.onMatures(e.target.value)}
          />
        </FormField>
      ) : (
        <div className="full">
          <Switch
            id={`${id}-insured`}
            label="Insured"
            checked={props.insured}
            onChange={(e) => props.onInsured(e.target.checked)}
          />
        </div>
      )}
    </div>
  )
}

export interface DetailsEditorProps {
  a: OwnedAsset
  /** Opened to add a detail of your own: a new, empty row is there already. Left empty, it is
   *  dropped on save like any empty row. */
  addDetail?: boolean
  onClose(): void
  onSaved(message: string): void
}

const EMPTY_ROW: Fact = ['', '']

export function DetailsEditor({ a, addDetail = false, onClose, onSaved }: DetailsEditorProps) {
  const [initial] = useState(() => splitFacts(a.category, a.facts))
  const [guided, setGuided] = useState(initial.guided)
  const [other, setOther] = useState(() =>
    addDetail ? [...initial.other, EMPTY_ROW] : initial.other,
  )
  const [focusRow, setFocusRow] = useState(addDetail ? initial.other.length : -1)
  const [insured, setInsured] = useState(a.insured)
  const [matures, setMatures] = useState(a.maturity_date ?? '')
  const policy = !showsInsured(a.category)
  const { saving, save, check, errorFor, edited, dialogError } = useEditorSave()

  async function onSave() {
    if (!check(Object.fromEntries(other.map((row, i) => [rowField(i), halfFilled(row)])))) return
    const changes = {
      facts: joinFacts(guided, other),
      insured,
      maturity_date: policy ? matures || null : a.maturity_date,
    }
    if (await save(() => api.updateAsset(a.id, changes))) onSaved('Details saved.')
  }

  return (
    <Modal
      className="as-modal"
      open
      onClose={onClose}
      title="Details"
      subtitle={a.name}
      size="lg"
      footer={
        <EditorFooter saving={saving} label="Save changes" onCancel={onClose} onSave={onSave} />
      }
    >
      <Stack gap="--space-7">
        <GuidedFields
          guided={guided}
          onGuided={setGuided}
          policy={policy}
          insured={insured}
          onInsured={setInsured}
          matures={matures}
          onMatures={(date) => {
            setMatures(date)
            edited('maturity_date')
          }}
          maturesError={errorFor('maturity_date')}
        />
        <OtherDetails
          rows={other}
          errors={other.map((_, i) => errorFor(rowField(i)))}
          focusRow={focusRow}
          onChange={setOther}
          onAdd={() => {
            setFocusRow(other.length)
            setOther((all) => [...all, EMPTY_ROW])
          }}
          onEdited={(row) =>
            edited(...(row === undefined ? other.map((_, i) => rowField(i)) : [rowField(row)]))
          }
        />
        <SaveError message={dialogError(FIELDS)} />
      </Stack>
    </Modal>
  )
}
