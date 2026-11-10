import assert from 'node:assert/strict';
import { test } from 'node:test';
import { MicrophoneReopen, REOPEN_WINDOW_MS } from '../src/main/microphone-reopen';

function setup(start: string) {
  const state = { status: start, now: 1_000 };
  const reopen = new MicrophoneReopen(() => state.status, () => state.now);
  reopen.watch();
  return { state, reopen };
}

test('allowing a refused microphone reopens the app when macOS quits it from Settings', () => {
  const { state, reopen } = setup('denied');
  assert.equal(reopen.shouldReopen(false), false);
  state.status = 'granted';
  assert.equal(reopen.shouldReopen(false), true);
});

test('a Cmd-Q of your own, with the app in front, stays quit', () => {
  const { state, reopen } = setup('denied');
  state.status = 'granted';
  assert.equal(reopen.shouldReopen(true), false);
});

test('a quit long after allowing it stays quit', () => {
  const { state, reopen } = setup('denied');
  state.status = 'granted';
  assert.equal(reopen.shouldReopen(false), true);
  state.now += REOPEN_WINDOW_MS + 1;
  assert.equal(reopen.shouldReopen(false), false);
});

test('a microphone that was never refused never reopens the app', () => {
  assert.equal(setup('granted').reopen.shouldReopen(false), false);
  const prompted = setup('not-determined');
  prompted.state.status = 'granted'; // our own prompt: macOS doesn't quit the app for this
  assert.equal(prompted.reopen.shouldReopen(false), false);
});

test('refusing the prompt first, then allowing it in Settings, counts', () => {
  const { state, reopen } = setup('not-determined');
  state.status = 'denied';
  assert.equal(reopen.shouldReopen(false), false);
  state.status = 'granted';
  assert.equal(reopen.shouldReopen(false), true);
});
