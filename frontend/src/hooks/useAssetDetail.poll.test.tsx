// @vitest-environment jsdom
//
// Regression tests for the 60s background poll added to useAssetDetail:
//  - it re-fetches the detail (GET) on each 60s tick while the view is open, and
//  - a transient poll FAILURE keeps the last-good view (never flips to error).
// Fixtures carry news + a fresh last_refreshed_at so the mount-time auto-refresh
// (POST) path stays dormant — these tests exercise the GET poll in isolation.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, cleanup } from '@testing-library/react'
import { useAssetDetail, _clearDetailCache, seedDetailCache } from './useAssetDetail'

vi.mock('../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function okJson(body: unknown) {
  return { ok: true, json: async () => body } as Response
}

// Minimal detail payload — only the fields the hook's refresh logic reads. News is
// non-empty and last_refreshed_at is "now" so shouldRefresh() is false (no POST).
function makeDetail(last: number) {
  return {
    has_ticker: true,
    asset: { ticker: 'X', name: 'X', asset_class: 'equity' },
    price: { last, currency: 'USD', as_of: new Date().toISOString() },
    news: [{ headline: 'n' }],
    key_issues: [],
    last_refreshed_at: new Date().toISOString(),
  }
}

beforeEach(() => {
  mockAuthFetch.mockReset()
  _clearDetailCache()
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('useAssetDetail — 60s poll', () => {
  it('re-fetches and updates the price on each 60s tick', async () => {
    vi.useFakeTimers()
    mockAuthFetch.mockResolvedValue(okJson(makeDetail(10)))

    const { result } = renderHook(() => useAssetDetail('a1'))
    await act(async () => {}) // flush mount fetch
    expect(result.current.loading).toBe(false)
    expect(result.current.data?.price.last).toBe(10)
    const callsAtMount = mockAuthFetch.mock.calls.length

    mockAuthFetch.mockResolvedValue(okJson(makeDetail(11)))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000)
    })

    expect(result.current.data?.price.last).toBe(11)
    expect(mockAuthFetch.mock.calls.length).toBeGreaterThan(callsAtMount)
    // GET only — the poll must never hit the expensive POST /refresh path.
    expect(mockAuthFetch).not.toHaveBeenCalledWith(
      expect.stringContaining('/refresh'),
      expect.anything(),
    )
  })

  it('keeps the last-good view when a poll fails (no error flip)', async () => {
    vi.useFakeTimers()
    mockAuthFetch.mockResolvedValue(okJson(makeDetail(10)))

    const { result } = renderHook(() => useAssetDetail('a2'))
    await act(async () => {})
    expect(result.current.data?.price.last).toBe(10)

    mockAuthFetch.mockRejectedValue(new Error('network blip'))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000)
    })

    // Transient failure: data preserved, error stays false (view does not blank out).
    expect(result.current.data?.price.last).toBe(10)
    expect(result.current.error).toBe(false)
  })

  it('skips the poll while the tab is hidden', async () => {
    vi.useFakeTimers()
    mockAuthFetch.mockResolvedValue(okJson(makeDetail(10)))

    const { result } = renderHook(() => useAssetDetail('a3'))
    await act(async () => {})
    const callsAtMount = mockAuthFetch.mock.calls.length

    const hiddenSpy = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000)
    })
    expect(mockAuthFetch.mock.calls.length).toBe(callsAtMount) // no fetch while hidden
    expect(result.current.data?.price.last).toBe(10)
    hiddenSpy.mockRestore()
  })
})

describe('useAssetDetail — auto-refresh is gated on has_ticker', () => {
  it('does NOT auto-refresh a no-ticker asset (no wasted POST, no "researching" skeleton)', async () => {
    // A manual holding: no ticker, no news. It can never fetch news, so the mount-time
    // auto-refresh must stay dormant — otherwise the Overview flashes the researching
    // skeleton before showing "news unavailable".
    mockAuthFetch.mockResolvedValue(
      okJson({ ...makeDetail(10), has_ticker: false, news: [], key_issues: [] }),
    )
    const { result } = renderHook(() => useAssetDetail('no-ticker-1'))
    await act(async () => {})

    expect(result.current.loading).toBe(false)
    expect(mockAuthFetch).not.toHaveBeenCalledWith(
      expect.stringContaining('/refresh'),
      expect.anything(),
    )
  })

  it('DOES auto-refresh a ticker-backed asset that has no news yet', async () => {
    // Positive control: a real ticker with an empty brief should research on open.
    mockAuthFetch.mockResolvedValue(
      okJson({ ...makeDetail(10), has_ticker: true, news: [], key_issues: [] }),
    )
    renderHook(() => useAssetDetail('needs-research-1'))
    await act(async () => {})

    expect(mockAuthFetch).toHaveBeenCalledWith(
      expect.stringContaining('/refresh'),
      expect.anything(),
    )
  })
})

describe('useAssetDetail — HELD-or-WATCHED instruments are kept warm by the background precompute, never on-open (Problem B point 3, Correction)', () => {
  it('does NOT auto-refresh a HELD instrument even with empty news/key_issues', async () => {
    mockAuthFetch.mockResolvedValue(
      okJson({
        ...makeDetail(10),
        has_ticker: true,
        news: [],
        key_issues: [],
        position: { held: true, qty: 5 },
      }),
    )
    renderHook(() => useAssetDetail('held-1'))
    await act(async () => {})

    expect(mockAuthFetch).not.toHaveBeenCalledWith(
      expect.stringContaining('/refresh'),
      expect.anything(),
    )
  })

  it('does NOT auto-refresh a HELD instrument whose last_refreshed_at is stale', async () => {
    mockAuthFetch.mockResolvedValue(
      okJson({
        ...makeDetail(10),
        has_ticker: true,
        news: [{ headline: 'n' }],
        key_issues: [{ question: 'q' }],
        last_refreshed_at: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(), // 2h stale
        position: { held: true, qty: 5 },
      }),
    )
    renderHook(() => useAssetDetail('held-2'))
    await act(async () => {})

    expect(mockAuthFetch).not.toHaveBeenCalledWith(
      expect.stringContaining('/refresh'),
      expect.anything(),
    )
  })

  it('does NOT auto-refresh a WATCHED-but-not-held instrument with empty news', async () => {
    // Correction (ADR .../cross-subject-fan-out-and-asset-brief-consolidation): the
    // consolidated key_issues synthesis is no longer held-only — a watched-not-held ticker is
    // now ALSO kept warm by the background precompute (_is_watched_instrument), so its on-open
    // refresh is suppressed exactly like a held instrument's.
    mockAuthFetch.mockResolvedValue(
      okJson({
        ...makeDetail(10),
        has_ticker: true,
        news: [],
        key_issues: [],
        watchlist: { following: true, price_alerts: [] },
      }),
    )
    renderHook(() => useAssetDetail('watched-1'))
    await act(async () => {})

    expect(mockAuthFetch).not.toHaveBeenCalledWith(
      expect.stringContaining('/refresh'),
      expect.anything(),
    )
  })

  it('does NOT auto-refresh a WATCHED instrument whose monitor_news is absent (defaults true)', async () => {
    // monitor_news defaults true when absent, matching the backend's own
    // `item.get("monitor_news", True)` convention — a watchlist item with no explicit
    // monitor_news key must still count as watched.
    mockAuthFetch.mockResolvedValue(
      okJson({
        ...makeDetail(10),
        has_ticker: true,
        news: [],
        key_issues: [],
        watchlist: { following: true, price_alerts: [] },
      }),
    )
    renderHook(() => useAssetDetail('watched-default-monitor-news'))
    await act(async () => {})

    expect(mockAuthFetch).not.toHaveBeenCalledWith(
      expect.stringContaining('/refresh'),
      expect.anything(),
    )
  })

  it('STILL auto-refreshes a WATCHED-but-MUTED instrument (monitor_news: false)', async () => {
    // A muted watch item is explicitly excluded from monitor_news scans everywhere else in
    // this package (news_screen, rss_poller_hot, the backend precompute's own
    // _is_watched_instrument) — so it is never kept warm by the background pass, and must keep
    // the pre-existing on-open refresh.
    mockAuthFetch.mockResolvedValue(
      okJson({
        ...makeDetail(10),
        has_ticker: true,
        news: [],
        key_issues: [],
        watchlist: { following: true, monitor_news: false, price_alerts: [] },
      }),
    )
    renderHook(() => useAssetDetail('watched-muted-1'))
    await act(async () => {})

    expect(mockAuthFetch).toHaveBeenCalledWith(
      expect.stringContaining('/refresh'),
      expect.anything(),
    )
  })

  it('STILL auto-refreshes an ad-hoc ticker (not held, not watched) with empty news', async () => {
    mockAuthFetch.mockResolvedValue(
      okJson({
        ...makeDetail(10),
        has_ticker: true,
        news: [],
        key_issues: [],
        watchlist: { following: false, price_alerts: [] },
      }),
    )
    renderHook(() => useAssetDetail('adhoc-1'))
    await act(async () => {})

    expect(mockAuthFetch).toHaveBeenCalledWith(
      expect.stringContaining('/refresh'),
      expect.anything(),
    )
  })

  it('the explicit Refresh button still works on a held instrument (force=true unaffected)', async () => {
    mockAuthFetch.mockImplementation((url: unknown) => {
      if (typeof url === 'string' && url.includes('/refresh')) {
        return Promise.resolve(
          okJson({ refreshed: true, last_refreshed_at: new Date().toISOString() }),
        )
      }
      return Promise.resolve(okJson({ ...makeDetail(10), position: { held: true, qty: 5 } }))
    })
    const { result } = renderHook(() => useAssetDetail('held-explicit-1'))
    await act(async () => {})
    // No on-open auto-refresh fired for this held instrument.
    expect(mockAuthFetch).not.toHaveBeenCalledWith(
      expect.stringContaining('/refresh'),
      expect.anything(),
    )

    await act(async () => {
      await result.current.refresh()
    })

    // The EXPLICIT tap still hits POST /refresh?force=true.
    expect(mockAuthFetch).toHaveBeenCalledWith(
      expect.stringContaining('/refresh?force=true'),
      expect.anything(),
    )
  })
})

describe('useAssetDetail — fast poll while a refresh is in flight', () => {
  it('polls faster (GET only) while refreshing, so progressive backend writes show up sooner', async () => {
    vi.useFakeTimers()

    // Mount-time auto-refresh fires (empty news/key_issues + has_ticker). Keep the POST
    // /refresh pending so `refreshing` stays true while we advance the fast-poll timer.
    let resolveRefresh!: (r: Response) => void
    mockAuthFetch.mockImplementation((url: unknown) => {
      if (typeof url === 'string' && url.includes('/refresh')) {
        return new Promise<Response>((r) => (resolveRefresh = r))
      }
      return Promise.resolve(
        okJson({ ...makeDetail(10), news: [], key_issues: [], has_ticker: true }),
      )
    })

    renderHook(() => useAssetDetail('fastpoll-1'))
    await act(async () => {}) // flush mount GET, which kicks off the auto POST /refresh

    const callsBeforeFastPoll = mockAuthFetch.mock.calls.length

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })

    // A GET fired during the 2s fast-poll window, even though POST /refresh is still pending —
    // this is what makes the backend's progressive writes (news_summaries landing before the
    // deeper analysis) visible instead of waiting for the single post-refresh refetch.
    expect(mockAuthFetch.mock.calls.length).toBeGreaterThan(callsBeforeFastPoll)
    const lastCallUrl = mockAuthFetch.mock.calls[mockAuthFetch.mock.calls.length - 1]?.[0]
    expect(String(lastCallUrl)).not.toContain('/refresh')

    await act(async () => {
      resolveRefresh(okJson({ ...makeDetail(10), news: [{ headline: 'n' }], key_issues: [] }))
      await Promise.resolve()
    })
  })

  it('stops the fast poll once refreshing settles back to false', async () => {
    vi.useFakeTimers()
    // has_narrative: true — isolates this test to the `refreshing` (explicit-POST) fast-poll
    // path; otherwise the on-demand background-watch effect would itself arm the fast poll on
    // this not-yet-narrated fixture (correct behavior, covered by its own describe block above).
    mockAuthFetch.mockResolvedValue(okJson({ ...makeDetail(10), has_narrative: true })) // fresh brief → no auto-refresh

    const { result } = renderHook(() => useAssetDetail('fastpoll-2'))
    await act(async () => {})
    expect(result.current.refreshing).toBe(false)
    expect(result.current.backgroundRefreshing).toBe(false)

    const callsAtRest = mockAuthFetch.mock.calls.length
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })
    // No refresh ever started → the fast-poll interval was never armed.
    expect(mockAuthFetch.mock.calls.length).toBe(callsAtRest)
  })
})

describe('useAssetDetail — on-demand background refresh (backend-initiated top-up)', () => {
  // The backend's on-demand key_issues top-up (backend.monitoring.analyst.
  // trigger_on_demand_brief_refresh) fires with NO POST from this hook — it stamps
  // key_issues_refresh_stage server-side and this hook picks it up purely by observing the GET
  // response. This field is SEPARATE from refresh_stage (R2 fix, 2026-08-31 — owner-reproduced
  // live on a cold KO ad-hoc open: the two backend pipelines can run concurrently for the same
  // instrument, and sharing one field let whichever finished first mask the other as still
  // in-flight). The held position shape keeps the on-open auto-refresh dormant except where a
  // test deliberately arms it.

  it('recognizes a stamped non-ready key_issues_refresh_stage as the QUIET background case, not the blocking one', async () => {
    // Proves the stamped stage lands in the non-blocking state machine branch
    // (backgroundRefreshing), never the blocking one (refreshing — which drives the
    // full-skeleton takeover and the Refresh button's own disabled/label state in
    // AssetDetailView). A held instrument already has content on screen, so
    // AssetDetailView's skeleton condition (`refreshing && !hasNews && !hasIssues`) must never
    // fire here regardless — this hook-level assertion is the direct proof that its gating
    // input, `refreshing`, stays false throughout.
    mockAuthFetch.mockResolvedValue(
      okJson({
        ...makeDetail(10),
        position: { held: true, qty: 5 },
        key_issues_refresh_stage: 'analysing',
      }),
    )

    const { result } = renderHook(() => useAssetDetail('ondemand-1'))
    await act(async () => {})

    expect(result.current.backgroundRefreshing).toBe(true)
    expect(result.current.refreshing).toBe(false) // the BLOCKING flag never trips
  })

  it('does NOT set backgroundRefreshing once has_narrative is already true', async () => {
    // "Landed" is has_narrative, not key_issues_refresh_stage — the backend stamps "ready" the
    // instant its pass finishes even when that pass only filled key_issues and left
    // portfolio_narrative empty (see useAssetDetail.ts's BACKGROUND_REFRESH_WATCH_MS comment),
    // so a null/ready stage alone no longer proves nothing needs watching.
    mockAuthFetch.mockResolvedValue(
      okJson({
        ...makeDetail(10),
        position: { held: true, qty: 5 },
        key_issues_refresh_stage: null,
        has_narrative: true,
      }),
    )
    const { result } = renderHook(() => useAssetDetail('ondemand-2'))
    await act(async () => {})

    expect(result.current.backgroundRefreshing).toBe(false)
  })

  it('arms the fast (2s) poll while backgroundRefreshing, same as an explicit refresh', async () => {
    vi.useFakeTimers()
    mockAuthFetch.mockResolvedValue(
      okJson({
        ...makeDetail(10),
        position: { held: true, qty: 5 },
        key_issues_refresh_stage: 'analysing',
      }),
    )

    renderHook(() => useAssetDetail('ondemand-3'))
    await act(async () => {})
    const callsAtMount = mockAuthFetch.mock.calls.length

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })

    expect(mockAuthFetch.mock.calls.length).toBeGreaterThan(callsAtMount)
  })

  it('clears backgroundRefreshing once a later poll observes has_narrative land', async () => {
    vi.useFakeTimers()
    let stage: string | null = 'analysing'
    let hasNarrative = false
    mockAuthFetch.mockImplementation(() =>
      Promise.resolve(
        okJson({
          ...makeDetail(10),
          position: { held: true, qty: 5 },
          key_issues_refresh_stage: stage,
          has_narrative: hasNarrative,
        }),
      ),
    )

    const { result } = renderHook(() => useAssetDetail('ondemand-4'))
    await act(async () => {})
    expect(result.current.backgroundRefreshing).toBe(true)

    stage = 'ready'
    hasNarrative = true
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })

    expect(result.current.backgroundRefreshing).toBe(false)
  })

  it('does NOT clear backgroundRefreshing on a stage:ready poll whose narrative is still empty', async () => {
    // The exact live gap this fix closes (owner-reproduced, UBER open-then-watch,
    // 2026-09-03): the backend can stamp key_issues_refresh_stage "ready" while
    // portfolio_narrative is still empty (key_issues filled, narrative didn't land in the same
    // pass) — that must NOT read as "done" here, or the fast poll gives up long before the
    // backend's own 5-minute narrative-missing retry fires again.
    vi.useFakeTimers()
    mockAuthFetch.mockResolvedValue(
      okJson({
        ...makeDetail(10),
        position: { held: true, qty: 5 },
        key_issues_refresh_stage: 'ready',
        has_narrative: false,
      }),
    )

    const { result } = renderHook(() => useAssetDetail('ondemand-4b'))
    await act(async () => {})

    expect(result.current.backgroundRefreshing).toBe(true)
  })

  it('self-heals a permanently stuck (never-narrated) instrument after the bounded watch window', async () => {
    // Defends against a rare backend failure mode that leaves has_narrative false forever
    // (a wedged process — never observed from this on-demand trigger in practice, but this
    // bound must hold regardless of cause). Without it, a phantom "Refreshing…" banner could
    // pin itself on the page permanently. The window (8 min — must match
    // BACKGROUND_REFRESH_WATCH_MS in useAssetDetail.ts) comfortably outlasts the backend's own
    // 5-minute narrative-missing retry cadence, so a real in-progress pass isn't mistaken for
    // wedged.
    vi.useFakeTimers()
    mockAuthFetch.mockResolvedValue(
      okJson({
        ...makeDetail(10),
        position: { held: true, qty: 5 },
        key_issues_refresh_stage: 'analysing', // never advances, ever
        has_narrative: false, // never lands, ever
      }),
    )

    const { result } = renderHook(() => useAssetDetail('ondemand-5'))
    await act(async () => {})
    expect(result.current.backgroundRefreshing).toBe(true)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(8 * 60_000)
    })

    expect(result.current.backgroundRefreshing).toBe(false)
  })

  it('sets backgroundRefreshing even while an explicit Refresh POST is ALSO in flight — the two are independent', async () => {
    // R2 fix regression: the OLD implementation gated the whole watch effect behind
    // `if (refreshingRef.current) return`, so a key_issues_refresh_stage transition landing
    // WHILE `refreshing` was true was silently missed (refreshingRef is a ref, not a
    // dependency, so its own flip back to false never re-ran the effect — a later poll could
    // carry the stage past "analysing" with nobody ever having armed the watch). The guard is
    // now removed entirely; both flags can be true at once, and the render layer already ORs
    // them (`refreshing || backgroundRefreshing`) so this is safe, not a double banner.
    mockAuthFetch.mockImplementation((url: unknown) => {
      if (typeof url === 'string' && url.includes('/refresh')) {
        return new Promise<Response>(() => {}) // never resolves — refreshing stays true
      }
      return Promise.resolve(
        okJson({
          ...makeDetail(10),
          news: [],
          key_issues: [],
          has_ticker: true,
          key_issues_refresh_stage: 'analysing',
        }),
      )
    })

    const { result } = renderHook(() => useAssetDetail('ondemand-6'))
    await act(async () => {}) // mount GET (empty news/key_issues) kicks off auto POST /refresh

    expect(result.current.refreshing).toBe(true)
    expect(result.current.backgroundRefreshing).toBe(true)
  })

  it('R2 regression: recognizes key_issues_refresh_stage as still in-flight even after refresh_stage independently reaches ready', async () => {
    // The exact live bug (owner-reproduced, cold KO ad-hoc open): the news/facts/peers
    // pipeline (refresh_stage) routinely finishes BEFORE the separate on-demand key_issues
    // pass. With the old shared field, refresh_stage reaching "ready" masked the still-running
    // key_issues pass entirely. Each pipeline's own field must be judged independently.
    mockAuthFetch.mockResolvedValue(
      okJson({
        ...makeDetail(10),
        position: { held: true, qty: 5 },
        refresh_stage: 'ready',
        key_issues_refresh_stage: 'analysing',
      }),
    )

    const { result } = renderHook(() => useAssetDetail('ondemand-7'))
    await act(async () => {})

    expect(result.current.backgroundRefreshing).toBe(true)
  })
})

// The Analysis tab dropped to "No key issues yet" while the bull/bear pass was still running:
// the watch never re-armed after it lapsed or after a Refresh, nor on a cached navigation that
// changed no has_narrative dependency.
describe('useAssetDetail — the background watch re-arms when it should', () => {
  const unnarrated = (stage: string | null = null) => ({
    ...makeDetail(10),
    position: { held: true, qty: 5 },
    has_narrative: false,
    key_issues_refresh_stage: stage,
  })

  it('arms for a cached reopen of a second not-yet-narrated asset', async () => {
    seedDetailCache('nav-b', unnarrated() as never)
    mockAuthFetch.mockResolvedValue(okJson(unnarrated()))

    const { result, rerender } = renderHook(({ id }) => useAssetDetail(id), {
      initialProps: { id: 'nav-a' },
    })
    await act(async () => {})
    expect(result.current.backgroundRefreshing).toBe(true)

    rerender({ id: 'nav-b' })
    await act(async () => {})

    expect(result.current.data).toBeTruthy()
    expect(result.current.backgroundRefreshing).toBe(true)
  })

  it('restarts the watch after a Refresh that ends with the narrative still missing', async () => {
    vi.useFakeTimers()
    mockAuthFetch.mockImplementation((url: unknown) => {
      if (typeof url === 'string' && url.includes('/refresh')) {
        return Promise.resolve(okJson({ refreshed: true }))
      }
      return Promise.resolve(okJson(unnarrated()))
    })

    const { result } = renderHook(() => useAssetDetail('rearm-refresh'))
    await act(async () => {})
    await act(async () => {
      await vi.advanceTimersByTimeAsync(8 * 60_000)
    })
    expect(result.current.backgroundRefreshing).toBe(false)

    await act(async () => {
      await result.current.refresh()
    })

    expect(result.current.refreshing).toBe(false)
    expect(result.current.backgroundRefreshing).toBe(true)
  })

  it('keeps the fast poll alive while the backend stamps its pass as running, after the watch lapsed', async () => {
    vi.useFakeTimers()
    mockAuthFetch.mockResolvedValue(okJson(unnarrated('analysing')))

    const { result } = renderHook(() => useAssetDetail('stage-poll'))
    await act(async () => {})
    await act(async () => {
      await vi.advanceTimersByTimeAsync(8 * 60_000)
    })
    expect(result.current.backgroundRefreshing).toBe(false)

    const calls = mockAuthFetch.mock.calls.length
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })
    expect(mockAuthFetch.mock.calls.length).toBeGreaterThan(calls)
  })

  it('does not fast-poll after the watch lapsed once the stage reads ready', async () => {
    vi.useFakeTimers()
    mockAuthFetch.mockResolvedValue(okJson(unnarrated('ready')))

    renderHook(() => useAssetDetail('stage-ready'))
    await act(async () => {})
    await act(async () => {
      await vi.advanceTimersByTimeAsync(8 * 60_000)
    })

    const calls = mockAuthFetch.mock.calls.length
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })
    expect(mockAuthFetch.mock.calls.length).toBe(calls)
  })
})

describe('useAssetDetail — instant reopen (stale-while-revalidate)', () => {
  it('first load shows the skeleton (loading=true) until the GET lands', async () => {
    let resolve!: (r: Response) => void
    mockAuthFetch.mockReturnValueOnce(new Promise<Response>((r) => (resolve = r)))

    const { result } = renderHook(() => useAssetDetail('reopen-1'))
    expect(result.current.loading).toBe(true) // no cache yet → skeleton
    expect(result.current.data).toBeNull()

    await act(async () => {
      resolve(okJson(makeDetail(10)))
      await Promise.resolve()
    })
    expect(result.current.loading).toBe(false)
    expect(result.current.data?.price.last).toBe(10)
  })

  it('reopen renders the cached view immediately — NO skeleton — then revalidates', async () => {
    // First open populates the cache.
    mockAuthFetch.mockResolvedValue(okJson(makeDetail(10)))
    const first = renderHook(() => useAssetDetail('reopen-2'))
    await act(async () => {})
    expect(first.result.current.data?.price.last).toBe(10)
    first.unmount()

    // Reopen: a SLOW GET is in flight, but the cached data must show instantly.
    let resolve!: (r: Response) => void
    mockAuthFetch.mockReturnValueOnce(new Promise<Response>((r) => (resolve = r)))
    const { result } = renderHook(() => useAssetDetail('reopen-2'))

    expect(result.current.loading).toBe(false) // ← no skeleton on reopen
    expect(result.current.data?.price.last).toBe(10) // ← old data shown immediately

    // Background revalidate lands with a newer price → updates in place.
    await act(async () => {
      resolve(okJson(makeDetail(12)))
      await Promise.resolve()
    })
    expect(result.current.data?.price.last).toBe(12)
  })

  it('keeps cached data (no error blank) if the reopen revalidate fails', async () => {
    mockAuthFetch.mockResolvedValue(okJson(makeDetail(10)))
    const first = renderHook(() => useAssetDetail('reopen-3'))
    await act(async () => {})
    first.unmount()

    mockAuthFetch.mockRejectedValueOnce(new Error('cold backend timeout'))
    const { result } = renderHook(() => useAssetDetail('reopen-3'))
    await act(async () => {})

    expect(result.current.error).toBe(false)
    expect(result.current.data?.price.last).toBe(10) // stale data preserved
  })
})

describe('useAssetDetail — refreshNotice on a declined explicit Refresh tap', () => {
  // Regression (owner report 2026-08-11): "i have to tap multiple times ... ah, it hit rate
  // limit" — the backend answers a declined refresh with 200 {refreshed: false, reason}
  // almost instantly, so the button re-enabled with nothing visibly different. refreshNotice
  // surfaces WHY on an explicit tap so the user isn't left guessing.
  function mockGetThenPost(postBody: unknown) {
    mockAuthFetch.mockImplementation((url: unknown) => {
      if (typeof url === 'string' && url.includes('/refresh')) {
        return Promise.resolve(okJson(postBody))
      }
      return Promise.resolve(okJson(makeDetail(10)))
    })
  }

  it('surfaces a human message when an explicit refresh is rate-limited', async () => {
    mockGetThenPost({ refreshed: false, reason: 'rate_limited', last_refreshed_at: null })
    const { result } = renderHook(() => useAssetDetail('notice-1'))
    await act(async () => {})

    await act(async () => {
      await result.current.refresh()
    })

    expect(result.current.refreshNotice).toBe('Just refreshed — try again shortly')
  })

  it('surfaces a message when already fresh', async () => {
    mockGetThenPost({ refreshed: false, reason: 'fresh', last_refreshed_at: null })
    const { result } = renderHook(() => useAssetDetail('notice-2'))
    await act(async () => {})

    await act(async () => {
      await result.current.refresh()
    })

    expect(result.current.refreshNotice).toBe('Already up to date')
  })

  it('auto-clears the notice after a few seconds', async () => {
    vi.useFakeTimers()
    mockGetThenPost({ refreshed: false, reason: 'rate_limited', last_refreshed_at: null })
    const { result } = renderHook(() => useAssetDetail('notice-3'))
    await act(async () => {})

    await act(async () => {
      await result.current.refresh()
    })
    expect(result.current.refreshNotice).not.toBeNull()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000)
    })
    expect(result.current.refreshNotice).toBeNull()
  })

  it('sets no notice when the explicit refresh actually succeeds', async () => {
    mockGetThenPost({ refreshed: true, last_refreshed_at: new Date().toISOString() })
    const { result } = renderHook(() => useAssetDetail('notice-4'))
    await act(async () => {})

    await act(async () => {
      await result.current.refresh()
    })

    expect(result.current.refreshNotice).toBeNull()
  })

  it('stays silent for the background on-open auto-refresh (force=false)', async () => {
    // A ticker-backed asset with no news yet triggers the SILENT mount-time auto-refresh —
    // it must never surface a notice the user never asked for.
    mockAuthFetch.mockImplementation((url: unknown) => {
      if (typeof url === 'string' && url.includes('/refresh')) {
        return Promise.resolve(
          okJson({ refreshed: false, reason: 'rate_limited', last_refreshed_at: null }),
        )
      }
      return Promise.resolve(
        okJson({ ...makeDetail(10), news: [], key_issues: [], has_ticker: true }),
      )
    })
    const { result } = renderHook(() => useAssetDetail('notice-5'))
    await act(async () => {})

    expect(result.current.refreshNotice).toBeNull()
  })
})
