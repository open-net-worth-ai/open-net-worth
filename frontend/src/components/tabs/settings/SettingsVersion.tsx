// The version of the server this app is talking to, at the foot of Settings. It asks the server
// rather than reading a build constant: a PWA or a desktop app connected to a server can be
// running a different build from it, and the server's version is the one that matters.
import { authFetch } from '../../../api/client'
import { useQueryCache } from '../../../state/queryCache'

const fetchServerVersion = async (): Promise<string | null> => {
  const response = await authFetch('/api/health')
  if (!response.ok) return null
  const body: { version?: unknown } = await response.json()
  return typeof body.version === 'string' && body.version ? body.version : null
}

export function SettingsVersion() {
  const { data: version } = useQueryCache('settings:server-version', fetchServerVersion)
  if (!version) return null
  return (
    <p className="rd-set-version">
      <span className="rd-set-versionlabel">Version </span>
      {version}
    </p>
  )
}
