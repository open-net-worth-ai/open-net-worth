import { storyScope } from './intelScope'
import type { StoryArc } from './storyMeta'
import type { Watchlist, WatchlistItem } from './WatchlistsSection'

// Intel's tabs: My holdings · each watchlist of yours · Topics & macro. Split by KIND (ADR
// 2026-09-27-topics-are-followed-not-listed): a watchlist holds tickers, so a list tab is that
// list's pulse; topics are followed on their own, in no list, so they — and macro news — live on
// the last tab.
//
// A story shows on every tab it belongs to: My holdings keeps its rule (names you hold, plus
// portfolio-wide risks and price alerts); a list tab takes stories on its tickers (held or not);
// Topics & macro takes topic and macro stories and anything no list claims. Read / dismiss state
// is per story, so a story on two tabs is marked read on both at once — the tab badges just no
// longer add up.

export type IntelTabKey = 'holdings' | 'macro' | 'following' | `list:${string}`

export interface IntelTab {
  key: IntelTabKey
  label: string
  /** The watchlist behind a list tab. */
  listId?: string
}

export const TOPICS_TAB_LABEL = 'Topics & macro'

/** Famous portfolios you follow (flag `followed_portfolios`): a screen of its own, not a story
 *  feed, so no story is ever partitioned onto it. Intel appends it after the feed tabs. */
export const FOLLOWING_TAB: IntelTab = { key: 'following', label: 'Following' }

export const listTabKey = (id: string): IntelTabKey => `list:${id}`

export function listIdOf(key: IntelTabKey): string | null {
  return key.startsWith('list:') ? key.slice('list:'.length) : null
}

const ownLists = (watchlists: Watchlist[]) =>
  watchlists.filter((w) => !w.is_auto_sync).sort((a, b) => a.order - b.order)

export function buildTabs(watchlists: Watchlist[]): IntelTab[] {
  return [
    { key: 'holdings', label: 'My holdings' },
    ...ownLists(watchlists).map((w) => ({ key: listTabKey(w.id), label: w.name, listId: w.id })),
    { key: 'macro', label: TOPICS_TAB_LABEL },
  ]
}

/** The tab a watchlist lives on. */
export function tabOfList(watchlists: Watchlist[], id: string): IntelTabKey {
  const w = watchlists.find((x) => x.id === id)
  return !w || w.is_auto_sync ? 'holdings' : listTabKey(w.id)
}

/** A requested or remembered tab, checked against the lists that exist now. The legacy
 *  `watching` opens `lastList` (else your first list); a list that's gone falls back to My
 *  holdings. Null when `raw` isn't a tab key at all. */
export function resolveTabKey(
  raw: string | null | undefined,
  watchlists: Watchlist[],
  lastList?: string | null,
): IntelTabKey | null {
  if (raw === 'holdings' || raw === 'macro' || raw === 'following') return raw
  const own = ownLists(watchlists)
  if (raw === 'watching') {
    const list = own.find((w) => w.id === lastList) ?? own[0]
    return list ? listTabKey(list.id) : 'holdings'
  }
  if (raw?.startsWith('list:')) return tabOfList(watchlists, raw.slice('list:'.length))
  return null
}

/** Each of your lists' tickers (upper-cased). */
export function listTickers(watchlists: Watchlist[], items: WatchlistItem[]) {
  const out = new Map<string, Set<string>>()
  for (const w of ownLists(watchlists)) out.set(w.id, new Set())
  for (const item of items) {
    if (item.type !== 'ticker') continue
    for (const id of item.watchlist_ids ?? []) out.get(id)?.add(item.value.trim().toUpperCase())
  }
  return out
}

export function partitionByTab(
  stories: StoryArc[],
  held: Set<string>,
  watched: Set<string>,
  tickersByList: Map<string, Set<string>>,
): Record<IntelTabKey, StoryArc[]> {
  const out: Record<IntelTabKey, StoryArc[]> = { holdings: [], macro: [], following: [] }
  for (const id of tickersByList.keys()) out[listTabKey(id)] = []
  for (const s of stories) {
    const scope = storyScope(s, held, watched)
    if (scope === 'holdings') out.holdings.push(s)
    const ticker = (s.ticker ?? '').trim().toUpperCase()
    let claimed = false
    if (ticker) {
      for (const [id, tickers] of tickersByList) {
        if (!tickers.has(ticker)) continue
        out[listTabKey(id)]!.push(s)
        claimed = true
      }
    }
    // Topics & macro gets what nobody else shows — never lose a story to a tab that isn't there.
    if (scope !== 'holdings' && !claimed) out.macro.push(s)
  }
  return out
}

/** The tab to open for a story: the one on screen if it's there, else the first that has it. */
export function tabOfStory(
  id: string,
  current: IntelTabKey,
  byTab: Record<IntelTabKey, StoryArc[]>,
  tabs: IntelTab[],
): IntelTabKey | null {
  if (byTab[current]?.some((s) => s.id === id)) return current
  return tabs.find((t) => byTab[t.key]?.some((s) => s.id === id))?.key ?? null
}
