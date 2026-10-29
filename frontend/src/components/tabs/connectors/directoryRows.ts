// The catalog, resolved against live connector state.
//
// Split out of `catalog.ts` so that module can stay a LEAF: `adapter.ts` reads
// `catalogProvenanceFor` from the catalog, so the catalog must not import the adapter back.
// This module is where the two legitimately meet — it depends on both and nothing depends on it
// except the directory itself.

import { isInstalled, toEntry } from './adapter'
import {
  CATALOG,
  MCP_INSTALL_PRESETS,
  catalogProvenanceFor,
  filterDirectory,
  previewEndpoint,
  type CatalogEntry,
  type DirectoryRow,
} from './catalog'
import { connectorLogo } from './logos'
import { FAMILY_SECTIONS, type FamilyTab } from './model'
import type { Connector } from './types'

const MCP_PREFIX = 'mcp:'

/**
 * Two things only the server can answer: which catalog entries are already installed, and which
 * MCP servers exist that no catalog could know about (a user's own custom server). Both land
 * here, so the grid renders one uniform list.
 *
 * An installed MCP server that isn't a catalog entry is APPENDED rather than dropped — the same
 * "installed entries stay listed" rule (spec §6), applied to the one family whose membership is
 * open-ended. Its detail page is reachable from the directory exactly like any other card's.
 */
export function resolveDirectory(
  connectors: Connector[],
  catalog: CatalogEntry[] = CATALOG,
): DirectoryRow[] {
  const installed = connectors.filter(isInstalled)
  const installedIds = new Set(installed.map((c) => c.id))
  // Every NATIVE connector (id present here) is synthesized by `list_connectors()`
  // unconditionally — configured or not — so this lookup finds it even on a fresh instance
  // where the user hasn't set anything up yet. Only a catalog entry with no live counterpart
  // (every MCP entry) misses.
  const liveById = new Map(connectors.map((c) => [c.id, c]))

  // `developer`/`verified` are PROJECTED from the provenance fields, never authored beside
  // them — so the card badge and the vendor-trust panel can't disagree with the catalog.
  // `logo` is looked up from the id rather than authored beside the entry, so a mark cannot be
  // attached to the wrong card by hand; an entry with none keeps its tinted category glyph.
  // An installed MCP server whose endpoint IS a catalog entry's merges into that card (gear,
  // not +) instead of listing a second card for the same server beside it.
  const serverByEndpoint = new Map<string, Connector>()
  for (const c of installed) {
    const url = c.id.startsWith(MCP_PREFIX) ? toEntry(c).url?.trim() : undefined
    if (url && !serverByEndpoint.has(url)) serverByEndpoint.set(url, c)
  }
  const merged = new Set<string>()

  const rows: DirectoryRow[] = catalog.map((entry) => {
    const preset = entry.mcp ? MCP_INSTALL_PRESETS[entry.id] : undefined
    const endpoint =
      preset?.transport === 'remote'
        ? preset.url
        : preset
          ? [preset.command, preset.args].filter(Boolean).join(' ')
          : ''
    const server = endpoint ? serverByEndpoint.get(endpoint.trim()) : undefined
    if (server) merged.add(server.id)
    const live = liveById.get(entry.id)
    // `sub`/`desc`/`tools`: for a NATIVE entry these read the SAME `short_description`/
    // `long_description`/`tools` the connector's own installed detail page renders (`toEntry`,
    // below) — never the catalog's own copy, which native entries no longer author (see
    // `CatalogEntry.sub`'s doc comment). This is the 2026-08-14 fix (originally sub/desc only):
    // before it, this line was `...entry` alone, so an installed Email's directory card and its
    // detail page one click later could show two independently hand-edited strings that
    // silently drifted apart. `tools` had the SAME bug (2026-08-22: a native connector's
    // preview always showed the catalog's own — usually empty — tools list, never the live,
    // backend-driven one) and gets the same fix here. An MCP catalog entry has no live
    // counterpart to read (its id is a catalog key, e.g. `mcp-playwright`, not a server id), so
    // it keeps authoring its own `sub`/`desc`/`tools` here, unaffected.
    const liveText = live ? toEntry(live) : null
    return {
      ...entry,
      sub: liveText?.sub || entry.sub || '',
      desc: liveText?.desc || entry.desc || '',
      tools: liveText?.tools || entry.tools || [],
      logo: connectorLogo(entry.id),
      developer: entry.publisher ?? undefined,
      installed: installedIds.has(entry.id) || !!server,
      ...(server ? { installedId: server.id, url: toEntry(server).url } : {}),
    }
  })

  const known = new Set(catalog.map((e) => e.id))
  for (const c of installed) {
    if (!c.id.startsWith(MCP_PREFIX) || known.has(c.id) || merged.has(c.id)) continue
    // `toEntry` already folds in catalog provenance when the endpoint matches a catalog server,
    // so an installed Playwright MCP keeps its real verification here too.
    const e = toEntry(c)
    rows.push({
      id: e.id,
      name: e.name,
      icon: e.icon,
      hue: e.hue,
      // Both inherited from the catalog entry this server's ENDPOINT matches, or absent — a
      // user's own server has no mark and nothing truthful to file it under.
      logo: e.logo,
      categories: catalogProvenanceFor(e.url)?.categories ?? [],
      families: e.families,
      tag: e.tag,
      sub: e.sub,
      desc: e.desc,
      mcp: e.mcp,
      url: e.url,
      tools: e.tools,
      popular: false,
      installed: true,
      publisher: e.developer ?? null,
      verification: e.verification,
      developer: e.developer,
    })
  }
  return rows
}

/** How many not-yet-added connectors a list section suggests under its rows. */
export const SECTION_SUGGESTION_COUNT = 3

/** Per list section, and for the All tab as a whole: the first few entries not added yet, popular first and otherwise in
 *  catalog order — never the generic "Custom MCP server" slot, which has its own add link.
 *  An MCP catalog entry counts as added when an installed server answers at its endpoint:
 *  the directory lists that server as its own row, so `installed` alone is false for it. */
export function suggestionsBySection(
  connectors: Connector[],
): Partial<Record<FamilyTab, DirectoryRow[]>> {
  const rows = resolveDirectory(connectors)
  const installedEndpoints = new Set(
    rows.filter((d) => d.installed && d.mcp && d.url).map((d) => d.url!.trim()),
  )
  const added = (d: DirectoryRow) =>
    d.installed || installedEndpoints.has(previewEndpoint(d)?.trim() ?? '')
  const pick = (family: FamilyTab) =>
    filterDirectory(rows, family, '', 'popular')
      .filter((d) => !added(d) && !d.generic)
      .slice(0, SECTION_SUGGESTION_COUNT)
  return Object.fromEntries([
    ['all', pick('all')],
    ...FAMILY_SECTIONS.map((s) => [s.key, pick(s.key)]),
  ])
}
