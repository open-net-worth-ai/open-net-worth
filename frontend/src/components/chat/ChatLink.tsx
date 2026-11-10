// A link in one of Lucius's replies. An in-app path (/help/…, /accounts) navigates inside the
// app: as a plain link it reloaded the whole app onto that page, which threw away the chat on
// screen and left Help nowhere to return to on close (it fell back to the Dashboard). A link to a
// past conversation (/lucius?conversation=<id>) switches the chat it was clicked in — the side
// panel, the floating dock or the Lucius page — to that conversation, without leaving the page.
// Anything else opens in a new tab, so the conversation stays put.
import type { AnchorHTMLAttributes, MouseEvent } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { useLuciusChatOptional } from '../../state/luciusChat'

const isInApp = (href: string | undefined): href is string =>
  !!href && href.startsWith('/') && !href.startsWith('//')

export function ChatLink({ href, children, ...rest }: AnchorHTMLAttributes<HTMLAnchorElement>) {
  const navigate = useNavigate()
  const chat = useLuciusChatOptional()
  if (!isInApp(href)) {
    return (
      <a href={href} target="_blank" rel="noopener noreferrer" {...rest}>
        {children}
      </a>
    )
  }
  const onClick = (e: MouseEvent<HTMLAnchorElement>) => {
    // A modified click (new tab, new window) keeps the browser's own behaviour.
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return
    e.preventDefault()
    const url = new URL(href, window.location.origin)
    const conversation = url.pathname === '/lucius' ? url.searchParams.get('conversation') : null
    if (conversation && chat) {
      chat.switchConversation(conversation)
      return
    }
    void navigate({
      to: url.pathname,
      search: Object.fromEntries(url.searchParams),
      hash: url.hash.replace(/^#/, '') || undefined,
    } as never)
  }
  return (
    <a href={href} onClick={onClick} {...rest}>
      {children}
    </a>
  )
}
