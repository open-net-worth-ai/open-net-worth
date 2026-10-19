import { useState } from 'react'
import { Info, Sun } from 'lucide-react'
import { KEEP_AWAKE_KEY, getKeepAwake, setKeepAwake } from '../../../api/keepAwake'
import {
  DESKTOP_SETTINGS_KEY,
  desktopBridge,
  fetchDesktopSettings,
} from '../../../platform/desktop'
import { setCachedQueryData, useQueryCache } from '../../../state/queryCache'
import { Switch } from '../../ui'

/** Automations stop while their computer sleeps. Inside the desktop app with a local book the
 *  app holds the switch; anywhere else the server does, where its machine supports it. */
export function KeepAwakeBanner() {
  return desktopBridge() ? <DesktopKeepAwake /> : <ServerKeepAwake />
}

function DesktopKeepAwake() {
  const bridge = desktopBridge()!
  const settings = useQueryCache(DESKTOP_SETTINGS_KEY, fetchDesktopSettings)
  if (!settings.data) return null
  return (
    <KeepAwakeRow
      text="Automations only run while this computer is awake."
      on={settings.data.keepAwake}
      save={async (on) =>
        setCachedQueryData(DESKTOP_SETTINGS_KEY, await bridge.updateSettings({ keepAwake: on }))
      }
    />
  )
}

function ServerKeepAwake() {
  const state = useQueryCache(KEEP_AWAKE_KEY, getKeepAwake)
  if (!state.data?.supported) return null
  return (
    <KeepAwakeRow
      text="Automations only run while the server is awake."
      on={state.data.enabled}
      save={setKeepAwake}
    />
  )
}

function KeepAwakeRow({
  text,
  on,
  save,
}: {
  text: string
  on: boolean
  save: (on: boolean) => Promise<unknown>
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(false)

  async function change(next: boolean) {
    setBusy(true)
    setError(false)
    try {
      await save(next)
    } catch {
      setError(true)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="a3-awake" data-od-id="automations-keep-awake">
      <Info className="a3-awake-ic" size={15} strokeWidth={1.7} aria-hidden="true" />
      <span className="a3-awake-text">
        {error ? 'Could not change Keep awake. Try again.' : text}
      </span>
      <span className="a3-awake-label" aria-hidden="true">
        <Sun className="a3-awake-ic" size={15} strokeWidth={1.7} />
        Keep awake
      </span>
      <Switch
        className="a3-awake-switch"
        aria-label="Keep computer awake"
        checked={on}
        disabled={busy}
        onChange={(event) => void change(event.target.checked)}
      />
    </div>
  )
}
