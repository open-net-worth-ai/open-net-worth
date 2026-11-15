import { useState } from 'react'
import { Button, FormField, Modal, NumberField, RadioGroup, TextField } from '../../ui'
import { createPortfolio } from './paperStore'

const PRESETS = [
  { value: '10000', label: '10,000' },
  { value: '100000', label: '100,000' },
  { value: 'mine', label: 'Same as your investments (240,000)' },
  { value: 'other', label: 'Another amount' },
]

export function NewPortfolioDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean
  onClose: () => void
  onCreated: (id: string) => void
}) {
  const [name, setName] = useState('Paper portfolio')
  const [preset, setPreset] = useState('mine')
  const [other, setOther] = useState('')
  const cash = preset === 'mine' ? 240000 : preset === 'other' ? Number(other) : Number(preset)
  const ok = name.trim() !== '' && cash > 0
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="New paper portfolio"
      size="md"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="paper"
            disabled={!ok}
            onClick={() => onCreated(createPortfolio(name.trim(), cash))}
          >
            Create
          </Button>
        </>
      }
    >
      <div className="paper-form">
        <FormField label="Name" htmlFor="pp-name">
          <TextField id="pp-name" value={name} onChange={(e) => setName(e.target.value)} />
        </FormField>
        <FormField label="Paper cash">
          <RadioGroup name="pp-cash" options={PRESETS} value={preset} onChange={setPreset} />
        </FormField>
        {preset === 'other' && (
          <FormField label="Amount" htmlFor="pp-amount">
            <NumberField
              id="pp-amount"
              min={1}
              value={other}
              onChange={(e) => setOther(e.target.value)}
            />
          </FormField>
        )}
      </div>
    </Modal>
  )
}
