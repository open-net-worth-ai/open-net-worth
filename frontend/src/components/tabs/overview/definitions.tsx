// Overview v2 — WIDGET_DEFINITIONS: the one registry every widget instance's
// `defId` resolves against, whether the def is hand-built or one of the three
// Lucius-generated rows (`lux-ask`/`lux-html`) — see types.ts.
// Ported from the design handoff's `overview_v2b/app.jsx` WIDGET_DEFS array
// (`id` → `defId`; `source: [tabKey, tabLabel]` → `source: {tab, label}`).

import type { ReactElement } from 'react'
import type { WidgetDefinition, WidgetGroup } from './types'
import type { PresetWidget } from './grid'
import {
  SERIES_PERIOD_OPTIONS,
  CATEGORIES_SETTING,
  TAGS_SETTING,
  TOTAL_PERIOD_OPTIONS,
  periodSetting,
} from './widgets/reportScope'

const icon = (paths: string, viewBox = '0 0 24 24'): ReactElement => (
  <svg
    width="17"
    height="17"
    viewBox={viewBox}
    fill="none"
    stroke="currentColor"
    strokeWidth="1.8"
    strokeLinecap="round"
    strokeLinejoin="round"
    dangerouslySetInnerHTML={{ __html: paths }}
  />
)

export const WIDGET_GROUPS: WidgetGroup[] = [
  'Net worth',
  'Overview',
  'Cash flow',
  'Investing',
  'Planning',
  'Reports',
  'Content',
]

// The starter "Subscriptions" category's stable seed id (backend/models/category_defs.py).
const STARTER_SUBSCRIPTIONS_CATEGORY = 'cat_f2ff09940000'

export const WIDGET_DEFINITIONS: WidgetDefinition[] = [
  // ── Net worth ──────────────────────────────────────────────────────────────
  {
    defId: 'hero',
    label: 'Net worth & trend',
    group: 'Net worth',
    dsize: 12,
    dh: 224,
    maxh: 336,
    desc: 'Headline number, trend chart with market-vs-saved split, key stats.',
    title: 'Net worth',
    shape: 'nw-headline',
    source: { tab: 'accounts', label: 'Accounts' },
    settings: [
      {
        key: 'chart',
        type: 'segment',
        label: 'Chart',
        options: ['Trend', 'Composition'],
        default: 'Trend',
      },
      {
        key: 'range',
        type: 'segment',
        label: 'Range',
        options: ['1M', '3M', '6M', '1Y', 'All'],
        default: '3M',
      },
    ],
    icon: icon('<path d="M3 17l5-5 4 3 8-8" /><path d="M16 7h4v4" />'),
  },
  {
    defId: 'networth-nums',
    label: 'Net worth (numbers)',
    group: 'Net worth',
    dsize: 6,
    dh: 148,
    desc: 'Headline net worth, change, and the key figures — no chart.',
    title: 'Net worth',
    source: { tab: 'accounts', label: 'Accounts' },
    icon: icon('<path d="M4 7h7M4 12h10M4 17h6" /><path d="M17.5 15.5l2 2 3-3.5" />'),
  },
  {
    defId: 'networth-chart',
    label: 'Net worth chart',
    group: 'Net worth',
    dsize: 8,
    dh: 224,
    desc: 'Just the trend / composition chart — no headline or figures.',
    title: 'Net worth trend',
    shape: 'nw-chart',
    source: { tab: 'accounts', label: 'Accounts' },
    settings: [
      {
        key: 'range',
        type: 'segment',
        label: 'Range',
        options: ['1M', '3M', '6M', '1Y', 'All'],
        default: '3M',
      },
    ],
    icon: icon('<path d="M3 17l5-5 4 3 8-8" /><path d="M4 21h16" />'),
  },
  {
    defId: 'alloc',
    rows: { options: [5, 10, 'all'], default: 'all' },
    label: 'Asset allocation',
    group: 'Net worth',
    dsize: 4,
    dh: 260,
    desc: 'Your money by asset class — list, bar chart, or pie.',
    title: 'By asset class',
    shape: 'breakdown',
    source: { tab: 'accounts', label: 'Accounts' },
    icon: icon('<path d="M21 12A9 9 0 1 1 12 3" /><path d="M12 3a9 9 0 0 1 9 9h-9z" />'),
  },
  {
    defId: 'reach',
    rows: { options: [5, 10, 'all'], default: 'all' },
    label: 'Reachability',
    group: 'Net worth',
    dsize: 4,
    dh: 220,
    desc: 'How quickly you can reach your money — list, bar, or pie.',
    title: 'Reachability',
    shape: 'breakdown',
    source: { tab: 'accounts', label: 'Accounts' },
    icon: icon(
      '<rect x="4" y="5" width="16" height="3.4" rx="1.7" /><rect x="4" y="10.3" width="11" height="3.4" rx="1.7" /><rect x="4" y="15.6" width="7" height="3.4" rx="1.7" />',
    ),
  },
  {
    defId: 'venues',
    rows: { options: [5, 10, 'all'], default: 'all' },
    label: 'Where money is held',
    group: 'Net worth',
    dsize: 4,
    dh: 260,
    desc: 'Value by account and venue — list, bar, or pie.',
    title: "Where it's held",
    shape: 'breakdown',
    source: { tab: 'accounts', label: 'Accounts' },
    icon: icon(
      '<rect x="4" y="9" width="16" height="12" rx="1.5" /><path d="M8 21V5a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v16" /><line x1="11" y1="8" x2="13" y2="8" />',
    ),
  },
  // ── Overview ───────────────────────────────────────────────────────────────
  {
    defId: 'attention',
    rows: { options: [3, 5, 'all'], default: 'all' },
    label: 'Needs attention',
    group: 'Overview',
    dsize: 12,
    dh: 196,
    desc: 'Prioritized flags — overdue payments, shortfalls, risks — with actions.',
    title: 'Needs attention',
    source: { tab: 'dashboard', label: 'Dashboard' },
    icon: icon('<path d="M12 3l10 18H2z" /><line x1="12" y1="10" x2="12" y2="14.5" />'),
  },
  {
    defId: 'briefing',
    label: 'Daily briefing',
    group: 'Overview',
    dsize: 6,
    dh: 196,
    prose: true,
    desc: "Lucius' morning note and suggested questions.",
    title: "Lucius's daily briefing",
    source: { tab: 'dashboard', label: 'Dashboard' },
    // The real Lucius brand mark (identity.tsx's `Spark`, also used by
    // `lux-ask` below) — this widget was using a generic 4-point diamond
    // instead of the actual Lucius sparkle.
    icon: icon(
      '<path d="M9.94 14.06A2 2 0 0 0 8.5 12.6l-5.4-1.4a.4.4 0 0 1 0-.78l5.4-1.4A2 2 0 0 0 9.94 7.6l1.4-5.4a.4.4 0 0 1 .78 0l1.4 5.4A2 2 0 0 0 14.96 9l5.4 1.4a.4.4 0 0 1 0 .78l-5.4 1.4a2 2 0 0 0-1.44 1.44l-1.4 5.4a.4.4 0 0 1-.78 0z" />',
    ),
  },
  {
    defId: 'lux-ask',
    label: 'Ask Lucius',
    group: 'Overview',
    dsize: 6,
    dh: 196,
    prose: true,
    desc: 'Ask Lucius anything about your money — opens the real assistant panel with your question.',
    title: 'Ask Lucius',
    icon: icon(
      '<path d="M9.94 14.06A2 2 0 0 0 8.5 12.6l-5.4-1.4a.4.4 0 0 1 0-.78l5.4-1.4A2 2 0 0 0 9.94 7.6l1.4-5.4a.4.4 0 0 1 .78 0l1.4 5.4A2 2 0 0 0 14.96 9l5.4 1.4a.4.4 0 0 1 0 .78l-5.4 1.4a2 2 0 0 0-1.44 1.44l-1.4 5.4a.4.4 0 0 1-.78 0z" />',
    ),
  },
  {
    defId: 'summary',
    label: 'Summary card',
    group: 'Overview',
    dsize: 4,
    dh: 128,
    prose: true,
    desc: 'A single headline figure with its month-to-date change.',
    title: 'Summary',
    icon: icon(
      '<rect x="3.5" y="4.5" width="17" height="15" rx="2" /><line x1="7" y1="11" x2="14" y2="11" /><line x1="7" y1="14.5" x2="11" y2="14.5" />',
    ),
  },
  // ── Cash flow ──────────────────────────────────────────────────────────────
  {
    defId: 'upcoming',
    rows: { options: [5, 10, 15, 'all'], default: 10 },
    label: 'Next 30 days',
    group: 'Cash flow',
    dsize: 6,
    dh: 196,
    desc: 'Money going out soon (installments, auto-debits, set-asides) and recurring income due in the same 30 days.',
    title: 'Next 30 days',
    source: { tab: 'commitments', label: 'Commitments' },
    icon: icon(
      '<rect x="3.5" y="5" width="17" height="15.5" rx="2" /><line x1="3.5" y1="9.5" x2="20.5" y2="9.5" /><line x1="8" y1="3" x2="8" y2="6.5" /><line x1="16" y1="3" x2="16" y2="6.5" />',
    ),
  },
  {
    defId: 'spending',
    rows: { options: [5, 10, 15, 'all'], default: 'all' },
    label: 'Spending pulse',
    group: 'Cash flow',
    dsize: 6,
    dh: 168,
    desc: "Budget envelopes month to date — what's on pace, what's over.",
    title: 'Spending pulse',
    source: { tab: 'plan', label: 'Plan' },
    icon: icon(
      '<rect x="4" y="5" width="16" height="3.4" rx="1.7" /><rect x="4" y="10.3" width="11" height="3.4" rx="1.7" /><rect x="4" y="15.6" width="7" height="3.4" rx="1.7" />',
    ),
  },
  {
    defId: 'cashflow',
    label: 'Cash flow',
    group: 'Cash flow',
    dsize: 4,
    dh: 112,
    prose: true,
    desc: 'Money in vs out this month, and the net.',
    title: 'Cash flow',
    source: { tab: 'plan', label: 'Plan' },
    icon: icon('<path d="M4 8h13l-3-3" /><path d="M20 16H7l3 3" />'),
  },
  {
    defId: 'calendar',
    label: 'Calendar card',
    group: 'Cash flow',
    dsize: 6,
    dh: 248,
    desc: 'Money in and out by day across the month.',
    title: 'Calendar',
    source: { tab: 'plan', label: 'Plan' },
    icon: icon(
      '<rect x="3.5" y="5" width="17" height="15.5" rx="2" /><line x1="3.5" y1="9.5" x2="20.5" y2="9.5" /><line x1="8" y1="3" x2="8" y2="6.5" /><line x1="16" y1="3" x2="16" y2="6.5" />',
    ),
  },
  {
    defId: 'recent-added-txns',
    rows: { options: [5, 10, 15, 25, 'all'], default: 10 },
    label: 'Recently added',
    group: 'Cash flow',
    dsize: 4,
    dh: 240,
    desc: 'The last transactions entered into the ledger — newest INSERTED first, not newest dated.',
    title: 'Recently added',
    source: { tab: 'accounts', label: 'Accounts' },
    icon: icon(
      '<line x1="4" y1="7" x2="14" y2="7" /><line x1="4" y1="12" x2="14" y2="12" /><line x1="4" y1="17" x2="10" y2="17" /><circle cx="18" cy="17" r="3.2" /><line x1="18" y1="15.6" x2="18" y2="18.4" /><line x1="16.6" y1="17" x2="19.4" y2="17" />',
    ),
  },
  {
    defId: 'recent-txns',
    rows: { options: [5, 10, 15, 25, 'all'], default: 10 },
    label: 'Recent transactions',
    group: 'Cash flow',
    dsize: 4,
    dh: 240,
    desc: 'The most recent transactions by date.',
    title: 'Recent transactions',
    source: { tab: 'accounts', label: 'Accounts' },
    icon: icon(
      '<path d="M6 3h12v18l-3-2-3 2-3-2-3 2V3z" /><line x1="9" y1="8" x2="15" y2="8" /><line x1="9" y1="12" x2="15" y2="12" />',
    ),
  },
  {
    defId: 'category-txns',
    rows: { options: [5, 10, 15, 25, 'all'], default: 10 },
    label: 'Category transactions',
    group: 'Cash flow',
    dsize: 4,
    dh: 240,
    desc: 'Recent transactions in the categories you pick — subscriptions, groceries, anything.',
    title: 'Category transactions',
    source: { tab: 'accounts', label: 'Accounts' },
    settings: [{ key: 'categories', type: 'categoryIds', label: 'Categories', default: [] }],
    icon: icon(
      '<path d="M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8z" /><circle cx="7.5" cy="7.5" r="1.5" />',
    ),
  },
  {
    // Category transactions with a home: titled and preset to the starter Subscriptions
    // category. Its Categories setting still takes any others.
    defId: 'subscriptions',
    rows: { options: [5, 10, 15, 25, 'all'], default: 10 },
    label: 'Subscriptions',
    group: 'Cash flow',
    dsize: 4,
    dh: 240,
    desc: 'Recent charges in your Subscriptions category — add more categories in its settings.',
    title: 'Subscriptions',
    source: { tab: 'accounts', label: 'Accounts' },
    settings: [
      {
        key: 'categories',
        type: 'categoryIds',
        label: 'Categories',
        default: [STARTER_SUBSCRIPTIONS_CATEGORY],
        fallbackLabel: 'Subscriptions',
      },
    ],
    icon: icon(
      '<path d="M4 4v5h5" /><path d="M20 20v-5h-5" /><path d="M5 15a8 8 0 0 1 14-5" /><path d="M19 9a8 8 0 0 1-14 5" />',
    ),
  },
  // ── Investing ──────────────────────────────────────────────────────────────
  {
    defId: 'movers',
    rows: { options: [3, 5, 10, 'all'], default: 'all' },
    label: "Today's movers",
    group: 'Investing',
    dsize: 6,
    dh: 168,
    desc: 'Biggest position moves today (also on Investments).',
    title: "Today's movers",
    source: { tab: 'investments', label: 'Investments' },
    icon: icon(
      '<path d="M4 15l4-4 3 3 5-6" /><path d="M13 8h3v3" /><line x1="4" y1="20" x2="20" y2="20" />',
    ),
  },
  {
    defId: 'fx',
    label: 'FX rates',
    group: 'Investing',
    dsize: 4,
    dh: 112,
    desc: "Currencies behind your foreign holdings, with today's move.",
    title: 'FX rates',
    source: { tab: 'investments', label: 'Investments' },
    settings: [{ key: 'pairs', type: 'pairs', label: 'Watch pairs', default: [] }],
    icon: icon(
      '<circle cx="12" cy="12" r="9" /><path d="M3.5 9h17M3.5 15h17" /><path d="M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" />',
    ),
  },
  {
    defId: 'dividends',
    rows: { options: [3, 5, 10, 'all'], default: 'all' },
    label: 'Dividends',
    group: 'Investing',
    dsize: 6,
    dh: 140,
    desc: "Upcoming payouts and what you've received this year.",
    title: 'Dividends',
    source: { tab: 'investments', label: 'Investments' },
    icon: icon(
      '<ellipse cx="12" cy="6.5" rx="7" ry="3" /><path d="M5 6.5v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6" /><path d="M5 12.5v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6" />',
    ),
  },
  {
    defId: 'rep-treemap',
    label: 'Holdings map',
    group: 'Investing',
    dsize: 6,
    dh: 260,
    desc: "Treemap heatmap of holdings — size = position value, color = today's move.",
    title: 'Holdings map',
    source: { tab: 'investments', label: 'Investments' },
    settings: [
      {
        key: 'group',
        type: 'segment',
        label: 'Group by',
        options: ['Sector', 'Asset type', 'Account', 'None'],
        default: 'Sector',
      },
    ],
    icon: icon(
      '<rect x="3.5" y="3.5" width="10" height="10" rx="1.5" /><rect x="15.5" y="3.5" width="5" height="5" rx="1.5" /><rect x="15.5" y="10.5" width="5" height="10" rx="1.5" /><rect x="3.5" y="15.5" width="10" height="5" rx="1.5" />',
    ),
  },
  {
    defId: 'inv-drift',
    label: 'Allocation drift',
    group: 'Investing',
    dsize: 4,
    dh: 240,
    desc: 'Actual vs target asset mix, with rebalance nudge.',
    title: 'Allocation drift',
    source: { tab: 'investments', label: 'Investments' },
    icon: icon(
      '<line x1="12" y1="4" x2="12" y2="20" /><rect x="6" y="7" width="10" height="3.2" rx="1.6" /><rect x="8" y="14" width="8" height="3.2" rx="1.6" />',
    ),
  },
  {
    defId: 'inv-perf',
    label: 'Performance vs benchmark',
    group: 'Investing',
    dsize: 6,
    dh: 260,
    desc: 'Your return vs an index over the last 12 months.',
    title: 'Performance',
    source: { tab: 'investments', label: 'Investments' },
    settings: [
      {
        key: 'range',
        type: 'segment',
        label: 'Range',
        options: ['3M', '1Y', 'ALL'],
        default: '1Y',
      },
    ],
    icon: icon('<path d="M3 17l5-6 4 3 8-9" /><path d="M3 21l5-3 4 1 8-5" opacity=".5" />'),
  },
  {
    defId: 'inv-gains',
    rows: { options: [5, 10, 15, 'all'], default: 'all' },
    label: 'Unrealized gains',
    group: 'Investing',
    dsize: 4,
    dh: 240,
    desc: 'Market value vs cost basis and unrealized P/L per holding.',
    title: 'Unrealized gains',
    source: { tab: 'investments', label: 'Investments' },
    icon: icon('<path d="M4 15l4-4 3 3 5-6" /><path d="M13 8h3v3" />'),
  },
  {
    defId: 'inv-divfc',
    label: 'Dividend forecast',
    group: 'Investing',
    dsize: 6,
    dh: 220,
    desc: 'Expected dividend income by month over the next year.',
    title: 'Dividend forecast',
    source: { tab: 'investments', label: 'Investments' },
    icon: icon(
      '<rect x="4" y="12" width="3" height="8" rx="1" /><rect x="10.5" y="8" width="3" height="12" rx="1" /><rect x="17" y="5" width="3" height="15" rx="1" />',
    ),
  },
  // ── Planning ───────────────────────────────────────────────────────────────
  {
    defId: 'spending-pace',
    label: 'Spending pace',
    group: 'Planning',
    dsize: 6,
    dh: 320,
    desc: 'Category spending pace vs budget for the current month.',
    title: 'Spending pace',
    source: { tab: 'plan', label: 'Plan' },
    settings: [
      {
        key: 'categories',
        type: 'categories',
        label: 'Categories to display',
        default: [],
      },
    ],
    icon: icon(
      '<rect x="3" y="4" width="18" height="16" rx="2" /><path d="M7 8h10" /><path d="M7 12h10" /><path d="M7 16h5" />',
    ),
  },
  {
    defId: 'goals',
    rows: { options: [3, 5, 10, 'all'], default: 'all' },
    label: 'Goals',
    group: 'Planning',
    dsize: 4,
    dh: 168,
    desc: 'Progress toward savings targets from your Plan.',
    title: 'Goals',
    source: { tab: 'plan', label: 'Plan' },
    icon: icon(
      '<circle cx="12" cy="12" r="8" /><circle cx="12" cy="12" r="4" /><circle cx="12" cy="12" r=".6" fill="currentColor" />',
    ),
  },
  {
    defId: 'crossover',
    label: 'Crossover point',
    group: 'Planning',
    dsize: 4,
    dh: 190,
    desc: 'When passive income is projected to cover your spending.',
    title: 'Crossover point',
    source: { tab: 'plan', label: 'Plan' },
    icon: icon('<path d="M3 18l7-7" /><path d="M3 6l18 12" />'),
  },
  {
    defId: 'plan-fire',
    label: 'FIRE projection',
    group: 'Planning',
    dsize: 6,
    dh: 280,
    desc: 'Compact path to financial independence — net worth vs FIRE number.',
    title: 'FIRE projection',
    source: { tab: 'plan', label: 'Outlook' },
    icon: icon('<path d="M12 3s5 4 5 9a5 5 0 0 1-10 0c0-1.6.6-3 1.4-4.2C9 9.5 12 8 12 3z" />'),
  },
  {
    defId: 'plan-runway',
    label: 'Cash-flow runway',
    group: 'Planning',
    dsize: 6,
    dh: 280,
    desc: 'Compact runway — how long cash lasts if income stopped.',
    title: 'Cash-flow runway',
    source: { tab: 'plan', label: 'Outlook' },
    icon: icon('<path d="M3 12h13l-3-3M3 12l3 3" /><line x1="20" y1="4" x2="20" y2="20" />'),
  },
  // ── Reports (chart-forward, YNAB Reflect / Monarch-style) ──────────────────
  {
    defId: 'rep-spendcat',
    rows: { options: [5, 10, 15, 'all'], default: 'all' },
    label: 'Spending by category',
    group: 'Reports',
    dsize: 4,
    dh: 300,
    desc: 'Where money goes — this month or any period, filtered by tag — donut, bar, or list.',
    title: 'Spending by category',
    shape: 'breakdown',
    source: { tab: 'plan', label: 'Plan' },
    settings: [periodSetting(TOTAL_PERIOD_OPTIONS, 'This month'), CATEGORIES_SETTING, TAGS_SETTING],
    icon: icon('<path d="M21 12A9 9 0 1 1 12 3" /><path d="M12 3a9 9 0 0 1 9 9h-9z" />'),
  },
  {
    defId: 'rep-trends',
    label: 'Spending trends',
    group: 'Reports',
    dsize: 6,
    dh: 240,
    desc: 'Monthly spend stacked by category over 3 to 12 months, against your average.',
    title: 'Spending trends',
    source: { tab: 'plan', label: 'Plan' },
    settings: [periodSetting(SERIES_PERIOD_OPTIONS, '6 months'), CATEGORIES_SETTING, TAGS_SETTING],
    icon: icon(
      '<rect x="4" y="11" width="3.5" height="9" rx="1" /><rect x="10.2" y="6" width="3.5" height="14" rx="1" /><rect x="16.5" y="9" width="3.5" height="11" rx="1" />',
    ),
  },
  {
    defId: 'rep-incexp',
    label: 'Income vs expense (chart)',
    group: 'Reports',
    dsize: 6,
    dh: 240,
    desc: 'Money in vs out, month by month — totals, or stacked by category.',
    title: 'Income vs expense',
    source: { tab: 'plan', label: 'Plan' },
    settings: [
      periodSetting(SERIES_PERIOD_OPTIONS, '6 months'),
      CATEGORIES_SETTING,
      TAGS_SETTING,
      {
        key: 'detail',
        type: 'segment',
        label: 'Detail',
        options: ['Totals', 'By category'],
        default: 'Totals',
      },
    ],
    icon: icon(
      '<path d="M6 20V9" /><path d="M11 20V4" /><path d="M16 20v-7" /><path d="M21 20V8" />',
    ),
  },
  {
    defId: 'rep-table',
    label: 'Income & expense table',
    group: 'Reports',
    dsize: 8,
    dh: 340,
    desc: 'Hierarchical income/spending breakdown with this-month, average, and total columns.',
    title: 'Income & expense table',
    source: { tab: 'plan', label: 'Plan' },
    settings: [periodSetting(SERIES_PERIOD_OPTIONS, '6 months'), CATEGORIES_SETTING, TAGS_SETTING],
    icon: icon(
      '<rect x="3.5" y="4.5" width="17" height="15" rx="2" /><line x1="3.5" y1="9" x2="20.5" y2="9" /><line x1="9" y1="9" x2="9" y2="19.5" />',
    ),
  },
  {
    defId: 'rep-savings',
    label: 'Savings rate',
    group: 'Reports',
    dsize: 3,
    dh: 188,
    desc: 'Share of income saved this month or over a period, as a gauge.',
    title: 'Savings rate',
    source: { tab: 'plan', label: 'Plan' },
    // This month by default, sent as a period: the server's own default is the last complete
    // month, which read as "Oct" in November.
    settings: [periodSetting(TOTAL_PERIOD_OPTIONS, 'This month', true)],
    icon: icon('<path d="M4 14a8 8 0 0 1 16 0" /><path d="M12 14l4-3" />'),
  },
  {
    defId: 'rep-merchants',
    rows: { options: [5, 10, 15, 25, 'all'], default: 10 },
    label: 'Top merchants',
    group: 'Reports',
    dsize: 4,
    dh: 240,
    desc: 'Where you spent the most this month or over a period, ranked — list, bar, or pie.',
    title: 'Top merchants',
    shape: 'breakdown',
    source: { tab: 'accounts', label: 'Accounts' },
    settings: [periodSetting(TOTAL_PERIOD_OPTIONS, 'This month'), CATEGORIES_SETTING, TAGS_SETTING],
    icon: icon(
      '<path d="M4 7h13l-1.5 8.5a2 2 0 0 1-2 1.5H7.5a2 2 0 0 1-2-1.5L4 7z" /><path d="M4 7L3 3" /><circle cx="8" cy="20" r="1" /><circle cx="15" cy="20" r="1" />',
    ),
  },
  {
    defId: 'rep-sankey',
    label: 'Money flow',
    group: 'Reports',
    dsize: 6,
    dh: 400,
    desc: 'Where income comes from and where it goes, over any period — click a group for its categories.',
    title: 'Money flow',
    source: { tab: 'plan', label: 'Plan' },
    settings: [periodSetting(TOTAL_PERIOD_OPTIONS, 'Last month'), CATEGORIES_SETTING, TAGS_SETTING],
    icon: icon(
      '<path d="M3 6c6 0 6 5 12 5s6-5 6-5" /><path d="M3 12c6 0 6 6 12 6" /><path d="M3 18h6" />',
    ),
  },
  // ── Content ────────────────────────────────────────────────────────────────
  {
    defId: 'text',
    label: 'Text widget',
    group: 'Content',
    dsize: 4,
    dh: 128,
    prose: true,
    desc: 'A Markdown note — headings, bold, lists, links.',
    title: 'Text',
    icon: icon(
      '<line x1="5" y1="7" x2="19" y2="7" /><line x1="5" y1="12" x2="19" y2="12" /><line x1="5" y1="17" x2="13" y2="17" />',
    ),
  },
  {
    defId: 'image',
    label: 'Image',
    group: 'Content',
    dsize: 4,
    dh: 196,
    desc: 'A picture — your goal, a chart screenshot, a family photo.',
    title: 'Image',
    icon: icon(
      '<rect x="3.5" y="4.5" width="17" height="15" rx="2" /><circle cx="9" cy="10" r="1.6" /><path d="M3.5 17l5-5 3.5 3.5L16 11l4.5 4.5" />',
    ),
  },
  {
    defId: 'embed',
    label: 'Embed',
    group: 'Content',
    dsize: 6,
    dh: 252,
    desc: 'An external page — a sheet, a TradingView chart, a doc.',
    title: 'Embed',
    settings: [{ key: 'url', type: 'url', label: 'Source URL', default: '' }],
    icon: icon(
      '<rect x="3" y="4" width="18" height="16" rx="2" /><path d="M3 8.5h18" /><path d="M9.5 12.5l-2.5 2.5 2.5 2.5M14.5 12.5l2.5 2.5-2.5 2.5" />',
    ),
  },
  // ── Lucius-generated (added via "Build with Lucius", not the normal picker) ─
  {
    defId: 'lux-coffee',
    label: 'Coffee • weekly spend',
    group: 'Content',
    dsize: 6,
    dh: 168,
    prose: true,
    lucius: true,
    desc: 'Custom widget built by Lucius.',
    title: 'Coffee • weekly spend',
  },
  {
    defId: 'lux-html',
    label: 'Lucius custom',
    group: 'Content',
    dsize: 6,
    dh: 240,
    lucius: true,
    desc: 'Free-form widget — a Lucius-generated artifact pinned to your dashboard.',
    title: 'Lucius custom',
  },
  // ── Tracking / Budgeting (hidden from the picker — the public demo layout places them) ──
  {
    defId: 'ins-debt',
    rows: { options: [5, 10, 'all'], default: 'all' },
    label: 'Debt paydown',
    group: 'Tracking',
    dsize: 6,
    dh: 240,
    hidden: true,
    desc: 'Projected payoff curve and balances by liability.',
    title: 'Debt paydown',
    source: { tab: 'accounts', label: 'Accounts' },
  },
  {
    defId: 'bud-vsactual',
    rows: { options: [5, 10, 15, 'all'], default: 10 },
    label: 'Budget vs actual',
    group: 'Budgeting',
    dsize: 4,
    dh: 300,
    hidden: true,
    desc: 'Category budgets with spent / budgeted progress bars.',
    title: 'Budget vs actual',
    source: { tab: 'plan', label: 'Plan' },
  },
]

export function findDefinition(defId: string): WidgetDefinition | undefined {
  return WIDGET_DEFINITIONS.find((d) => d.defId === defId)
}

/** Defs offered by the normal Add-widget picker — Lucius-generated and hidden
 * (Tracking/Budgeting demo) rows are excluded. */
export const PICKER_DEFINITIONS: WidgetDefinition[] = WIDGET_DEFINITIONS.filter(
  (d) => !d.lucius && !d.hidden,
)

// ── Preset views (seeded on first run) ──────────────────────────────────────
// Each line below is one row of 12 columns. Lucius's daily briefing is not seeded: a new
// book has little to brief on, and each note is a model call. It stays in Add widget.
export const PRESET_VIEWS: Array<{ name: string; widgets: PresetWidget[] }> = [
  {
    name: 'Overview',
    widgets: [
      'hero',
      'attention',
      { defId: 'upcoming', w: 6 },
      { defId: 'calendar', w: 6 },
      { defId: 'cashflow', w: 4 },
      { defId: 'recent-added-txns', w: 4 },
      { defId: 'subscriptions', w: 4 },
      { defId: 'alloc', w: 6 },
      { defId: 'venues', w: 6 },
    ],
  },
  {
    name: 'Reports',
    widgets: [
      { defId: 'rep-spendcat', w: 4 },
      { defId: 'rep-merchants', w: 4 },
      { defId: 'rep-savings', w: 4 },
      { defId: 'rep-trends', w: 6 },
      { defId: 'rep-incexp', w: 6 },
      { defId: 'rep-sankey', w: 12 },
    ],
  },
]
