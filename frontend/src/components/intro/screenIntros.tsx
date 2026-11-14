// What each main screen's first-visit intro says: per step, a small copy of the screen, its
// title and the two reasons it is worth using; per screen, what the Lucius button asks.
// Assets has its own (assetsIntro.tsx), because its "Set up myself" also runs a tour.
import type { ReactNode } from 'react'
import type { IntroPoint, IntroStep } from './FeatureIntro'
import type { TourStep } from './PageTour'
import {
  AccountsPreview,
  AutomationsPreview,
  BudgetPreview,
  CashFlowPreview,
  CommitmentsPreview,
  ConnectorsPreview,
  DashboardPreview,
  IntelPreview,
  InvestmentsPreview,
  OutlookPreview,
  SkillsPreview,
} from './IntroPreviews'

export type IntroScreen =
  | 'accounts'
  | 'plan'
  | 'plan-cashflow'
  | 'plan-outlook'
  | 'investments'
  | 'intel'
  | 'commitments'
  | 'dashboard'
  | 'automations'
  | 'skills'
  | 'connectors'

export interface ScreenIntroConfig {
  /** What the user calls the screen, for "Walk me through my … screen". */
  name: string
  steps: IntroStep[]
  /** The message the Lucius button opens the chat with. */
  lucius: string
  /** The Lucius button's label, when "Set up with Lucius" doesn't fit (nothing to set up). */
  luciusLabel?: string
  /** The other button's label, when "Set up myself" doesn't fit. */
  secondaryLabel?: string
  /** Where "Set up myself" points on an EMPTY screen: its own way to add the first thing. A
   *  screen with nothing to add has none, and its other button just closes ("Got it"). */
  start?: TourStep
  /** On a screen that already holds data the buttons are "Got it" and "Walk me through it";
   *  a screen with something worth pointing at then names it here instead of "Got it". */
  filled?: { label: string; start: TourStep }
}

// The picture is a small copy of the screen itself, so the title sits under it.
function screen(title: string, hero: ReactNode, points: IntroPoint[]): IntroStep {
  return { title, hero, points }
}

/** Intros the owner turned off because the screen already says what they say (launch review
 *  2026-10-15). The configs stay, so one can come back by leaving this set. */
export const HIDDEN_INTROS: ReadonlySet<IntroScreen> = new Set<IntroScreen>([
  'dashboard',
  'plan-cashflow',
])

export const SCREEN_INTROS: Record<IntroScreen, ScreenIntroConfig> = {
  accounts: {
    name: 'Accounts',
    start: {
      target: 'setup-accounts',
      title: 'Add your first account',
      body: 'A bank account, card, brokerage or loan. Then bring its transactions in.',
    },
    steps: [
      screen('Every account, in one ledger', <AccountsPreview />, [
        {
          title: 'Bring transactions in.',
          body: 'Import a statement, or let Lucius log them from your bank emails as they arrive.',
        },
        {
          title: 'Lucius keeps it tidy.',
          body: 'It sorts categories, catches duplicates and flags anything odd.',
        },
      ]),
    ],
    lucius:
      'Help me set up my accounts: ask which banks, cards, brokerages and loans I have, add ' +
      'them, then help me bring in their transactions. I can also send you screenshots or ' +
      'statements to read them from. Ask me one thing at a time.',
  },
  // Plan is three tabs; each introduces itself the first time it is opened, never all three
  // at once over a tab the user can't see yet.
  plan: {
    name: 'Budget',
    start: {
      target: 'setup-plan',
      title: 'Pick how you budget',
      body: 'Choose a method here. You can switch any time.',
    },
    steps: [
      screen('Budget your way', <BudgetPreview />, [
        {
          title: 'A quick check.',
          body: '50/30/20 splits your income into needs, wants and savings. Nothing to maintain.',
        },
        {
          title: 'Or zero-based.',
          body: 'Zero-based assigns each dollar, and you move money when a category runs over.',
        },
      ]),
    ],
    lucius:
      'Help me set up my budget: ask a few questions, pick the method that fits me (a quick ' +
      '50/30/20 check or zero-based), and set it up. Ask me one thing at a time.',
  },
  'plan-cashflow': {
    name: 'Cash Flow',
    steps: [
      screen('The weeks ahead', <CashFlowPreview />, [
        {
          title: 'Cash flow, forward.',
          body: 'Your balance day by day, with bills and paydays already on it.',
        },
        { title: 'No surprises.', body: 'Spot a low point before it happens, not after.' },
      ]),
    ],
    lucius:
      'Help me set up my cash flow: ask what I earn each month and when it lands, and my ' +
      'regular bills and when they are due, then show me the weeks ahead. Ask me one thing at a time.',
  },
  'plan-outlook': {
    name: 'Outlook',
    start: {
      target: 'setup-outlook',
      title: 'Try a what-if',
      body: 'Drag a lever, or ask Lucius and he moves them for you.',
    },
    steps: [
      screen('Will your money last?', <OutlookPreview />, [
        {
          title: 'Your outlook.',
          body: 'See when you could retire, and how likely your plan is to hold up.',
        },
        {
          title: 'Try what-ifs.',
          body: 'A new job, a child, a downturn: see the effect before it happens.',
        },
      ]),
    ],
    luciusLabel: 'Try a what-if with Lucius',
    secondaryLabel: 'Try it myself',
    lucius:
      'On my Outlook, walk me through where I stand, then show me what retiring five years ' +
      'earlier would do. Move the sliders so I can see it.',
  },
  investments: {
    name: 'Investments',
    start: {
      target: 'setup-investments',
      title: 'Add a holding',
      body: 'A stock, fund or coin, with how many you hold and where.',
    },
    steps: [
      screen('All your investments, one view', <InvestmentsPreview />, [
        {
          title: 'Every broker together.',
          body: 'Holdings, returns, dividends and options in one place.',
        },
        {
          title: 'Lucius watches your mix.',
          body: 'It flags concentration and drift before they cost you.',
        },
      ]),
    ],
    lucius:
      'Help me add my investments: ask which brokers and holdings I have, add them, then tell ' +
      'me what stands out about my mix. If it is easier, I can send you a screenshot or ' +
      'statement instead of typing. Ask me one thing at a time.',
  },
  intel: {
    name: 'Intelligence',
    start: {
      target: 'setup-intel',
      title: 'Watch a ticker',
      body: 'Add something to watch. Lucius tells you when it moves.',
    },
    steps: [
      screen('Know when something moves your money', <IntelPreview />, [
        {
          title: 'Watch what matters.',
          body: 'Tickers and topics, with alerts that tell Lucius what to do.',
        },
        { title: 'Signals, not noise.', body: 'Only the news that touches what you hold.' },
      ]),
    ],
    lucius:
      'Help me set up Intel: ask what I hold or want to follow, suggest what to watch, and ' +
      'set alerts that tell you what to do when they fire. Ask me one thing at a time.',
  },
  commitments: {
    name: 'Commitments',
    start: {
      target: 'setup-commitments',
      title: 'Add a big date',
      body: 'A purchase, a loan or a goal, with its date and amount.',
    },
    steps: [
      screen('Big dates, funded on time', <CommitmentsPreview />, [
        {
          title: 'Plan the big ones.',
          body: 'A car, a wedding, a loan, each with its date and amount.',
        },
        {
          title: 'Know how you will pay.',
          body: 'See where the money comes from, month by month.',
        },
      ]),
    ],
    lucius:
      'Help me plan my commitments: ask about big expenses, loans or goals coming up, add each ' +
      'with its date and amount, and show me how I will fund them. I can also send you a ' +
      'contract or payment schedule to read. Ask me one thing at a time.',
  },
  dashboard: {
    name: 'Dashboard',
    // Empty, there is nothing to arrange yet: Lucius sets up the accounts that fill it.
    filled: {
      label: 'Customize it',
      start: {
        target: 'setup-dashboard',
        title: 'Make it yours',
        body: 'Add, remove and arrange widgets here.',
      },
    },
    steps: [
      screen('Your money at a glance', <DashboardPreview />, [
        {
          title: 'Pick what you see.',
          body: 'Net worth, cash flow and spending, as widgets you arrange.',
        },
        { title: 'Ask about any number.', body: 'Lucius explains what changed and why.' },
      ]),
    ],
    lucius:
      'My dashboard is empty. Help me get started: walk me through adding my accounts so it ' +
      'fills in. Ask me one thing at a time.',
  },
  automations: {
    name: 'Automations',
    // Its add button leads the header; pointing at it would only state the obvious.
    secondaryLabel: 'Set up myself',
    steps: [
      screen('Lucius works while you are away', <AutomationsPreview />, [
        {
          title: 'On a schedule or a trigger.',
          body: 'Every month, when a price hits, or when a bill email lands.',
        },
        {
          title: 'You stay in control.',
          body: 'Choose what needs your OK first. Every run is a conversation you can reopen.',
        },
      ]),
    ],
    lucius:
      'Help me set up my first automation: ask what I want done on a schedule or when ' +
      'something happens, then create it. Ask me one thing at a time.',
  },
  skills: {
    name: 'Skills',
    // Its add button leads the header; pointing at it would only state the obvious.
    steps: [
      screen('Teach Lucius how you do things', <SkillsPreview />, [
        {
          title: 'Your way, every time.',
          body: 'A skill is a playbook Lucius follows step by step.',
        },
        {
          title: 'Type / to run one.',
          body: 'Or ask Lucius to save what he just did as a skill.',
        },
      ]),
    ],
    luciusLabel: 'Make one with Lucius',
    secondaryLabel: 'Write one myself',
    lucius:
      'Help me make my first skill: ask what I do often that you should do my way, then write ' +
      'it as a skill. Ask me one thing at a time.',
  },
  connectors: {
    name: 'Connectors',
    // Its add button leads the header; pointing at it would only state the obvious.
    secondaryLabel: 'Set up myself',
    steps: [
      screen('Extend what Lucius can do', <ConnectorsPreview />, [
        {
          title: 'Data, channels and tools.',
          body: 'Bank feeds, Telegram, web search, or any MCP server.',
        },
        {
          title: 'You decide what he may use.',
          body: 'Each connector has its own permissions.',
        },
      ]),
    ],
    lucius:
      'Help me choose connectors: ask what I want you to be able to do, like pull my bank ' +
      'transactions, message me on Telegram or search the web, then set up the ones that fit. ' +
      'Ask me one thing at a time.',
  },
}
