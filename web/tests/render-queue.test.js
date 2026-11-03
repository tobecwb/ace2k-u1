import { test } from 'node:test';
import assert from 'node:assert/strict';
import { makeRenderer, MAX_CHAIN } from '../js/render-queue.js';

const tick = () => new Promise((r) => setTimeout(r, 0));

test('a draw asked for during a draw runs once after it, never nested', async () => {
  let depth = 0;
  let maxDepth = 0;
  let runs = 0;
  const render = makeRenderer(() => {
    depth += 1;
    maxDepth = Math.max(maxDepth, depth);
    runs += 1;
    if (runs === 1) render(); // an effect that draws again
    depth -= 1;
  });
  render();
  assert.equal(runs, 1);
  await tick();
  assert.deepEqual([runs, maxDepth], [2, 1]);
});

test('several calls during a draw coalesce into one more draw; a draw that throws does not lock the page', async () => {
  let runs = 0;
  const render = makeRenderer(() => {
    runs += 1;
    if (runs === 1) {
      render();
      render();
      render();
    }
    if (runs === 3) throw new Error('boom');
  });
  render();
  await tick();
  assert.equal(runs, 2);
  assert.throws(() => {
    runs = 2;
    render();
  }, /boom/);
  runs = 10;
  render();
  assert.equal(runs, 11);
});

test('a chain of draws that each ask for another is cut after the cap, with one warning, then one last draw', async () => {
  const warns = [];
  const real = console.warn;
  console.warn = (m) => warns.push(m);
  try {
    let runs = 0;
    const render = makeRenderer(() => {
      runs += 1;
      render();
    });
    render();
    await tick();
    await tick();
    // The chain, then one last draw on a later task; what that one asks for is dropped.
    assert.equal(runs, MAX_CHAIN + 2);
    await tick();
    assert.equal(runs, MAX_CHAIN + 2);
    assert.equal(warns.length, 1);
    // A later draw starts a new chain and the warning is not repeated.
    render();
    await tick();
    await tick();
    assert.equal(runs, 2 * (MAX_CHAIN + 2));
    assert.equal(warns.length, 1);
  } finally {
    console.warn = real;
  }
});

test('a draw asked for past the cap is not lost: the last draw shows the latest state', async () => {
  const real = console.warn;
  console.warn = () => {};
  try {
    let state = 0;
    const shown = [];
    const render = makeRenderer(() => {
      shown.push(state);
      // Each draw moves the state on and asks for another, one more time than the chain allows.
      if (state < MAX_CHAIN + 1) {
        state += 1;
        render();
      }
    });
    render();
    await tick();
    await tick();
    // The draw asked for at the cap still comes: the page shows the last state.
    assert.equal(shown.at(-1), state);
    assert.equal(shown.length, MAX_CHAIN + 2);
  } finally {
    console.warn = real;
  }
});
