// Shared tab ID constants — imported by router.tsx and RouterShell.tsx
// without creating a circular dependency.

export const TABS = [
  'lucius',
  'dashboard',
  'inbox',
  'investments',
  'accounts',
  'assets',
  'commitments',
  'plan',
  'intel',
  'skills',
  'automations',
  'build',
  'profile',
  'connectors',
  'imports',
  'settings',
  'help',
] as const

export type TabId = (typeof TABS)[number]

/**
 * What the phone's bottom bar can have selected.
 *
 * `MobileNavTarget = TabId | 'lucius'` is redundant (TabId includes 'lucius') and kept only
 * for the name: 'lucius' is the /lucius route on BOTH viewports now. On a phone that route
 * renders the full-screen LuciusMobile tab; on desktop it renders the two-pane Lucius home.
 * Same string, same route, different rendering — the `&& isMobile` guards at the read sites
 * pick the rendering, never a separate "chat open" state (retired 2026-09-05:
 * docs/adr/2026-09-05-phone-lucius-is-the-lucius-route.md).
 *
 * The point of the union is that the bar takes ONE selection value. It used to take an
 * (activeTab, luciusOpen) pair, and every consumer re-derived "…and Lucius isn't open".
 */
export type MobileNavTarget = TabId | 'lucius'

// 'inbox' is deliberately excluded from PRIMARY_TABS — it is hidden pending further
// work. Kept in TABS/TAB_* records so the route/component stay reachable by direct URL
// and typed lookups don't break; just not linked from any nav.
export const PRIMARY_TABS: TabId[] = [
  'lucius',
  'assets',
  'accounts',
  'plan',
  'investments',
  'intel',
  'commitments',
  'dashboard',
]

// 'profile' is deliberately absent: Profile is no longer a tab, it is the first SECTION of
// the Settings overlay (2026-08-05 redesign). The id stays in TABS so /profile keeps working
// as a direct URL — it opens Settings on that section.
// 'build' is likewise hidden from nav for now (2026-08-06) — same treatment as
// 'inbox' above: the route and component stay reachable at /build.
// 'help' is deliberately NOT here (owner, 2026-08-23). It briefly had a slot on the
// reasoning that a self-hosted app's users have nobody else to ask — but Help is a
// reference surface, not a place you work, and a permanent sidebar row is prime
// space that a destination has to earn. It stays fully reachable: the topbar Help
// control (RouterShell.tsx) opens it, and /help still routes directly.
//
// This array also drives the mobile "More" sheet's Manage group (MobileTabBar.tsx maps
// over CONFIG_TABS generically), so removing it here covers both surfaces at once.
export const CONFIG_TABS: TabId[] = ['automations', 'skills', 'connectors', 'imports', 'settings']

export const TAB_LABELS: Record<TabId, string> = {
  lucius: 'Lucius',
  dashboard: 'Dashboard',
  inbox: 'Inbox',
  investments: 'Investments',
  accounts: 'Accounts',
  assets: 'Assets',
  commitments: 'Commitments',
  plan: 'Plan',
  intel: 'Intel',
  skills: 'Skills',
  automations: 'Automations',
  build: 'Build',
  profile: 'Profile',
  connectors: 'Connectors',
  imports: 'Imports',
  settings: 'Settings',
  help: 'Help',
}

export const TAB_DISPLAY_NAMES: Record<TabId, string> = {
  ...TAB_LABELS,
  intel: 'Intelligence',
  imports: 'Imports & sync',
}

// List pages that read as a centred column (`.app-layout--narrow`), like a settings page —
// not the data tabs, whose tables and charts are built to use the width.
export const NARROW_TABS = new Set<TabId>([
  'automations',
  'skills',
  'inbox',
  'connectors',
  'imports',
])

export const SELF_HEADER_TABS = new Set<TabId>([
  'lucius',
  'intel',
  'profile',
  'commitments',
  'automations', // Automations v3 owns its handoff masthead; detail/list vary the subtitle.
  'inbox',
  'skills',
  'accounts', // Accounts redesign renders its own header (sidebar + overview/account)
  'assets', // Assets tab renders its own PageHeader (faithful port of the design handoff)
  'plan', // Plan redesign renders its own PageHeader (masthead + sub-tabs)
  'dashboard', // Overview v2 renders its own PageHeader (title/subtitle + Customize actions)
  'build',
  'connectors', // Connectors redesign renders its own two-tier masthead + sticky subrow
  'help', // Help Center renders its own title/masthead
])

export const TAB_SUBTITLES: Record<TabId, string> = {
  lucius: 'Chat with Lucius — your personal wealth manager',
  dashboard: 'Your complete financial picture',
  inbox: 'Everything that needs your attention, in one place',
  investments: 'Stocks, ETFs, crypto, and commodities across all accounts',
  accounts: 'All your accounts — balances, liabilities, and receivables',
  assets: 'Things you own — property, vehicles, valuables — valued by a dated mark',
  commitments: "Dated obligations and how you'll fund them",
  plan: 'Budgets, projections, and retirement planning',
  intel: 'Market signals, watchlists, and your portfolio brief',
  skills: 'Reusable playbooks Lucius can run for you — one-click routines',
  automations:
    'Run on a schedule or when something happens. Every run is a conversation you can pick back up.',
  build: 'Customize Open Net Worth with a local coding engine',
  profile: 'Personal details, income, and FIRE settings',
  connectors: 'Channels, data sources, and tools this instance connects to',
  imports: 'Bring your history in from another budgeting app, and keep the two in step',
  settings: 'AI model, appearance, security, and your data — instance-level settings',
  help: 'Guides, concepts, and answers for running Open Net Worth',
}
