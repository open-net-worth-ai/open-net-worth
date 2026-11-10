// Keep awake for a server the owner runs on their own computer. GET/PUT /api/config/keep-awake —
// `supported` is false in a container, on Linux, and under the desktop app (its own switch).

import { setCachedQueryData } from '../state/queryCache'
import { authFetch } from './client'

export const KEEP_AWAKE_KEY = '/api/config/keep-awake'

export interface KeepAwake {
  supported: boolean
  enabled: boolean
  active: boolean
}

export async function getKeepAwake(): Promise<KeepAwake> {
  const r = await authFetch(KEEP_AWAKE_KEY)
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return r.json() as Promise<KeepAwake>
}

export async function setKeepAwake(enabled: boolean): Promise<KeepAwake> {
  const r = await authFetch(KEEP_AWAKE_KEY, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return setCachedQueryData(KEEP_AWAKE_KEY, (await r.json()) as KeepAwake)
}
