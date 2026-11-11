import { useEffect, useRef, useState } from 'react'
import { Check, Copy } from 'lucide-react'
import { Button, CollapsibleSection, Stack, Text } from '../ui'
import { TERMINAL_NOTICE } from './privateCloudCopy'

export type CliHostPlatform = 'macos' | 'windows' | 'linux' | 'other'
export type CliSignInState = 'signed_in' | 'not_signed_in' | 'unknown'
export interface CliSubscriptionHelpProps {
  provider: 'claude_cli' | 'codex_cli'
  platform?: CliHostPlatform
  detected: boolean
  /** What the CLI itself says about its login. Found is not signed in. */
  signIn?: CliSignInState
  /** The app runs in a container, so the sign-in has to go through `docker exec`. */
  inContainer?: boolean
  onCheck: () => Promise<unknown>
  /** Private Cloud: the sign-in happens in the in-app Terminal on the server, not on this computer. */
  terminal?: { onOpen: () => void }
  /** Bumped by the parent to open the steps — e.g. "Use this" was pressed before signing in. */
  openRequest?: number
  /** Open the sign-in steps on their own the first time the CLI reads as signed out. */
  openWhenSignedOut?: boolean
}

const PROVIDERS = {
  claude_cli: {
    label: 'Claude Code',
    unix: 'curl -fsSL https://claude.ai/install.sh | bash',
    windows: 'irm https://claude.ai/install.ps1 | iex',
    login: 'claude auth login',
    instructions: 'https://code.claude.com/docs/en/setup',
    signIn: 'Choose your Claude subscription when prompted.',
  },
  codex_cli: {
    label: 'Codex',
    unix: 'curl -fsSL https://chatgpt.com/codex/install.sh | sh',
    windows:
      'powershell -ExecutionPolicy ByPass -c "irm https://chatgpt.com/codex/install.ps1 | iex"',
    login: 'codex login',
    instructions: 'https://developers.openai.com/codex/cli',
    signIn: 'Choose “Sign in with ChatGPT” when prompted.',
  },
} as const

function CopyCommand({ command, label }: { command: string; label: string }) {
  const [copied, setCopied] = useState(false)
  const [failed, setFailed] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current)
    },
    [],
  )

  async function copy() {
    if (timer.current) clearTimeout(timer.current)
    setCopied(false)
    setFailed(false)
    try {
      await navigator.clipboard.writeText(command)
      setCopied(true)
      timer.current = setTimeout(() => setCopied(false), 2000)
    } catch {
      setFailed(true)
    }
  }

  return (
    <Stack gap="--space-2">
      <div className="cli-subscription-command-row">
        <code className="cli-subscription-command">{command}</code>
        <button
          type="button"
          className="cli-subscription-copy"
          aria-label={label}
          title={copied ? 'Copied' : 'Copy'}
          data-copied={copied || undefined}
          onClick={() => void copy()}
        >
          {copied ? <Check size={16} aria-hidden /> : <Copy size={16} aria-hidden />}
          <span className="sr-only">{copied ? 'Copied' : 'Copy'}</span>
        </button>
      </div>
      <span role="status" className="sr-only">
        {copied ? 'Command copied.' : ''}
      </span>
      {failed && (
        <div role="status">
          <Text as="p" size="sm" tone="muted">
            Could not copy. Select the command and copy it manually.
          </Text>
        </div>
      )}
    </Stack>
  )
}

function InstallationSteps({
  provider,
  platform,
}: Pick<CliSubscriptionHelpProps, 'provider' | 'platform'>) {
  const details = PROVIDERS[provider]
  const knownPlatform = platform === 'windows' || platform === 'macos' || platform === 'linux'
  const command = platform === 'windows' ? details.windows : details.unix
  return (
    <Stack gap="--space-5">
      {knownPlatform && (
        <>
          <Stack gap="--space-2">
            <Text as="p" size="sm" weight={600}>
              1. Install {details.label} in {platform === 'windows' ? 'PowerShell' : 'Terminal'}
            </Text>
            <CopyCommand
              key={command}
              command={command}
              label={`Copy ${details.label} install command`}
            />
          </Stack>
          <Stack gap="--space-2">
            <Text as="p" size="sm" weight={600}>
              2. Open a new terminal and sign in
            </Text>
            <CopyCommand
              key={details.login}
              command={details.login}
              label={`Copy ${details.label} sign-in command`}
            />
            <Text as="p" size="sm" tone="muted">
              {details.signIn} Plan limits apply.
            </Text>
          </Stack>
        </>
      )}
      {!knownPlatform && (
        <a
          className="cli-subscription-link"
          href={details.instructions}
          target="_blank"
          rel="noreferrer"
        >
          Official {details.label} installation instructions
        </a>
      )}
    </Stack>
  )
}

// The browser sign-in returns to the container's own 127.0.0.1, which nothing outside can reach.
const CONTAINER_LOGIN = {
  claude_cli: 'docker exec -it open-net-worth claude auth login',
  codex_cli: 'docker exec -it open-net-worth codex login --device-auth',
} as const

function SignInSteps({
  provider,
  inContainer,
}: Pick<CliSubscriptionHelpProps, 'provider' | 'inContainer'>) {
  const details = PROVIDERS[provider]
  const command = inContainer ? CONTAINER_LOGIN[provider] : details.login
  const signIn =
    inContainer && provider === 'codex_cli'
      ? 'Open the link it shows on any device and enter the code.'
      : details.signIn
  return (
    <Stack gap="--space-3">
      <Text as="p" size="sm">
        {inContainer
          ? `${details.label} runs inside the container. Sign it in from the machine running Docker:`
          : 'Run this on the computer running Open Net Worth.'}
      </Text>
      <CopyCommand command={command} label={`Copy ${details.label} sign-in command`} />
      <Text as="p" size="sm" tone="muted">
        {signIn} This updates on its own.
      </Text>
    </Stack>
  )
}

function TerminalSteps({
  provider,
  onOpen,
}: Pick<CliSubscriptionHelpProps, 'provider'> & { onOpen: () => void }) {
  const details = PROVIDERS[provider]
  // The terminal runs on the server: Codex's default sign-in sends the browser to the server's
  // own 127.0.0.1, which this browser cannot reach. Its device-code sign-in works from anywhere.
  const command = provider === 'claude_cli' ? 'claude' : 'codex login --device-auth'
  const signIn =
    provider === 'claude_cli'
      ? details.signIn
      : 'It shows a link and a code: open the link here and enter the code.'
  return (
    <Stack gap="--space-3">
      <Text as="p" size="sm">
        Open Terminal and run <code className="cli-subscription-command">{command}</code>. {signIn}{' '}
        This updates on its own once you are signed in.
      </Text>
      <Text as="p" size="sm" tone="muted">
        {TERMINAL_NOTICE}
      </Text>
      <div>
        <Button variant="secondary" onClick={onOpen}>
          Open Terminal
        </Button>
      </div>
    </Stack>
  )
}

// While the help is open, re-check on its own: when the window regains focus (back from the
// terminal) and every few seconds. There is no button to press.
const RECHECK_MS = 4000

export function CliSubscriptionHelp({
  provider,
  platform,
  detected,
  signIn,
  inContainer,
  onCheck,
  terminal,
  openRequest,
  openWhenSignedOut,
}: CliSubscriptionHelpProps) {
  const [open, setOpen] = useState(false)
  // A new request opens the steps — adjusted during render, React's pattern for a prop change.
  const [seenRequest, setSeenRequest] = useState(openRequest ?? 0)
  if ((openRequest ?? 0) !== seenRequest) {
    setSeenRequest(openRequest ?? 0)
    if (openRequest) setOpen(true)
  }
  // Once only: a reader who closes the steps keeps them closed across the 4s re-checks.
  const [autoOpened, setAutoOpened] = useState(false)
  if (openWhenSignedOut && !autoOpened && detected && signIn === 'not_signed_in') {
    setAutoOpened(true)
    setOpen(true)
  }
  const check = useRef(onCheck)
  useEffect(() => {
    check.current = onCheck
  }, [onCheck])

  useEffect(() => {
    if (!open) return
    let busy = false
    const recheck = () => {
      if (busy) return
      busy = true
      void check
        .current()
        .catch(() => {})
        .finally(() => {
          busy = false
        })
    }
    window.addEventListener('focus', recheck)
    const timer = setInterval(recheck, RECHECK_MS)
    return () => {
      window.removeEventListener('focus', recheck)
      clearInterval(timer)
    }
  }, [open])

  // Help is for a tool that isn't there yet, or can't sign in yet. A found tool with no word
  // against its login needs no row; one found while the help is open (installed just now) keeps
  // it open, since found is not signed in.
  const needsSignIn = detected && signIn === 'not_signed_in'
  const justSignedIn = detected && signIn === 'signed_in'
  if (detected && !open && !needsSignIn) return null

  return (
    <CollapsibleSection
      className="cli-subscription-help"
      title={terminal ? 'Sign in from Terminal' : needsSignIn ? 'Sign in' : 'Install & sign in'}
      open={open}
      onOpenChange={setOpen}
      actions={
        <span role="status">
          <Text size="sm" tone="muted">
            {!detected
              ? 'Not installed'
              : needsSignIn
                ? 'Not signed in'
                : justSignedIn
                  ? 'Signed in'
                  : 'Detected'}
          </Text>
        </span>
      }
    >
      {justSignedIn ? (
        <Text as="p" size="sm">
          Signed in. Lucius can use it now.
        </Text>
      ) : terminal ? (
        <TerminalSteps provider={provider} onOpen={terminal.onOpen} />
      ) : needsSignIn ? (
        <SignInSteps provider={provider} inContainer={inContainer} />
      ) : (
        <Stack gap="--space-5">
          <Text as="p" size="sm" tone="muted">
            Run these on the computer running Open Net Worth. This updates on its own once
            it&rsquo;s installed.
          </Text>
          <InstallationSteps provider={provider} platform={platform} />
        </Stack>
      )}
    </CollapsibleSection>
  )
}
