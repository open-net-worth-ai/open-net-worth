// The per-provider CONFIGURATION form, hosted inside the connector detail page (spec §3).
//
// Spec §3 makes the detail page the one place a connector is configured, connected and
// disconnected. The credential forms themselves are unchanged — each one owns its own
// fields and its own per-provider write endpoint; each publishes its save to the modal's
// footer (`./formSave`) and reveals a saved secret through the shared eye
// (`ui/useSecretReveal`). This module is only the ROUTER: it maps `kind` → the
// right form (the switch the outgoing tab's card body used to own).
//
// WHY the forms are not rewritten: they are the tested, working write path to real
// endpoints (`/api/plaid/*`, `/api/telegram/config`, `/api/channels/*`, `/api/search/config`,
// …). Restyling their internals would be a rewrite of credential handling for a visual gain;
// the shell is what the redesign owns, and the shell is `ConnectorDetail`'s modal.
//
// Every connector's on/off is Pause/Resume/Remove ONLY (no per-form Enable checkbox). Email
// is ONE connector with one row, one address, one password and one save
// (`EmailUnifiedSection`); whether Lucius may SEND mail is the `email_send_message` tool's own
// `tool_permissions` entry (Connectors → Email → Tool permissions), not a second on/off bit.

import type { JSX } from 'react'
import { Text } from '../../ui'
import { EmailUnifiedSection } from './EmailUnifiedSection'
import { WebhookSection } from './ChannelSection'
import { TelegramSection } from './TelegramSection'
import { VoiceSection } from './VoiceSection'
import { SimpleFinSection } from './SimpleFinSection'
import { PlaidSection } from './PlaidSection'
import { LunchMoneySection } from './LunchMoneySection'
import { LunchFlowSection } from './LunchFlowSection'
import { ActualSection } from './ActualSection'
import { YnabSection } from './YnabSection'
import { WebSearchSection } from './WebSearchSection'
import { EnableBankingSection } from './EnableBankingSection'
import { GoCardlessSection } from './GoCardlessSection'
import { YodleeSection } from './YodleeSection'
import { AlpacaSection } from './AlpacaSection'
import { IbkrSection } from './IbkrSection'
import { YahooFinanceSection } from './YahooFinanceSection'
import { CoinGeckoSection } from './CoinGeckoSection'
import type { Connector } from './types'

const WEB_SEARCH_PREFIX = 'web_search:'

/** The connector kinds that have a credential form. An MCP server is deliberately absent —
 *  it is configured by the add flow at the moment it is reached (spec §7), and re-opened
 *  from the kebab's Edit configuration, not from here. */
const CONFIGURABLE_KINDS = new Set([
  'email',
  'webhook',
  'telegram',
  'voice',
  'simplefin',
  'plaid',
  'lunch_money',
  'lunch_flow',
  'ynab',
  'actual_budget',
  'web_search',
  'enable_banking',
  'gocardless',
  'yodlee',
  'alpaca',
  'ibkr',
  'yahoo_finance',
  'coingecko',
])

/**
 * Whether this connector can be configured from the detail page.
 *
 * A connector that is not `pausable` (Yahoo Finance, CoinGecko) still has a real, writable
 * config form (the Yahoo-first toggle, the CoinGecko API key + tier); what it lacks is only
 * the enable/disable concept, which `ConnectorDetail` gates separately via its own `alwaysOn`
 * prop, and — since 2026-08-22 — the ability to be removed, which it gates via `removable`.
 */
export function hasConfigForm(connector: Connector | undefined): boolean {
  if (!connector) return false
  return CONFIGURABLE_KINDS.has(connector.kind)
}

export interface ConnectorConfigProps {
  connector: Connector
  /** Seeded by the "Find your bank" institution search, which resolves a country to a
   *  provider and hands the country on so the form does not ask for it twice. */
  initialCountry?: string
}

export function ConnectorConfig({ connector, initialCountry }: ConnectorConfigProps): JSX.Element {
  // One opt-out for all 16 credential forms below — see screenSnapshot.secrets.test.ts.
  return <div data-ctx-secret="">{configForm(connector, initialCountry)}</div>
}

function configForm(connector: Connector, initialCountry?: string): JSX.Element {
  switch (connector.kind) {
    case 'email':
      return <EmailUnifiedSection />
    case 'webhook':
      return <WebhookSection />
    case 'telegram':
      return <TelegramSection />
    case 'voice':
      return <VoiceSection />
    case 'simplefin':
      return <SimpleFinSection />
    case 'plaid':
      return <PlaidSection />
    case 'lunch_money':
      return <LunchMoneySection />
    case 'lunch_flow':
      return <LunchFlowSection />
    case 'ynab':
      return <YnabSection />
    case 'actual_budget':
      return <ActualSection />
    case 'web_search':
      return <WebSearchSection providerId={connector.id.replace(WEB_SEARCH_PREFIX, '')} />
    case 'enable_banking':
      return <EnableBankingSection initialCountry={initialCountry} />
    case 'gocardless':
      return <GoCardlessSection initialCountry={initialCountry} />
    case 'yodlee':
      return <YodleeSection />
    case 'alpaca':
      return <AlpacaSection />
    case 'ibkr':
      return <IbkrSection />
    case 'yahoo_finance':
      return <YahooFinanceSection />
    case 'coingecko':
      return <CoinGeckoSection />
    default:
      return (
        <Text size="sm" tone="faint">
          This connector has no settings to configure here.
        </Text>
      )
  }
}
