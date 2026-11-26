// The picture at the top of a setup nudge: two phones in a coloured scene, one per case — a lock
// screen of Lucius notifications for Telegram, an email turning into a booked row for Gmail, a
// sunrise briefing, a week of bars for reports, an inbox boiled down for the digest, and a faded,
// failing version of each channel when it breaks. SAMPLE content only — never the book.
import type { ReactNode } from 'react'
import {
  AlertTriangle,
  CalendarRange,
  Check,
  LuciusMark,
  Mail,
  Send,
  Sunrise,
} from '../../ui/icons'
import { Donut, Funnel, Progress, Sparkline } from './nudgeCharts'
import type { NudgeCase } from './setupNudges'

function Phone({ side, children }: { side: 'back' | 'front'; children: ReactNode }) {
  return (
    <div className={`setup-nudge-phone setup-nudge-phone--${side}`} aria-hidden="true">
      {children}
    </div>
  )
}

function Banner({
  icon,
  title,
  text,
  warn,
}: {
  icon: ReactNode
  title: string
  text: string
  warn?: boolean
}) {
  return (
    <div className={`setup-nudge-banner${warn ? ' setup-nudge-banner--warn' : ''}`}>
      <span className="setup-nudge-banner-ic">{icon}</span>
      <span className="setup-nudge-banner-body">
        <span className="setup-nudge-banner-t">{title}</span>
        <span className="setup-nudge-banner-x">{text}</span>
      </span>
    </div>
  )
}

function ListScreen({
  title,
  rows,
  icon,
  unread = 0,
}: {
  title: string
  rows: string[]
  icon?: ReactNode
  unread?: number
}) {
  return (
    <>
      <div className="setup-nudge-list-t">{title}</div>
      {rows.map((row, i) => (
        <div key={row} className="setup-nudge-list-row">
          <span className="setup-nudge-list-dot">{i === 0 && icon}</span>
          <span className="setup-nudge-list-l">{row}</span>
          {i < unread ? (
            <span className="setup-nudge-unread" />
          ) : (
            <span className="setup-nudge-list-bar" />
          )}
        </div>
      ))}
    </>
  )
}

function LockClock({ time, date }: { time: string; date: string }) {
  return (
    <div className="setup-nudge-clock">
      <span className="setup-nudge-clock-d">{date}</span>
      <span className="setup-nudge-clock-t">{time}</span>
    </div>
  )
}

const lucius = <LuciusMark size={10} />

function TelegramScene({ broken }: { broken?: boolean }) {
  return (
    <>
      <Phone side="back">
        <div className="setup-nudge-list-t">
          <span className="setup-nudge-list-dot">{lucius}</span> Lucius
        </div>
        <div className="setup-nudge-msg">Your spending this week</div>
        <div
          className={`setup-nudge-msg setup-nudge-msg--chart${broken ? ' setup-nudge-msg--failed' : ''}`}
        >
          <Sparkline points={[12, 18, 15, 26, 22, 30, 27]} height={30} />
        </div>
        <div className="setup-nudge-msg setup-nudge-msg--me">{broken ? 'Hello?' : 'Thanks!'}</div>
      </Phone>
      <Phone side="front">
        <LockClock time="9:41" date="Tuesday" />
        {broken ? (
          <Banner
            warn
            icon={<AlertTriangle size={10} />}
            title="Not delivered"
            text="Your daily briefing didn't arrive."
          />
        ) : (
          <>
            <Banner
              icon={<Send size={10} />}
              title="Lucius"
              text="Yesterday you spent $48, mostly groceries."
            />
            <Banner icon={<Send size={10} />} title="Lucius" text="Rent of $1,200 is due Friday." />
          </>
        )}
      </Phone>
    </>
  )
}

function GmailScene({ broken }: { broken?: boolean }) {
  return (
    <>
      <Phone side="back">
        <ListScreen
          title="Inbox"
          rows={['Your bank', 'Corner Cafe', 'City Power', 'Gym']}
          icon={<Mail size={8} />}
          unread={broken ? 4 : 2}
        />
      </Phone>
      <Phone side="front">
        {broken ? (
          <>
            <div className="setup-nudge-mailcard">
              <Mail size={10} /> 3 new emails from your bank
            </div>
            <Banner
              warn
              icon={<AlertTriangle size={10} />}
              title="Sync paused"
              text="Lucius can't read your inbox."
            />
          </>
        ) : (
          <>
            <div className="setup-nudge-mailcard">
              <Mail size={10} /> Card charged $23.40 at Corner Cafe
            </div>
            <div className="setup-nudge-flow">{lucius}</div>
            <div className="setup-nudge-row">
              <span className="setup-nudge-row-l">Corner Cafe</span>
              <span className="setup-nudge-row-v">−$23.40</span>
              <span className="setup-nudge-chip">Dining</span>
              <span className="setup-nudge-ok">
                <Check size={9} />
              </span>
            </div>
            <Progress label="Dining this month" value="$180 of $250" pct={72} />
          </>
        )}
      </Phone>
    </>
  )
}

function BriefingScene() {
  return (
    <>
      <span className="setup-nudge-sun" aria-hidden="true" />
      <Phone side="back">
        <LockClock time="9:00" date="Wednesday" />
        <Banner icon={<Sunrise size={10} />} title="Daily briefing" text="Ready for you" />
      </Phone>
      <Phone side="front">
        <div className="setup-nudge-card-t">Good morning</div>
        <div className="setup-nudge-stat setup-nudge-stat--plain">
          <span>Net worth</span>
          <b className="setup-nudge-up">+0.4%</b>
        </div>
        <Sparkline points={[40, 42, 41, 45, 44, 48, 47, 52, 51, 55]} />
        <div className="setup-nudge-stat">
          <span>Spent yesterday</span>
          <b>$48</b>
        </div>
        <div className="setup-nudge-stat">
          <span>Rent due</span>
          <b>Friday</b>
        </div>
      </Phone>
    </>
  )
}

const BARS = [38, 62, 44, 80, 52, 30, 58]

function ReportsScene() {
  return (
    <>
      <Phone side="back">
        <div className="setup-nudge-list-t">Where it went</div>
        <Donut
          parts={[
            { label: 'Rent', pct: 46 },
            { label: 'Groceries', pct: 24 },
            { label: 'Dining', pct: 18 },
            { label: 'Transport', pct: 12 },
          ]}
        />
        <div className="setup-nudge-list-t">November</div>
        <div className="setup-nudge-cal">
          {Array.from({ length: 14 }, (_, i) => (
            <span key={i} className={i === 0 || i === 7 ? 'setup-nudge-cal-on' : undefined} />
          ))}
        </div>
      </Phone>
      <Phone side="front">
        <div className="setup-nudge-card-t">
          <CalendarRange size={10} /> Last week
        </div>
        <div className="setup-nudge-bars">
          {BARS.map((h, i) => (
            <span
              key={i}
              style={{ height: `${h}%` }}
              className={i === 3 ? 'setup-nudge-bar-hi' : undefined}
            />
          ))}
        </div>
        <div className="setup-nudge-stat">
          <span>Spent</span>
          <b>$612</b>
        </div>
        <div className="setup-nudge-stat">
          <span>vs budget</span>
          <b className="setup-nudge-up">8% under</b>
        </div>
      </Phone>
    </>
  )
}

function DigestScene() {
  return (
    <>
      <Phone side="back">
        <ListScreen
          title="Inbox · 23 new"
          rows={['Your bank', 'Broker', 'City Power', 'Gym', 'Card']}
          icon={<Mail size={8} />}
          unread={5}
        />
      </Phone>
      <Phone side="front">
        <div className="setup-nudge-card-t">{lucius} Your digest</div>
        <Funnel
          steps={[
            { label: '23 emails', pct: 100 },
            { label: '8 about money', pct: 55 },
            { label: '3 need you', pct: 26 },
          ]}
        />
        <div className="setup-nudge-check">
          <Check size={9} /> 5 receipts logged
        </div>
        <div className="setup-nudge-check setup-nudge-check--warn">
          <AlertTriangle size={9} /> Gym payment failed
        </div>
        <div className="setup-nudge-check">
          <Check size={9} /> Bill due Thursday
        </div>
      </Phone>
    </>
  )
}

function Scene({ c }: { c: NudgeCase }) {
  switch (c) {
    case 'telegram':
      return <TelegramScene />
    case 'telegram-error':
      return <TelegramScene broken />
    case 'gmail':
      return <GmailScene />
    case 'email-error':
      return <GmailScene broken />
    case 'daily-briefing':
      return <BriefingScene />
    case 'reports':
      return <ReportsScene />
    case 'mail-digest':
      return <DigestScene />
  }
}

export function NudgePreview({ c, children }: { c: NudgeCase; children?: ReactNode }) {
  return (
    <div className={`setup-nudge-preview setup-nudge-preview--${c}`}>
      <div className="setup-nudge-scene">
        <Scene c={c} />
      </div>
      {children}
    </div>
  )
}
