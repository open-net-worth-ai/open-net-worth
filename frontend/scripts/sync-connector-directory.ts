// Regenerates backend/services/connector_directory.json from the Browse & add catalog.
// Run: npm run sync:connector-directory
import { writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import {
  buildDirectorySnapshot,
  serializeDirectorySnapshot,
} from '../src/components/tabs/connectors/directorySnapshot'

const target = resolve(process.cwd(), '../backend/services/connector_directory.json')
const snapshot = buildDirectorySnapshot()
writeFileSync(target, serializeDirectorySnapshot(snapshot))
console.log(`wrote ${snapshot.entries.length} entries to ${target}`)
