import { useCallback, useEffect, useState } from 'react'
import { binanceApi, type BinanceSettings, type BinanceStatus } from '../../../api/binance'
import { Badge, Button, Checkbox, Cluster, ErrorState, FormField, Loading, PasswordField, Select, Stack, Text, TextField, useSecretReveal } from '../../ui'
import { unrevealed, useConnectorSave, useTouched } from './formSave'
import { revealConnectorSecret } from './secrets'

interface BinanceForm extends Omit<BinanceSettings, 'enabled'> {
  api_key: string
  api_secret: string
}
const formOf = (status: BinanceStatus): BinanceForm => ({
  environment: status.environment,
  quote_currency: status.quote_currency,
  account_scope: status.account_scope,
  isolated_account: status.isolated_account,
  api_key: '',
  api_secret: '',
})

export function BinanceSection() {
  const [cfg, setCfg] = useState<BinanceStatus | null>(null)
  const [form, setForm] = useState<BinanceForm | null>(null)
  const [loadError, setLoadError] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const load = useCallback(async () => {
    setLoadError('')
    try {
      const status = await binanceApi.status()
      setCfg(status)
      setForm(formOf(status))
    } catch {
      setLoadError('Could not load the Binance Spot connector status.')
    }
  }, [])
  useEffect(() => { void load() }, [load])
  const changeKey = useCallback((value: string) => setForm((f) => f && { ...f, api_key: value }), [])
  const changeSecret = useCallback((value: string) => setForm((f) => f && { ...f, api_secret: value }), [])
  const key = useSecretReveal({ saved: !!cfg?.has_api_key, value: form?.api_key ?? '', onChange: changeKey, reveal: useCallback(() => revealConnectorSecret('binance', 'api_key'), []) })
  const secret = useSecretReveal({ saved: !!cfg?.has_api_secret, value: form?.api_secret ?? '', onChange: changeSecret, reveal: useCallback(() => revealConnectorSecret('binance', 'api_secret'), []) })
  const keyTouched = useTouched()
  const secretTouched = useTouched()
  const values = form && { ...form, api_key: unrevealed(form.api_key, key), api_secret: unrevealed(form.api_secret, secret) }
  const dirty = !!cfg && JSON.stringify(values) !== JSON.stringify(formOf(cfg))
  const hasKey = !!values?.api_key.trim()
  const hasSecret = !!values?.api_secret.trim()
  const keysValid = hasKey === hasSecret && (hasKey || !!(cfg?.has_api_key && cfg?.has_api_secret))
  const environmentChanged = !!cfg && form?.environment !== cfg.environment
  async function save() {
    if (!form || !cfg || !keysValid) return false
    setBusy(true)
    setError('')
    try {
      const { api_key, api_secret, ...settings } = form
      const status = await binanceApi.configure({
        ...settings,
        enabled: cfg.configured ? cfg.enabled : true,
        ...(hasKey ? { api_key: api_key.trim(), api_secret: api_secret.trim() } : {}),
      })
      setCfg(status)
      setForm(formOf(status))
      return true
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save Binance settings.')
      const status = await binanceApi.status().catch(() => null)
      if (status) setCfg(status)
      return false
    } finally {
      setBusy(false)
    }
  }
  useConnectorSave({ ready: !!form && keysValid && !!form.isolated_account && (!environmentChanged || hasKey), values, baseline: cfg ? formOf(cfg) : null, saving: busy, error, save })
  async function check() {
    setBusy(true)
    setError('')
    try {
      setCfg(await binanceApi.test())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not check Binance account.')
      const status = await binanceApi.status().catch(() => null)
      if (status) setCfg(status)
    } finally {
      setBusy(false)
    }
  }
  if (loadError) return <ErrorState message={loadError} onRetry={() => void load()} />
  if (!cfg || !form) return <Loading label="Loading Binance Spot settings…" />
  const set = <K extends keyof BinanceForm>(name: K, value: BinanceForm[K]) => setForm((f) => f && { ...f, [name]: value })
  const pairError = (filled: boolean, other: boolean, touched: boolean, saved: boolean) => filled ? undefined : other ? 'Enter both keys together.' : touched && !saved ? 'This key is required.' : undefined
  return <Stack gap="--space-4">
    <Text size="sm" tone="muted">Connect a dedicated Binance Spot wallet. Reserve the entire funded account or subaccount exclusively for managed trading.</Text>
    {cfg.configured && !cfg.enabled && <Text size="sm" tone="warn">Currently paused. Use Resume on the connector card after saving to enable it.</Text>}
    <FormField label="Environment" htmlFor="conn-binance-environment" help={environmentChanged ? 'Changing environment requires a new key pair for that environment.' : undefined}>
      <Select id="conn-binance-environment" value={form.environment} onChange={(e) => set('environment', e.target.value as BinanceStatus['environment'])} options={[
        { value: 'testnet', label: 'Binance Spot Testnet — virtual funds' },
        { value: 'live', label: 'Binance Spot — real funds' },
      ]} />
    </FormField>
    <FormField label="API key" htmlFor="conn-binance-key" required help={key.caption || 'Use a key for the selected environment. Leave both fields blank to keep the saved pair.'} error={key.error || pairError(hasKey, hasSecret, keyTouched.touched, cfg.has_api_key)}>
      <PasswordField id="conn-binance-key" value={form.api_key} onChange={(e) => set('api_key', e.target.value)} onBlur={keyTouched.onBlur} placeholder={cfg.has_api_key ? '••••••••' : ''} {...key.fieldProps} />
    </FormField>
    <FormField label="API secret" htmlFor="conn-binance-secret" required help={secret.caption} error={secret.error || pairError(hasSecret, hasKey, secretTouched.touched, cfg.has_api_secret)}>
      <PasswordField id="conn-binance-secret" value={form.api_secret} onChange={(e) => set('api_secret', e.target.value)} onBlur={secretTouched.onBlur} placeholder={cfg.has_api_secret ? '••••••••' : ''} {...secret.fieldProps} />
    </FormField>
    <FormField label="Quote currency" htmlFor="conn-binance-quote">
      <TextField id="conn-binance-quote" value={form.quote_currency} pattern="[A-Z0-9]{2,8}" required onChange={(e) => set('quote_currency', e.target.value.toUpperCase())} />
    </FormField>
    <FormField label="Account scope" htmlFor="conn-binance-scope">
      <Select id="conn-binance-scope" value={form.account_scope} onChange={(e) => set('account_scope', e.target.value as BinanceStatus['account_scope'])} options={[{ value: 'dedicated_account', label: 'Dedicated account' }, { value: 'subaccount', label: 'Subaccount' }]} />
    </FormField>
    <Checkbox checked={form.isolated_account} onChange={(e) => set('isolated_account', e.target.checked)} label="This entire Spot wallet is reserved exclusively for managed trading." />
    <Cluster wrap>
      <Badge tone={cfg.verification === 'private_account_checked' && !dirty ? 'pos' : 'muted'}>{cfg.verification === 'private_account_checked' && !dirty ? 'Account checked' : 'Account not checked'}</Badge>
      <Button variant="secondary" disabled={dirty || !cfg.configured || busy} onClick={() => void check()}>{busy ? 'Checking…' : 'Check account'}</Button>
    </Cluster>
    {dirty && <Text size="sm" tone="muted">Save your settings before checking the account.</Text>}
    {cfg.broker_account_id && <Text size="sm">Account UID: {cfg.broker_account_id} · {cfg.venue} · {cfg.quote_currency}</Text>}
    {cfg.verified_at && !dirty && cfg.verification === 'private_account_checked' && <Stack gap="--space-2">
      <Text size="sm" tone="muted">Checked {new Date(cfg.verified_at).toLocaleString()}</Text>
      <Text size="sm">Available cash: {cfg.available_cash ?? '—'} {cfg.quote_currency} · Total cash: {cfg.total_cash ?? '—'} {cfg.quote_currency}</Text>
      <Text size="sm">Non-quote balances: {cfg.non_quote_balances ?? '—'} · Open orders: {cfg.open_orders ?? '—'}</Text>
    </Stack>}
    <Text size="sm" tone="muted">Starting a run checks its account, balance, permissions and execution requirements again. Account checks do not submit orders.</Text>
    {error && <ErrorState message={error} />}
  </Stack>
}
