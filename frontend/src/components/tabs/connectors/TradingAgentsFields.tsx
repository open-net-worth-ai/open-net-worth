import { TA_CLI_LABEL, TA_CLI_PROVIDERS } from '../../../types/tradingAgentsConstants'
import type { useModels } from '../../../hooks/useModels'
import {
  Button,
  Cluster,
  FormField,
  ModelPicker,
  NumberField,
  PasswordField,
  Stack,
  Text,
} from '../../ui'
import type { useSecretReveal } from '../../ui'
import { modelRefOf } from '../../ui/ModelPicker'

// The setup form's three reusable fields: a model picker that warns about a vanished provider
// or a subscription CLI, a ranged number, and a saved-key field with its eye and clear.

export function ModelField({
  id,
  label,
  help,
  defaultLabel,
  value,
  onChange,
  models,
  defaultModel,
  loaded,
}: {
  id: string
  label: string
  help: string
  defaultLabel: string
  value: string
  onChange: (v: string) => void
  models: ReturnType<typeof useModels>['models']
  defaultModel: string
  loaded: boolean
}) {
  const provider = value.includes(':') ? value.slice(0, value.indexOf(':')) : ''
  const gone = loaded && !!value && !models.some((m) => modelRefOf(m) === value)
  const cli = TA_CLI_PROVIDERS.has(provider) ? (TA_CLI_LABEL[provider] ?? provider) : null
  return (
    <FormField label={label} htmlFor={id} help={help}>
      <Stack gap="--space-3">
        <ModelPicker
          id={id}
          size="field"
          label={label}
          models={models}
          value={value}
          defaultLabel={defaultLabel}
          defaultModelId={defaultModel}
          onChange={onChange}
        />
        {gone && (
          <Text size="xs" tone="warn">
            {value}: this provider is turned off. Runs will refuse until you pick another.
          </Text>
        )}
        {cli && (
          <Text size="xs" tone="faint">
            Uses your {cli} subscription: calls count against your plan, not dollars, and run
            slower.
          </Text>
        )}
      </Stack>
    </FormField>
  )
}

export function NumField({
  id,
  label,
  help,
  value,
  error,
  step,
  onChange,
}: {
  id: string
  label: string
  help?: string
  value: string
  error?: string
  step?: number
  onChange: (v: string) => void
}) {
  return (
    <FormField label={label} htmlFor={id} help={help} error={error}>
      <NumberField
        id={id}
        value={value}
        step={step ?? 1}
        invalid={!!error}
        onChange={(e) => onChange(e.target.value)}
      />
    </FormField>
  )
}

export function KeyField({
  id,
  label,
  saved,
  value,
  clear,
  reveal,
  saving,
  onChange,
  onClear,
}: {
  id: string
  label: string
  saved: boolean
  value: string
  clear: boolean
  reveal: ReturnType<typeof useSecretReveal>
  saving: boolean
  onChange: (v: string) => void
  onClear: () => void
}) {
  const help =
    reveal.caption ||
    (clear
      ? 'The saved key will be removed when you save.'
      : saved
        ? 'A key is saved. Leave blank to keep it.'
        : 'Optional. TradingAgents works without it.')
  return (
    <Stack gap="--space-3">
      <FormField label={label} htmlFor={id} help={help} error={reveal.error}>
        <PasswordField
          id={id}
          value={value}
          placeholder={saved ? '••••••••' : ''}
          onChange={(e) => onChange(e.target.value)}
          {...reveal.fieldProps}
        />
      </FormField>
      {saved && (
        <Cluster>
          <Button size="sm" variant="ghost" disabled={saving} onClick={onClear}>
            {clear ? 'Undo clear' : 'Clear saved key'}
          </Button>
        </Cluster>
      )}
    </Stack>
  )
}
