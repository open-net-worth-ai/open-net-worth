// Reasoning, drawn as one more step on the activity rail — because that is what it is.
//
// Lives HERE, not in MessageBubble, so `ToolActivity` can render a thought in its true place
// among the calls without importing its own parent. A turn that thinks, calls a tool, then
// thinks again is ONE run of activity; splitting it into two groups either side of the
// thought gave the second half no summary bar, no step count, and no way to fold — so
// collapsing the group left the thought and a stray row still on screen (owner report,
// 2026-08-25).

import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { SAFE_MARKDOWN_COMPONENTS } from '../../ui/safeMarkdown'
import remarkGfm from 'remark-gfm'
import { ChevronDown, LuciusMark } from '../../../ui/icons'
import { previewText } from './previewText'

/** The row itself, for use INSIDE an existing `.msg-toolact__rail`. */
export function ThinkingRow({ text, streaming }: { text: string; streaming?: boolean }) {
  // Open while it is being WRITTEN, folded once it is finished — unless you say otherwise.
  //
  // Reasoning arriving two clamped lines at a time is unreadable and reads as broken: the
  // text visibly changes under a preview that never grows (owner report, 2026-08-23). Once
  // the turn is done it is history, and history belongs out of the way.
  //
  // `null` means "nobody has decided", so a click at any point wins permanently — including
  // folding it mid-stream, which then stays folded when the turn ends.
  const [choice, setChoice] = useState<boolean | null>(null)
  const open = choice ?? Boolean(streaming)
  const contentRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (streaming && open && contentRef.current) {
      const el = contentRef.current
      if (el.scrollHeight > el.clientHeight) {
        el.scrollTop = el.scrollHeight
      }
    }
  }, [text, streaming, open])

  // The row is a two-line preview only while collapsed WITH reasoning to preview; open, and
  // before any text has arrived, it is a one-line header and aligns like every other row.
  const showPreview = !open && Boolean(text)

  return (
    <div
      className={`msg-toolact__row msg-toolact__row--think${open ? ' msg-toolact__row--open' : ''}`}
    >
      <button
        type="button"
        className={`msg-toolact__head${showPreview ? ' msg-toolact__head--preview' : ''}`}
        aria-expanded={open}
        onClick={() => setChoice(!open)}
      >
        <span className="msg-toolact__icon">
          {/* LuciusMark is the app's Lucius mark ("Ask Lucius about…") — the reasoning is
              Lucius's, so it wears Lucius's icon rather than a generic brain. */}
          <LuciusMark size={14} strokeWidth={1.5} aria-hidden="true" />
        </span>
        <span className="msg-toolact__label msg-toolact__label--think">
          {/* OPEN: a plain header. The clamped preview is the first two lines of the very
              text rendered in full directly beneath it, so leaving it up prints those lines
              twice (owner report, 2026-08-22). */}
          {!showPreview ? (
            <span className="msg-toolact__text">{streaming ? 'Thinking…' : 'Thought process'}</span>
          ) : (
            /* Never pre-shortened here: a truncation done in JS would bake a different
               sentence into the DOM. CSS line-clamp does it, and the full text is one
               click away. */
            <span className="msg-toolact__think-preview">{previewText(text)}</span>
          )}
        </span>
        <ChevronDown size={12} strokeWidth={1.5} className="msg-toolact__caret" aria-hidden />
      </button>
      {open && (
        <div className="msg-toolact__detail msg-toolact__think-body" ref={contentRef}>
          {/* Reasoning arrives as markdown — Codex titles each part with a bold headline,
              which rendered as literal "**…**" while this was a plain string. */}
          <ReactMarkdown
            remarkPlugins={[[remarkGfm, { singleTilde: false }]]}
            components={SAFE_MARKDOWN_COMPONENTS}
          >
            {text}
          </ReactMarkdown>
        </div>
      )}
    </div>
  )
}

/** A thought with no tool calls around it to belong to: its own one-row rail.
 *
 *  Used for the merged block (Settings → AI → Chat), for a transcript recorded before
 *  thinking became a positioned block, and for a thought that leads nowhere. */
export function ThinkingBlock({ text, streaming }: { text: string; streaming?: boolean }) {
  return (
    <div className="msg-toolact">
      <div className="msg-toolact__rail">
        <ThinkingRow text={text} streaming={streaming} />
      </div>
    </div>
  )
}
