const POLL_MS = 2_000;
export const REOPEN_WINDOW_MS = 120_000;

/**
 * Turning the microphone on in System Settings has macOS quit the app to reopen it, and it never
 * came back: our quit cancels the system's request to shut down in order first (owner,
 * 2026-11-04). macOS doesn't say why it asks, so this reads the signs: the switch went from refused
 * to allowed a moment ago, and the app isn't in front, as it would be for a Cmd-Q of your own.
 * Never from "not asked yet": that grant is our own prompt, which needs no quit.
 */
export class MicrophoneReopen {
  private refused = false;
  private allowedAt: number | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;

  constructor(
    private readonly status: () => string,
    private readonly now: () => number = Date.now,
  ) {}

  watch(): void {
    this.check();
    if (this.status() === 'granted') return; // turning it off has macOS end the app outright
    this.timer = setInterval(() => this.check(), POLL_MS);
    this.timer.unref();
  }

  shouldReopen(inFront: boolean): boolean {
    this.check();
    return !inFront && this.allowedAt !== null && this.now() - this.allowedAt <= REOPEN_WINDOW_MS;
  }

  private check(): void {
    const status = this.status();
    if (status === 'denied' || status === 'restricted') {
      this.refused = true;
    } else if (status === 'granted') {
      if (this.refused) this.allowedAt = this.now();
      this.refused = false;
      if (this.timer) clearInterval(this.timer);
      this.timer = null;
    }
  }
}
