import { Button, FormField, TextArea } from '../../ui'

// A certificate the person trusts for their own Actual server: self-signed, or from a private
// authority. Pasted as text; a saved one shows by its fingerprint. Trusting it adds to the normal
// checks for this server only, and a new address drops it, as it does the sign-in.

const BEGIN = '-----BEGIN CERTIFICATE-----'

export function certLooksRight(text: string) {
  const t = text.trim()
  return !t || (t.startsWith(BEGIN) && t.includes('-----END CERTIFICATE-----'))
}

export function ActualCertField(props: {
  value: string
  onChange: (value: string) => void
  fingerprints: string[]
  moved: boolean
  clearing: boolean
  onClear: (clear: boolean) => void
}) {
  const { value, fingerprints, moved, clearing } = props
  const saved = fingerprints.length > 0 && !moved && !clearing
  const valid = certLooksRight(value)
  return (
    <FormField
      label="Server certificate"
      htmlFor="conn-actual-cert"
      help={
        moved && fingerprints.length > 0 ? (
          'A new address needs its certificate again: the saved one is for the old server.'
        ) : clearing ? (
          <>
            Removed when you save.{' '}
            <Button size="sm" variant="ghost" onClick={() => props.onClear(false)}>
              Keep it
            </Button>
          </>
        ) : saved ? (
          <>
            Trusted: SHA-256 {fingerprints.map((f) => f.slice(0, 23)).join(', ')}…. Paste another to
            replace it.{' '}
            <Button size="sm" variant="ghost" onClick={() => props.onClear(true)}>
              Remove
            </Button>
          </>
        ) : (
          'Only if your server uses its own certificate (self-signed, or from a private authority) that this computer doesn’t trust. Paste it as text. The address is still checked, and nothing else changes.'
        )
      }
      error={valid ? undefined : `Paste the certificate from ${BEGIN} to the END line.`}
    >
      <TextArea
        id="conn-actual-cert"
        mono
        rows={2}
        value={value}
        placeholder={saved ? '••••••••' : BEGIN}
        autoComplete="off"
        spellCheck={false}
        autoCapitalize="none"
        invalid={!valid}
        onChange={(e) => {
          props.onChange(e.target.value)
          if (e.target.value.trim()) props.onClear(false)
        }}
      />
    </FormField>
  )
}
