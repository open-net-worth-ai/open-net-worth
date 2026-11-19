// "Don't see yours?" — the two ways to connect what the directory does not list: a custom MCP
// server and a REST API. One row at the top of Browse & add offers both; the MCP and APIs tabs
// show one each.

import type { JSX } from 'react'
import { Btn } from './primitives'
import { Icons } from './icons'
import { Link2, Share2 } from '../../../ui/icons'

interface AddYourOwnRowProps {
  /** Opens the custom MCP server form; absent where an MCP server is not what is on offer. */
  onMcp?: () => void
  /** Opens the form for a new REST API; absent likewise. */
  onRest?: () => void
}

/** "Don't see yours?" — the two ways to connect what the directory does not list, a custom
 *  MCP server and a REST API, as a row of its own at the top of the list. Each button names
 *  what it adds: "Add custom server" alone read as the only way, and never said MCP. */
export function AddYourOwnRow({ onMcp, onRest }: AddYourOwnRowProps): JSX.Element {
  const copy =
    onMcp && onRest
      ? {
          title: 'Don’t see yours? Connect it yourself',
          sub: 'Add any MCP server by its URL or a command, or any REST API by its address and key.',
        }
      : onMcp
        ? {
            title: 'Don’t see yours? Add a custom MCP server',
            sub: 'Connect any MCP server by its URL or a command run on this machine.',
          }
        : {
            title: 'Connect any REST API',
            sub: 'Give its address, a key, and its endpoints or an OpenAPI document. Each endpoint becomes a tool Lucius can call.',
          }
  return (
    <div className="cn-customrow">
      {/* The Connectors screen's own nav icon — this row IS "add a connector". */}
      <span className="cn-customrow-ic" aria-hidden>
        {onMcp ? <Share2 size={16} /> : <Link2 size={16} />}
      </span>
      <div className="cn-customrow-text">
        <div className="cn-customrow-title">{copy.title}</div>
        <div className="cn-customrow-sub">{copy.sub}</div>
      </div>
      <div className="cn-customrow-actions">
        {onMcp ? (
          <Btn variant="primary" onClick={onMcp}>
            <Icons.plus /> Add MCP server
          </Btn>
        ) : null}
        {onRest ? (
          <Btn variant="primary" onClick={onRest}>
            <Icons.plus /> Add REST API
          </Btn>
        ) : null}
      </div>
    </div>
  )
}
