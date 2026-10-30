// The backend's copy of the Browse & add directory — DERIVED from `catalog.ts`, never edited.
//
// Lucius answers "what can I connect?" on the backend, which cannot import the frontend, so
// this projects `CATALOG` + `MCP_INSTALL_PRESETS` into `backend/services/connector_directory.json`.
// `npm run sync:connector-directory` writes it; `directorySnapshot.test.ts` fails when the
// checked-in file differs from what this would produce. ADR
// `2026-10-31-lucius-discovers-connectors-from-a-synced-snapshot-and-setup-stays-human`.
//
// NON-SECRET FIELDS ONLY. A preset carries a vendor's public endpoint and the NAMES of the
// variables it reads, never a value; `env` is reduced to those names here, and an endpoint that
// could carry a credential (userinfo, a query string) refuses the build rather than ship.
// `endpoint` exists only so the backend can tell which entries are already installed, matching
// by endpoint exactly as `catalogProvenanceFor` does; the tool never returns it.

import { CATALOG, MCP_INSTALL_PRESETS } from './catalog'
import type { CatalogEntry, McpInstallPreset } from './catalog'

/** How the user finishes setting one up — what Lucius tells them to have ready. */
export type SnapshotConnect =
  | { kind: 'in_app_form' }
  | { kind: 'sign_in' }
  | { kind: 'api_key' }
  | { kind: 'none' }
  | { kind: 'local_command'; runtime: string; env_names: string[] }
  | { kind: 'manual' }

export interface SnapshotEntry {
  id: string
  name: string
  families: string[]
  categories: string[]
  publisher: string | null
  verification: string
  sub?: string
  popular: boolean
  mcp: boolean
  connect: SnapshotConnect
  endpoint?: string
}

export interface DirectorySnapshot {
  entries: SnapshotEntry[]
}

/** What a local server's command needs installed. An unlisted command fails the build, so a new
 *  runtime is a deliberate line here rather than a blank in what Lucius tells the user. */
const RUNTIMES: Record<string, string> = {
  npx: 'Node.js (npx)',
  uvx: 'uv (uvx)',
  'kraken-mcp': 'the Kraken CLI',
}

function endpointOf(preset: McpInstallPreset): string {
  if (preset.transport === 'remote') return (preset.url ?? '').trim()
  return [preset.command, preset.args].filter(Boolean).join(' ').trim()
}

const CREDENTIAL_FLAG = /^--?(api[-_]?key|token|secret|password|passphrase)(=|$)/i

function assertNoCredential(id: string, endpoint: string): void {
  for (const part of endpoint.split(/\s+/)) {
    if (CREDENTIAL_FLAG.test(part)) throw new Error(`${id}: preset passes a credential flag`)
    if (!part.includes('://')) continue
    let url: URL
    try {
      url = new URL(part.replace(/^[a-z+]+\+(?=[a-z]+:\/\/)/i, ''))
    } catch {
      continue
    }
    if (url.username || url.password || url.search || url.hash) {
      throw new Error(`${id}: preset endpoint must not carry userinfo or a query string`)
    }
  }
}

function connectOf(entry: CatalogEntry, preset: McpInstallPreset | undefined): SnapshotConnect {
  if (!entry.mcp) return { kind: 'in_app_form' }
  if (!preset) return { kind: 'manual' }
  if (preset.transport === 'remote') {
    return {
      kind: preset.auth === 'oauth' ? 'sign_in' : preset.auth === 'key' ? 'api_key' : 'none',
    }
  }
  const runtime = RUNTIMES[preset.command ?? '']
  if (!runtime) throw new Error(`${entry.id}: no runtime named for command "${preset.command}"`)
  const env_names = (preset.env ?? '')
    .split('\n')
    .filter((line) => line.trim())
    .map((line) => {
      const name = /^([A-Z_][A-Z0-9_]*)=/.exec(line.trim())?.[1]
      if (!name) throw new Error(`${entry.id}: env line is not NAME=value`)
      return name
    })
  return { kind: 'local_command', runtime, env_names }
}

export function buildDirectorySnapshot(
  catalog: CatalogEntry[] = CATALOG,
  presets: Record<string, McpInstallPreset> = MCP_INSTALL_PRESETS,
): DirectorySnapshot {
  const entries: SnapshotEntry[] = []
  for (const entry of catalog) {
    if (entry.generic) continue
    const preset = entry.mcp ? presets[entry.id] : undefined
    const endpoint = preset ? endpointOf(preset) : ''
    if (endpoint) assertNoCredential(entry.id, endpoint)
    entries.push({
      id: entry.id,
      name: entry.name,
      families: [...entry.families],
      categories: [...entry.categories],
      publisher: entry.publisher,
      verification: entry.verification,
      ...(entry.sub ? { sub: entry.sub } : {}),
      popular: entry.popular,
      mcp: entry.mcp,
      connect: connectOf(entry, preset),
      ...(endpoint ? { endpoint } : {}),
    })
  }
  return { entries }
}

export function serializeDirectorySnapshot(snapshot: DirectorySnapshot): string {
  return JSON.stringify(snapshot, null, 2) + '\n'
}
