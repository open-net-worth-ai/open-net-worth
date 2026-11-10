import { readFileSync, renameSync, writeFileSync } from 'node:fs';

export interface Rect { x: number; y: number; width: number; height: number }
export interface WindowState { bounds: Rect; maximized: boolean }

const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);

export function parseWindowState(text: string): WindowState | null {
  try {
    const saved = JSON.parse(text) as { bounds?: Partial<Rect>; maximized?: unknown };
    const bounds = saved.bounds;
    if (!bounds || ![bounds.x, bounds.y, bounds.width, bounds.height].every(finite) || typeof saved.maximized !== 'boolean') return null;
    return { bounds: bounds as Rect, maximized: saved.maximized };
  } catch { return null; }
}

// A saved position is kept only while most of the window still lands on a connected display —
// an unplugged monitor must not reopen the window off screen. The size always fits the display.
export function restoredBounds(saved: Rect, workAreas: Rect[], min: { width: number; height: number }): Partial<Rect> {
  const overlap = (area: Rect) => Math.max(0, Math.min(saved.x + saved.width, area.x + area.width) - Math.max(saved.x, area.x))
    * Math.max(0, Math.min(saved.y + saved.height, area.y + area.height) - Math.max(saved.y, area.y));
  const best = workAreas.reduce<Rect | null>((found, area) => !found || overlap(area) > overlap(found) ? area : found, null);
  if (!best) return {};
  const width = Math.max(min.width, Math.min(saved.width, best.width));
  const height = Math.max(min.height, Math.min(saved.height, best.height));
  if (overlap(best) < saved.width * saved.height / 2) return { width, height };
  const x = Math.min(Math.max(saved.x, best.x), best.x + best.width - width);
  const y = Math.min(Math.max(saved.y, best.y), best.y + best.height - height);
  return { x, y, width, height };
}

export function readWindowState(path: string): WindowState | null {
  try { return parseWindowState(readFileSync(path, 'utf8')); } catch { return null; }
}

export function writeWindowState(path: string, state: WindowState): void {
  try {
    writeFileSync(`${path}.tmp`, JSON.stringify(state), { mode: 0o600 });
    renameSync(`${path}.tmp`, path);
  } catch { /* the window still opens at its default size next time */ }
}
