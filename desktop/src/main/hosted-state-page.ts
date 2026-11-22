import { escapeHtml, nativeDocument } from './native-page';
import type { AgentCheck } from './hosted-state';

interface Message { title: string; body: string; primary?: 'retry' | 'portal' | 'update'; portalLabel?: string; auto?: boolean }

const WAITING = 'This usually takes a few minutes. This screen refreshes on its own.';

function message(check: Exclude<AgentCheck, { kind: 'open' }>): Message {
  if (check.kind === 'update') return { title: 'Update the desktop app.', body: `Your Private Cloud now needs Open Net Worth ${escapeHtml(check.required)} or newer. Download the update, install it, and open the app again.`, primary: 'update' };
  if (check.kind === 'unreachable') return { title: 'Can’t reach your Private Cloud.', body: 'Check your internet connection. If you’re online, try again in a minute.', primary: 'retry' };
  switch (check.state) {
    case 'starting': return { title: 'Your Private Cloud is starting.', body: WAITING, auto: true };
    case 'updating': return { title: 'Your Private Cloud is updating.', body: `${WAITING} Your book is kept safe throughout.`, auto: true };
    case 'restarting': return { title: 'Your Private Cloud is restarting.', body: WAITING, auto: true };
    case 'stopped': return { title: 'Your Private Cloud is paused.', body: 'Reactivate it from your Private Cloud account, then come back here.', primary: 'portal', portalLabel: 'Reactivate in your account' };
    case 'failed': return { title: 'Something went wrong on your Private Cloud.', body: 'You can restart it from your Private Cloud account. Your book is kept safe.', primary: 'portal', portalLabel: 'Open your account' };
    case 'deleting': return { title: 'This Private Cloud is being deleted.', body: 'It will no longer open. Choose another connection to keep using the app.', primary: 'portal', portalLabel: 'Open your account' };
    default: return { title: 'No Private Cloud found at this address.', body: 'It may have been deleted, or the address is wrong. Sign in again, or open your account to check.', primary: 'portal', portalLabel: 'Open your account' };
  }
}

// What the window shows instead of the app when the Private Cloud can't be opened yet, with the fix on the page.
export function hostedStatePage(check: Exclude<AgentCheck, { kind: 'open' }>, theme: 'dark' | 'light'): string {
  const text = message(check);
  const primary = (kind: 'retry' | 'portal' | 'update', label: string) => `<button${text.primary === kind ? ' class="primary"' : ''} id="${kind}">${label}</button>`;
  const actions = [text.primary === 'update' ? primary('update', 'Download the update') : '', text.primary === 'portal' ? primary('portal', escapeHtml(text.portalLabel ?? 'Open your account')) : '', text.primary === 'update' ? '' : primary('retry', 'Try again'), '<button class="link" id="change">Change connection</button>'].join('');
  const body = `<h1>${text.title}</h1><p>${text.body}</p><div class="actions">${actions}</div>`;
  const script = `const hosted=window.onwHosted;
document.getElementById('retry')?.addEventListener('click',()=>run(()=>window.onwNativeConnection.retry()));
document.getElementById('portal')?.addEventListener('click',()=>run(()=>hosted.openPortal()));
document.getElementById('update')?.addEventListener('click',()=>run(()=>hosted.openUpdate()));
document.getElementById('change').addEventListener('click',()=>run(()=>window.onwDesktopClient.changeConnection()));
${text.auto ? "setTimeout(()=>window.onwNativeConnection.retry().catch(()=>{}),5000);" : ''}`;
  return nativeDocument({ theme, title: 'Private Cloud', body, script, nonce: 'onw-state' });
}
