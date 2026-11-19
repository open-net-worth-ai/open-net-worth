import { useState } from 'react'
import { Button, FormField, Modal, NumberField, RadioGroup, Select, TextField } from '../../ui'
import { fCurrency } from '../../../utils/format'
import { createPaperPortfolio } from '../../../api/paperTrading'
import { usePaperDefaults } from './usePaper'

type StartWith = 'cash' | 'holdings' | 'followed'

export function NewPortfolioDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean
  onClose: () => void
  onCreated: (id: string) => void
}) {
  const defaults = usePaperDefaults(open).data
  const ccy = defaults?.base_currency ?? 'USD'
  const [name, setName] = useState('')
  const [startWith, setStartWith] = useState<StartWith>('cash')
  const [preset, setPreset] = useState('mine')
  const [other, setOther] = useState('')
  const [followedId, setFollowedId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const mine = defaults?.default_cash ?? 10000
  const cash = preset === 'mine' ? mine : preset === 'other' ? Number(other) : Number(preset)
  const needsCash = startWith !== 'holdings'
  const followed = defaults?.followed ?? []
  const ok = (!needsCash || cash > 0) && (startWith !== 'followed' || !!followedId) && !busy
  const whole = (n: number) => fCurrency(n, ccy, { maximumFractionDigits: 0 })

  async function create() {
    setBusy(true)
    setError(null)
    try {
      const out = await createPaperPortfolio({
        name: name.trim() || 'Paper portfolio',
        start_with: startWith,
        ...(needsCash ? { cash } : {}),
        ...(startWith === 'followed' ? { followed_id: followedId } : {}),
      })
      onCreated(out.portfolio.id)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="New paper portfolio"
      size="md"
      footer={
        <>
          <Button size="sm" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button
            size="sm"
            variant="primary"
            disabled={!ok}
            loading={busy}
            onClick={() => void create()}
          >
            Create
          </Button>
        </>
      }
    >
      <div className="paper-form">
        <FormField label="Name" htmlFor="pp-name">
          <TextField
            id="pp-name"
            placeholder="Paper portfolio"
            value={name}
            maxLength={80}
            onChange={(e) => setName(e.target.value)}
          />
        </FormField>
        <FormField label="Start with">
          <RadioGroup
            name="pp-start"
            value={startWith}
            onChange={(v) => setStartWith(v as StartWith)}
            options={[
              { value: 'cash', label: 'Cash' },
              {
                value: 'holdings',
                label: 'A copy of my holdings',
                description: "Today's holdings at today's prices",
              },
              {
                value: 'followed',
                label: 'A portfolio I follow',
                description: followed.length
                  ? 'Its current weights'
                  : 'Follow one in Copy Trading first',
                disabled: followed.length === 0,
              },
            ]}
          />
        </FormField>
        {startWith === 'followed' && (
          <FormField label="Portfolio" htmlFor="pp-followed">
            <Select
              id="pp-followed"
              placeholder="Choose a portfolio"
              value={followedId}
              onChange={(e) => setFollowedId(e.target.value)}
              options={followed.map((f) => ({ value: f.id, label: f.name }))}
            />
          </FormField>
        )}
        {needsCash && (
          <FormField label="Starting cash">
            <RadioGroup
              name="pp-cash"
              value={preset}
              onChange={setPreset}
              options={[
                { value: 'mine', label: `Same as your investments (${whole(mine)})` },
                { value: '10000', label: whole(10000) },
                { value: '100000', label: whole(100000) },
                { value: 'other', label: 'Another amount' },
              ]}
            />
          </FormField>
        )}
        {needsCash && preset === 'other' && (
          <FormField label={`Amount (${ccy})`} htmlFor="pp-amount">
            <NumberField
              id="pp-amount"
              min={1}
              value={other}
              onChange={(e) => setOther(e.target.value)}
            />
          </FormField>
        )}
        {error && <div className="paper-next bad">{error}</div>}
      </div>
    </Modal>
  )
}
