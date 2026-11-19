import { useState } from 'react'
import { Button, InfoPopover, TextField } from '../ui'
import { LuciusMark } from '../../ui/icons'
import { IntelCardHead } from './IntelCardHead'
import type { WatchlistItem } from './WatchlistsSection'

// The Topics & macro tab's lead card: the topics you follow (tap to edit one), a way to follow
// another, and — on a cold start — a few starter topics.

const STARTER_TOPICS = [
  'Global markets this week',
  'Central-bank policy & rates',
  'AI & technology',
  'Geopolitics & markets',
]

const ABOUT =
  'Topics & macro collects the topics you follow — each is followed on its own, not kept in a ' +
  'watchlist — plus market-wide stories: central banks, currencies and the economy, and news ' +
  'about companies you neither own nor watch. Stories about your holdings are on My holdings; ' +
  'stories about a watchlist’s tickers are on that list’s tab.'

interface Props {
  topics: WatchlistItem[]
  onEditTopic: (item: WatchlistItem) => void
  onAddTopic: (topic: string) => void
}

export function TopicsStrip({ topics, onEditTopic, onAddTopic }: Props) {
  return (
    <section className="intel-brief-card">
      <IntelCardHead
        icon={<LuciusMark size={13} />}
        title="Topics you follow"
        info={<InfoPopover text={ABOUT} width={300} />}
      />
      <div className="intel-topics-strip-body">
        {topics.length > 0 ? (
          <div className="intel-topics-chips">
            {topics.map((t) => (
              <button
                key={t.id}
                type="button"
                className={`intel-topic-chip${t.monitor_news === false ? ' intel-topic-chip--muted' : ''}`}
                onClick={() => onEditTopic(t)}
                title={t.monitor_news === false ? 'Muted — tap to edit' : 'Tap to edit'}
              >
                {t.label || t.value}
                {t.monitor_news === false && <span className="intel-topic-chip-count">muted</span>}
              </button>
            ))}
          </div>
        ) : (
          <>
            <p className="intel-topics-note">
              Follow a theme, an industry or a company and Lucius builds a story from it on the next
              scan.
            </p>
            <div className="intel-topics-chips">
              {STARTER_TOPICS.map((t) => (
                <button
                  key={t}
                  type="button"
                  className="intel-topic-chip"
                  onClick={() => onAddTopic(t)}
                >
                  + {t}
                </button>
              ))}
            </div>
          </>
        )}
        <FollowTopicRow onAdd={onAddTopic} />
      </div>
    </section>
  )
}

function FollowTopicRow({ onAdd }: { onAdd: (t: string) => void }) {
  const [val, setVal] = useState('')
  function submit() {
    const t = val.trim()
    if (!t) return
    onAdd(t)
    setVal('')
  }
  return (
    <div className="intel-follow-topic-row">
      <TextField
        value={val}
        onChange={(e) => setVal(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && submit()}
        placeholder='Follow another topic — e.g. "SpaceX IPO and relevant stocks"'
        aria-label="Follow another topic"
      />
      <Button variant="primary" onClick={submit} disabled={!val.trim()}>
        Add via Lucius <LuciusMark size="1em" aria-hidden="true" />
      </Button>
    </div>
  )
}
