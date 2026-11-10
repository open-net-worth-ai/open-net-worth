import assert from 'node:assert/strict';
import { test } from 'node:test';
import { parseWindowState, restoredBounds } from '../src/main/window-state';

const MIN = { width: 760, height: 560 };
const laptop = { x: 0, y: 25, width: 1512, height: 920 };
const monitor = { x: 1512, y: 0, width: 2560, height: 1415 };

test('a saved window state is read back, and anything malformed is ignored', () => {
  assert.deepEqual(parseWindowState('{"bounds":{"x":10,"y":40,"width":1400,"height":900},"maximized":true}'), { bounds: { x: 10, y: 40, width: 1400, height: 900 }, maximized: true });
  assert.equal(parseWindowState('not json'), null);
  assert.equal(parseWindowState('{"bounds":{"x":10,"y":40,"width":"wide","height":900},"maximized":false}'), null);
  assert.equal(parseWindowState('{"bounds":{"x":10,"y":40,"width":1400,"height":900}}'), null);
});

test('the window reopens where it was on a display that is still connected', () => {
  assert.deepEqual(restoredBounds({ x: 1700, y: 100, width: 1800, height: 1100 }, [laptop, monitor], MIN), { x: 1700, y: 100, width: 1800, height: 1100 });
});

test('a window that filled the screen reopens filling it, not inset', () => {
  assert.deepEqual(restoredBounds(laptop, [laptop], MIN), laptop);
});

test('a window last on an unplugged monitor keeps its size, fitted, but not its position', () => {
  assert.deepEqual(restoredBounds({ x: 1700, y: 100, width: 1800, height: 1100 }, [laptop], MIN), { width: 1512, height: 920 });
});

test('a window hanging partly off its display is pulled back inside it', () => {
  assert.deepEqual(restoredBounds({ x: 900, y: 25, width: 1000, height: 800 }, [laptop], MIN), { x: 512, y: 25, width: 1000, height: 800 });
});
