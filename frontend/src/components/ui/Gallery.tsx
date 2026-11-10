import { useState } from 'react'
import type { ReactNode } from 'react'
import { TopDailyMovers } from '../shared/TopDailyMovers'
import {
  Badge,
  Button,
  Checkbox,
  CheckboxGroup,
  Cluster,
  Container,
  EmptyState,
  ErrorState,
  FormField,
  Grid,
  Loading,
  Meter,
  Modal,
  MoneyField,
  NumberField,
  PageHeader,
  PageTabs,
  Panel,
  RadioGroup,
  SegmentedControl,
  Select,
  Stack,
  SummaryBar,
  Tag,
  TagInput,
  Text,
  TextArea,
  List,
  SwipeActions,
  TextField,
} from './index'
import type { ButtonVariant, ButtonSize, CheckboxGroupOption, TextSize, TextTone } from './index'
import { Plus } from '../../ui/icons'

// Dev-only gallery: renders every primitive × every variant/state on one
// scrollable screen so the whole vocabulary can be reviewed in a single
// screenshot. Reachable at /?gallery=1 (gated in main.tsx); not in the nav.

const COLOR_TOKENS = [
  'bg',
  'surface',
  'surface2',
  'line',
  'ink',
  'muted',
  'faint',
  'good',
  'bad',
  'warn',
  'ibkr',
  'accent',
  'locked',
  'onwai',
  'apt',
  'recv',
  'cash-c',
  'crypto',
]
const SPACE_STEPS = [1, 2, 3, 4, 5, 6, 7, 8]
const TYPE_STEPS: [string, string][] = [
  ['2xs', '10'],
  ['xs', '11'],
  ['sm', '12'],
  ['base', '13'],
  ['md', '14'],
  ['lg', '16'],
  ['xl', '20'],
  ['2xl', '22'],
]
const RADIUS_STEPS = ['xs', 'sm', 'md', 'lg', 'xl', '2xl', 'pill']
const TEXT_SIZES: TextSize[] = ['2xs', 'xs', 'sm', 'base', 'md', 'lg', 'xl', '2xl']
const TEXT_TONES: TextTone[] = ['ink', 'muted', 'faint', 'good', 'bad', 'warn']

// A few demo cards so <Grid>'s auto-fit STRETCH-to-fill is visible (vs the old
// auto-fill void). Card action rows pin to the bottom via margin-top:auto.
const DEMO_CARDS = ['monthly-close', 'budget-review', 'subscription-audit', 'tax-prep', 'fx-sweep']

function Section({
  title,
  derives,
  children,
}: {
  title: string
  derives?: string
  children: ReactNode
}) {
  return (
    <section className="ui-gallery__section">
      <h2>
        {title}
        {derives && <span className="ui-gallery__derives"> — {derives}</span>}
      </h2>
      {children}
    </section>
  )
}

const ACCOUNT_OPTIONS: CheckboxGroupOption[] = [
  { value: 'a1', label: 'DBS Multiplier Savings', description: 'SGD • bank' },
  { value: 'a2', label: 'OCBC 360', description: 'SGD • bank' },
  {
    value: 'a3',
    label: 'Standard Chartered Bonus Saver Account (joint)',
    description: 'SGD • bank',
  },
  { value: 'a4', label: 'Interactive Brokers — Cash USD', description: 'USD • brokerage' },
  { value: 'a5', label: 'Binance Spot Wallet', description: 'USD • crypto exchange' },
  {
    value: 'a6',
    label: 'Revolut Multi-currency Pocket (EUR/GBP/USD)',
    description: 'EUR • ewallet',
  },
]

const MANY_OPTIONS: CheckboxGroupOption[] = Array.from({ length: 30 }, (_, i) => ({
  value: `m${i}`,
  label: `Account number ${i + 1} with a deliberately long descriptive name`,
  description: 'SGD • bank',
}))

export function Gallery() {
  const [modalSm, setModalSm] = useState(false)
  const [modalMd, setModalMd] = useState(false)
  const [modalLg, setModalLg] = useState(false)
  const [modalXl, setModalXl] = useState(false)
  const [seg, setSeg] = useState<'1m' | '3m' | 'ytd' | '1y'>('3m')
  const [seg2, setSeg2] = useState<'on' | 'off'>('on')
  const [pageSec, setPageSec] = useState<'overview' | 'details' | 'history' | 'export'>('overview')
  const [linked, setLinked] = useState<string[]>(['a1', 'a4'])
  const [linkedMany, setLinkedMany] = useState<string[]>([])
  const [radioValue, setRadioValue] = useState('balanced')
  const [tags, setTags] = useState<string[]>(['groceries', 'recurring'])
  const [noTags, setNoTags] = useState<string[]>([])
  const [checked, setChecked] = useState(true)
  const [money, setMoney] = useState(5000)
  const [worth, setWorth] = useState<number | null>(14800)
  const [paid, setPaid] = useState<number | null>(null)

  const variants: ButtonVariant[] = ['primary', 'secondary', 'danger', 'ghost']
  const sizes: ButtonSize[] = ['md', 'sm']

  return (
    <div className="ui-gallery">
      <h1>UI Kit Gallery</h1>
      <p className="ui-gallery__derives">
        Every primitive in <code>src/components/ui/</code>. Toggle the chat panel to verify modal
        chat-clearance.
      </p>

      <Section
        title="Layout — Container / Grid / Stack / Cluster / PageHeader"
        derives="new — phase-3 §2"
      >
        <Container size="wide" className="ui-gallery__bordered">
          <Stack gap="--space-5">
            <PageHeader
              title="Layout primitives"
              subtitle="Container caps + centers • Grid stretches with auto-fit • Stack = vertical rhythm • Cluster wraps"
              actions={
                <>
                  <Button size="sm">Secondary</Button>
                  <Button size="sm" variant="primary">
                    Primary
                  </Button>
                </>
              }
            />
            <Text as="p" size="sm" tone="muted">
              Grid (min=240) — the cards STRETCH to fill the row (auto-fit), no ragged void; resize
              the window to watch them reflow + stay full-width.
            </Text>
            <Grid min={240}>
              {DEMO_CARDS.map((name) => (
                <Panel key={name} title={name} className="ui-gallery__democard">
                  <Text as="p" size="sm" tone="muted">
                    A playbook card. The action row pins to the bottom so every card is the same
                    height.
                  </Text>
                  <div className="ui-gallery__democard-actions">
                    <Button size="sm">Run</Button>
                  </div>
                </Panel>
              ))}
            </Grid>
            <Cluster gap="--space-3">
              <Badge tone="neutral">Cluster</Badge>
              <Tag>wraps</Tag>
              <Tag>onto</Tag>
              <Tag>multiple</Tag>
              <Tag>rows</Tag>
              <Button size="sm">when narrow</Button>
            </Cluster>
          </Stack>
        </Container>
      </Section>

      <Section title="List" derives="new — numbered steps, short enumerations">
        <List ordered>
          <li>
            Shortcuts → <strong>Automation</strong> → <strong>+</strong>
          </li>
          <li>Pick your cards.</li>
        </List>
        <List>
          <li>A bulleted item</li>
          <li>Another one</li>
        </List>
      </Section>

      <Section title="SwipeActions" derives="new — swipe a row left for its actions (touch only)">
        <SwipeActions
          actions={[
            { label: 'Edit', ariaLabel: 'Edit sample row', onClick: () => {} },
            { label: 'Delete', ariaLabel: 'Delete sample row', onClick: () => {}, tone: 'danger' },
          ]}
        >
          <Text>On a touch screen, swipe this row left.</Text>
        </SwipeActions>
      </Section>

      <Section title="Typography — <Text>" derives="new — phase-3 §3">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
          {TEXT_SIZES.map((size) => (
            <div key={size} className="ui-gallery__row">
              <span className="ui-gallery__label">size={size}</span>
              <Text size={size}>The quick brown fox • 0123</Text>
            </div>
          ))}
        </div>
        <div className="ui-gallery__row">
          <span className="ui-gallery__label">tones</span>
          {TEXT_TONES.map((tone) => (
            <Text key={tone} tone={tone} weight={600}>
              {tone}
            </Text>
          ))}
        </div>
        <div className="ui-gallery__row">
          <span className="ui-gallery__label">weights</span>
          <Text weight={400}>400</Text>
          <Text weight={500}>500</Text>
          <Text weight={600}>600</Text>
          <Text weight={700}>700</Text>
        </div>
        <div className="ui-gallery__row">
          <span className="ui-gallery__label">headings</span>
          <Text as="h1" size="2xl" weight={700}>
            h1 / 2xl
          </Text>
          <Text as="h2" size="xl" weight={600}>
            h2 / xl
          </Text>
          <Text as="h3" size="lg" weight={600}>
            h3 / lg
          </Text>
        </div>
        <div style={{ maxWidth: 220 }}>
          <span className="ui-gallery__label">truncate (+ title)</span>
          <Text truncate title="Meridian Bank World Card — joint account ending 7734">
            Meridian Bank World Card — joint account ending 7734
          </Text>
        </div>
      </Section>

      <Section title="Tokens — colors">
        <div className="ui-gallery__grid">
          {COLOR_TOKENS.map((t) => (
            <div key={t} className="ui-gallery__swatch">
              <i style={{ background: `var(--${t})` }} />
              <span>--{t}</span>
            </div>
          ))}
        </div>
      </Section>

      <Section title="Tokens — spacing (--space-1…8)">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
          {SPACE_STEPS.map((n) => (
            <div key={n} className="ui-gallery__row">
              <span className="ui-gallery__label">--space-{n}</span>
              <div className="ui-gallery__scalebar" style={{ width: `var(--space-${n})` }} />
            </div>
          ))}
        </div>
      </Section>

      <Section title="Tokens — type (--text-*)">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
          {TYPE_STEPS.map(([name, px]) => (
            <div key={name} className="ui-gallery__row">
              <span className="ui-gallery__label">
                --text-{name} ({px}px)
              </span>
              <span style={{ fontSize: `var(--text-${name})` }}>The quick brown fox • 0123</span>
            </div>
          ))}
        </div>
      </Section>

      <Section title="Tokens — radius (--radius-*) + shadow">
        <div className="ui-gallery__row">
          {RADIUS_STEPS.map((r) => (
            <div
              key={r}
              className="ui-gallery__radiusbox"
              style={{ borderRadius: `var(--radius-${r})` }}
            >
              {r}
            </div>
          ))}
          <div
            className="ui-gallery__radiusbox"
            style={{ boxShadow: 'var(--shadow-modal)', borderRadius: 'var(--radius-xl)' }}
          >
            shadow
          </div>
        </div>
      </Section>

      <Section title="Button" derives=".btn-primary/.btn-secondary/.btn-danger/.btn-small">
        {sizes.map((size) => (
          <div key={size} className="ui-gallery__row">
            <span className="ui-gallery__label">size={size}</span>
            {variants.map((v) => (
              <Button key={v} variant={v} size={size}>
                {v}
              </Button>
            ))}
          </div>
        ))}
        <div className="ui-gallery__row">
          <span className="ui-gallery__label">states</span>
          <Button variant="primary" disabled>
            disabled
          </Button>
          <Button variant="primary" loading>
            loading
          </Button>
          <Button variant="secondary" loading>
            loading
          </Button>
          <Button variant="secondary" iconLeft={<Plus size={14} aria-hidden="true" />}>
            iconLeft
          </Button>
        </div>
        <div style={{ maxWidth: 320 }}>
          <Button variant="primary" fullWidth>
            fullWidth
          </Button>
        </div>
      </Section>

      <Section title="Form fields" derives=".profile-field + .form-label">
        <div className="profile-grid">
          <FormField label="Text field" htmlFor="g-text" help="A muted helper line.">
            <TextField id="g-text" placeholder="Type here…" defaultValue="Filled value" />
          </FormField>
          <FormField label="Number field" htmlFor="g-num" required>
            <NumberField id="g-num" min={0} step={100} defaultValue={1500} />
          </FormField>
          <FormField label="Money field" htmlFor="g-money">
            <MoneyField
              id="g-money"
              aria-label="Monthly retirement spending"
              value={money}
              formattedValue={`S$${money.toLocaleString('en-SG', { minimumFractionDigits: 2 })}`}
              suffix="/month"
              onValueChange={setMoney}
            />
          </FormField>
          <FormField label="Money field · prefix" htmlFor="g-money-prefix">
            <MoneyField
              id="g-money-prefix"
              allowEmpty
              prefix="S$"
              value={worth}
              formattedValue={worth == null ? '' : worth.toLocaleString('en-SG')}
              onValueChange={setWorth}
            />
          </FormField>
          <FormField
            label="Money field · allowEmpty"
            htmlFor="g-money-empty"
            help="Clearing it commits null, not an error."
          >
            <MoneyField
              id="g-money-empty"
              allowEmpty
              prefix="HK$"
              placeholder="Optional"
              value={paid}
              formattedValue={paid == null ? '' : paid.toLocaleString('en-SG')}
              onValueChange={setPaid}
            />
          </FormField>
          <FormField label="Select" htmlFor="g-sel">
            <Select
              id="g-sel"
              placeholder="Select…"
              defaultValue="food"
              options={[
                { value: 'food', label: 'Food & dining' },
                { value: 'transport', label: 'Transport' },
                { value: 'rent', label: 'Rent' },
              ]}
            />
          </FormField>
          <FormField label="Invalid + error" htmlFor="g-err" error="This field is required.">
            <TextField id="g-err" invalid defaultValue="" />
          </FormField>
          <FormField label="Disabled" htmlFor="g-dis">
            <TextField id="g-dis" disabled defaultValue="Can't touch this" />
          </FormField>
          <FormField label="Text area" htmlFor="g-ta" span="full">
            <TextArea id="g-ta" rows={3} placeholder="Notes…" />
          </FormField>
          <FormField label="Text area — mono" htmlFor="g-ta-mono" span="full">
            <TextArea
              id="g-ta-mono"
              mono
              readOnly
              rows={2}
              value="https://box.example.com|tok_1234567890abcdef"
            />
          </FormField>
        </div>
        <div className="ui-gallery__row" style={{ flexDirection: 'column', alignItems: 'stretch' }}>
          <Checkbox
            id="g-cb"
            label="Single checkbox with a description"
            description="The box aligns to the top of the label, not the middle of the wrapped text."
            checked={checked}
            onChange={(e) => setChecked(e.target.checked)}
          />
        </div>
      </Section>

      <Section title="CheckboxGroup" derives="fixes the Targets linked-accounts grid">
        <FormField label="Linked accounts (6 — with long names)" htmlFor="g-cg">
          <CheckboxGroup
            options={ACCOUNT_OPTIONS}
            value={linked}
            onChange={setLinked}
            selectAll
            ariaLabel="Linked accounts"
            emptyText="No accounts yet"
          />
        </FormField>
        <FormField label="Many accounts (30 — proves scroll, no clip)">
          <CheckboxGroup
            options={MANY_OPTIONS}
            value={linkedMany}
            onChange={setLinkedMany}
            selectAll
            maxHeight={180}
            ariaLabel="Many accounts"
          />
        </FormField>
        <FormField label="Empty group">
          <CheckboxGroup options={[]} value={[]} onChange={() => {}} emptyText="No accounts yet" />
        </FormField>
      </Section>

      <Section title="RadioGroup" derives="exclusive choice w/ description — routing profiles">
        <RadioGroup
          name="g-radio"
          ariaLabel="Routing profile"
          value={radioValue}
          onChange={setRadioValue}
          options={[
            {
              value: 'cheapest',
              label: 'Cheapest',
              description: 'Always the lowest-cost model — even a slow local one.',
            },
            {
              value: 'balanced',
              label: 'Balanced',
              badge: <Badge tone="neutral">Default</Badge>,
              description: 'Weighs cost and speed together.',
            },
            {
              value: 'fastest',
              label: 'Fastest',
              description: 'Always the quickest reply.',
              disabled: true,
            },
          ]}
        />
      </Section>

      <Section title="TagInput / Tag" derives=".txn-tag-input / .acct-txn-tag">
        <FormField label="Tags (filled)" htmlFor="g-tags">
          <TagInput id="g-tags" value={tags} onChange={setTags} placeholder="Add a tag…" />
        </FormField>
        <FormField label="Tags (empty)">
          <TagInput value={noTags} onChange={setNoTags} placeholder="Add a tag…" />
        </FormField>
        <div className="ui-gallery__row">
          <Tag>accent</Tag>
          <Tag tone="muted">muted</Tag>
          <Tag onRemove={() => {}}>removable</Tag>
        </div>
      </Section>

      <Section
        title="Modal — Radix behavior + responsive sheet"
        derives="@radix-ui/react-dialog + .modal-overlay + .ui-modal-card"
      >
        <Text as="p" size="sm" tone="muted">
          Radix owns focus, Escape, portals, scroll lock, and nested dialogs. Sizes: sm 380 • md 520
          • lg 720 • xl 960 on desktop; below 640px every modal becomes the same bottom sheet.
        </Text>
        <div className="ui-gallery__row">
          <Button onClick={() => setModalSm(true)}>Open sm</Button>
          <Button onClick={() => setModalMd(true)}>Open md</Button>
          <Button onClick={() => setModalLg(true)}>Open lg</Button>
          <Button onClick={() => setModalXl(true)}>Open xl</Button>
        </div>
        <Modal
          open={modalSm}
          onClose={() => setModalSm(false)}
          size="sm"
          title="Small modal"
          footer={
            <>
              <Button onClick={() => setModalSm(false)}>Cancel</Button>
              <Button variant="primary" onClick={() => setModalSm(false)}>
                Confirm
              </Button>
            </>
          }
        >
          A compact confirm dialog. Press Escape or click the backdrop to close.
        </Modal>
        <Modal
          open={modalMd}
          onClose={() => setModalMd(false)}
          size="md"
          title="Medium modal (default)"
          footer={
            <>
              <Button onClick={() => setModalMd(false)}>Cancel</Button>
              <Button variant="primary" onClick={() => setModalMd(false)}>
                Save
              </Button>
            </>
          }
        >
          <FormField label="A field inside a modal" htmlFor="g-modal-field">
            <TextField id="g-modal-field" placeholder="…" />
          </FormField>
        </Modal>
        <Modal
          open={modalLg}
          onClose={() => setModalLg(false)}
          size="lg"
          title="Large modal — 2-column forms"
          footer={
            <>
              <Button onClick={() => setModalLg(false)}>Close</Button>
              <Button variant="primary" onClick={() => setModalLg(false)}>
                Done
              </Button>
            </>
          }
        >
          The lg size (720) is the default for 2-column forms (e.g. edit-transaction). With the chat
          open it must clear the fixed panel via <code>--chat-width</code>.
        </Modal>
        <Modal
          open={modalXl}
          onClose={() => setModalXl(false)}
          size="xl"
          title="Extra-large modal — wide/tabular editors"
          footer={
            <>
              <Button onClick={() => setModalXl(false)}>Close</Button>
              <Button variant="primary" onClick={() => setModalXl(false)}>
                Done
              </Button>
            </>
          }
        >
          The xl size (960) is for wide tabular editors — a bulk-import preview or a lot editor.
        </Modal>
      </Section>

      <Section title="Panel" derives=".panel">
        <Panel title="Panel with title + actions" actions={<Button size="sm">Action</Button>}>
          Panel body content.
        </Panel>
        <Panel>Panel with no title.</Panel>
      </Section>

      <Section title="SummaryBar" derives=".sumbar / .sumcard">
        <SummaryBar
          cards={[
            { label: 'Net worth', value: '$482,910' },
            { label: 'Day P&L', value: '+$1,204', tone: 'pos' },
            { label: 'Drawdown', value: '-$3,180', tone: 'neg' },
            { label: 'Cash runway', value: '4.2 mo', tone: 'warn' },
          ]}
        />
      </Section>

      <Section title="SegmentedControl" derives=".intel-wl-tabs / .intel-wl-tab">
        <Text as="p" size="sm" tone="muted">
          Compact inline filter — 2–5 short options inside a panel or toolbar. Use{' '}
          <code>PageTabs</code> (below) for page-level section navigation instead.
        </Text>
        <div className="ui-gallery__row">
          <SegmentedControl
            ariaLabel="Period"
            value={seg}
            onChange={setSeg}
            options={[
              { value: '1m', label: '1M' },
              { value: '3m', label: '3M' },
              { value: 'ytd', label: 'YTD' },
              { value: '1y', label: '1Y' },
            ]}
          />
          <SegmentedControl
            ariaLabel="Rollover"
            value={seg2}
            onChange={setSeg2}
            options={[
              { value: 'on', label: 'On' },
              { value: 'off', label: 'Off' },
            ]}
          />
        </div>
      </Section>

      <Section title="PageTabs" derives=".ui-page-tabs / .ui-page-tabs__tab">
        <Text as="p" size="sm" tone="muted">
          Underline tab bar for primary page-section navigation — below the page title, above all
          panels. Full-width spine; active indicator is a 2px accent underline. <strong>Not</strong>{' '}
          SegmentedControl (which stays for compact inline 2–5-option filters).
        </Text>
        <PageTabs
          tabs={[
            { key: 'overview', label: 'Overview' },
            { key: 'details', label: 'Details', badge: 3 },
            { key: 'history', label: 'History' },
            { key: 'export', label: 'Export' },
          ]}
          value={pageSec}
          onChange={setPageSec}
          ariaLabel="Gallery section example"
        />
        <div
          role="tabpanel"
          aria-labelledby={`ui-page-tab-${pageSec}`}
          style={{
            padding: 'var(--space-4)',
            border: '1px dashed var(--line)',
            borderRadius: 'var(--radius-sm)',
            color: 'var(--muted)',
          }}
        >
          <Text size="sm">
            Active panel: <strong>{pageSec}</strong>
          </Text>
        </div>
      </Section>

      <Section title="Badge" derives=".pl-badge">
        <div className="ui-gallery__row">
          <Badge tone="pos">+12.4%</Badge>
          <Badge tone="neg">-4.1%</Badge>
          <Badge tone="neutral">Active</Badge>
          <Badge tone="muted">Draft</Badge>
          <Badge tone="warn">Review</Badge>
        </div>
      </Section>

      <Section title="Meter" derives=".ui-meter">
        <Text as="p" size="sm" tone="muted">
          Single fill, single fill + target tick (incl. the negative flag), multi-segment track, and
          ring — promoted from Overview's budget-vs-actual/movers bars, Commitments' funding
          coverage bar, and Plan's target ring.
        </Text>
        <div
          style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)', maxWidth: 320 }}
        >
          <Meter
            variant="linear"
            size="sm"
            value={42}
            tone="good"
            ariaLabel="Groceries: 42% spent"
          />
          <Meter
            variant="linear"
            size="md"
            value={68}
            target={80}
            tone="warn"
            ariaLabel="Wants: 68% of target"
          />
          <Meter
            variant="linear"
            size="sm"
            value={-15}
            tone="bad"
            target={60}
            ariaLabel="Savings rate (negative)"
          />
          <Meter
            variant="linear"
            size="lg"
            ariaLabel="Funding coverage"
            segments={[
              {
                id: 'cash',
                pct: 40,
                color: 'var(--good)',
                ariaLabel: 'Cash: $4,000',
                content: '$4K',
              },
              {
                id: 'loan',
                pct: 35,
                color: 'var(--warn)',
                ariaLabel: 'Loan: $3,500',
                content: '$3.5K',
              },
              {
                id: 'gap',
                pct: 25,
                color: 'var(--bad)',
                ariaLabel: 'Unfunded: $2,500',
                content: '$2.5K',
              },
            ]}
          />
        </div>
        <div className="ui-gallery__row">
          <Meter
            variant="ring"
            size="sm"
            value={64}
            color="var(--warn)"
            ariaLabel="Target progress: 64%"
          />
          <Meter
            variant="ring"
            size="lg"
            value={82}
            color="var(--good)"
            ariaLabel="Target progress: 82%"
          />
          <Meter variant="ring" size="lg" complete ariaLabel="Target met" />
        </div>
      </Section>

      <Section title="TopDailyMovers" derives=".top-movers / .top-movers-row / .top-movers-bar">
        <TopDailyMovers
          rows={
            [
              {
                label: 'Apple Inc.',
                symbol: 'AAPL',
                ticker: 'AAPL',
                _daily_pl: 1240,
                _daily_plpct: 2.34,
                _base: 52000,
                asset_class: 'equity',
                bucket: 'liquid',
                currency: 'USD',
                _pct: 15,
                _cost_base: 48000,
                _pl: 4000,
                _plpct: 8.3,
              },
              {
                label: 'Bitcoin',
                symbol: 'BTC',
                ticker: 'BTC',
                _daily_pl: 890,
                _daily_plpct: 1.12,
                _base: 79000,
                asset_class: 'crypto',
                bucket: 'contingent',
                currency: 'USD',
                _pct: 23,
                _cost_base: 60000,
                _pl: 19000,
                _plpct: 31.7,
              },
              {
                label: 'Microsoft Corporation',
                symbol: 'MSFT',
                ticker: 'MSFT',
                _daily_pl: -543,
                _daily_plpct: -2.59,
                _base: 20400,
                asset_class: 'equity',
                bucket: 'liquid',
                currency: 'USD',
                _pct: 6,
                _cost_base: 22000,
                _pl: -1600,
                _plpct: -7.3,
              },
              {
                label: 'Ethereum',
                symbol: 'ETH',
                ticker: 'ETH',
                _daily_pl: -210,
                _daily_plpct: -0.88,
                _base: 23800,
                asset_class: 'crypto',
                bucket: 'contingent',
                currency: 'USD',
                _pct: 7,
                _cost_base: 20000,
                _pl: 3800,
                _plpct: 19.0,
              },
            ] as any
          }
        />
      </Section>

      <Section title="States (EmptyState / Loading / ErrorState)" derives="new — no class existed">
        <Panel title="Empty (with CTA)">
          <EmptyState
            icon={<span aria-hidden>📭</span>}
            title="No transactions yet"
            body="Log spending manually, import a statement, or connect email ingestion."
            action={
              <Button variant="primary" size="sm">
                Add transaction
              </Button>
            }
          />
        </Panel>
        <Panel title="Empty (no CTA)">
          <EmptyState title="Nothing to show" body="There's no data in this range." />
        </Panel>
        <Panel title="Loading">
          <Loading label="Fetching prices…" />
        </Panel>
        <Panel title="Error (with Retry)">
          <ErrorState message="Couldn't reach the pricing service." onRetry={() => {}} />
        </Panel>
      </Section>
    </div>
  )
}
