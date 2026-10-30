// Flags a recorded directory tool list (src/components/tabs/connectors/recordedTools.ts) whose
// package has released past the version it was read from, so a maintainer knows to re-record
// it. Reads registry metadata only — never downloads or runs a server.
// Run: npm run check:connector-tools
import { RECORDED_TOOLS } from '../src/components/tabs/connectors/recordedTools'

async function latest(registry: 'npm' | 'pypi', name: string): Promise<string | null> {
  const url =
    registry === 'npm'
      ? `https://registry.npmjs.org/${name.replace('/', '%2F')}/latest`
      : `https://pypi.org/pypi/${name}/json`
  const res = await fetch(url)
  if (!res.ok) return null
  const body = (await res.json()) as { version?: string; info?: { version?: string } }
  return body.version ?? body.info?.version ?? null
}

let stale = 0
for (const [id, rec] of Object.entries(RECORDED_TOOLS)) {
  if (!rec.pkg || !rec.version) {
    console.log(`·  ${id}: recorded ${rec.asOf}, no package to check — re-read ${rec.source}`)
    continue
  }
  const now = await latest(rec.pkg.registry, rec.pkg.name)
  if (now && now !== rec.version) {
    stale += 1
    console.log(
      `✗  ${id}: recorded v${rec.version}, ${rec.pkg.name} publishes v${now} — re-read ${rec.source}`,
    )
  } else {
    console.log(`✓  ${id}: v${rec.version}`)
  }
}
process.exitCode = stale ? 1 : 0
