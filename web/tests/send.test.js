import { test } from 'node:test';
import assert from 'node:assert/strict';
import { built, send, sendScript } from '../js/send.js';
import { RefusedError } from '../js/model.js';

const t = (key, vars) => (vars ? `${key} ${JSON.stringify(vars)}` : key);
function harness({ demo = false, status = { ace2k: { lanes: [] } }, reject = null, units = null } = {}) {
  const calls = { toast: [], render: 0, gcode: [], once: [], pending: [] };
  const app = {
    status: units ? { ace2k_units: { units } } : status,
    pending: new Set(),
    events: [],
    conn: {
      unit: null,
      gcode: async (s) => {
        calls.pending.push([...app.pending]);
        calls.gcode.push(s);
        if (reject) throw new Error(reject);
      },
      gcodeOnce: async (k, s) => {
        calls.once.push([k, s]);
        if (reject) throw new Error(reject);
      },
    },
  };
  const ctx = {
    app,
    demo,
    t,
    now: () => 1234.5,
    toast: (m) => calls.toast.push(m),
    render: () => {
      calls.render += 1;
    },
    sendScript: (...a) => sendScript(ctx, ...a),
  };
  return { app, ctx, calls };
}

test('sendScript: the key is pending while the request runs, and the page redraws on both sides', async () => {
  const { app, ctx, calls } = harness();
  assert.equal(await sendScript(ctx, 'ACEK_STOP', null, { key: 'k' }), true);
  assert.deepEqual(calls.gcode, ['ACEK_STOP']);
  assert.deepEqual(calls.pending, [['k']]);
  assert.equal(app.pending.size, 0);
  assert.equal(calls.render, 2);
});

test('sendScript: once asks the connection for one request per key', async () => {
  const { ctx, calls } = harness();
  assert.equal(await sendScript(ctx, 'GO', null, { key: 'print:go', once: true }), true);
  assert.deepEqual(calls.once, [['print:go', 'GO']]);
  assert.deepEqual(calls.gcode, []);
});

test('sendScript: the demo shows the script in a toast and sends nothing', async () => {
  const { app, ctx, calls } = harness({ demo: true });
  assert.equal(await sendScript(ctx, 'ACEK_STOP', null, { key: 'k' }), true);
  assert.deepEqual(calls.toast, ['conn.demo: ACEK_STOP']);
  assert.deepEqual([calls.gcode, calls.render, app.pending.size], [[], 0, 0]);
});

test("sendScript: a refusal is toasted and filed under the lane of the page's own unit", async () => {
  const { app, ctx, calls } = harness({ reject: 'busy' });
  assert.equal(await sendScript(ctx, 'X', 2, { key: 'k' }), false);
  assert.deepEqual(calls.toast, ['toast.refused {"msg":"busy"}']);
  assert.deepEqual(
    app.events.map((e) => [e.k, e.lvl, e.src, e.msg]),
    [['e', 'err', 'lane2', 'busy']],
  );
  assert.equal(app.pending.size, 0);
  assert.equal(calls.render, 2);
});

test("sendScript: another unit's lane, or no lane, is filed under the unit", async () => {
  const { app, ctx } = harness({ reject: 'busy', units: ['ace2k a', 'ace2k b'] });
  await sendScript(ctx, 'X', 2, { key: 'k', unit: 'b' });
  assert.deepEqual(
    app.events.map((e) => [e.src, e.t]),
    [['unit', 1234.5]],
  );
  let n = 0;
  app.conn.gcode = async () => {
    n += 1;
    throw new Error(`other ${n}`);
  };
  await sendScript(ctx, 'X', 2, { key: 'k', unit: 'a' }); // a lane of the first unit
  await sendScript(ctx, 'X', null, { key: 'k', unit: 'a' }); // no lane, so the unit
  assert.deepEqual(
    app.events.map((e) => e.src),
    ['unit', 'lane2', 'unit'],
  );
});

test('sendScript: a refusal the caller handles (quiet) is shown nowhere', async () => {
  const { app, ctx, calls } = harness({ reject: 'changed' });
  assert.equal(await sendScript(ctx, 'X', null, { key: 'k', quiet: (m) => m === 'changed' }), false);
  assert.deepEqual([calls.toast, app.events], [[], []]);
  await sendScript(ctx, 'X', null, { key: 'k', quiet: () => false });
  assert.equal(calls.toast.length, 1);
});

test('send: builds the G-code, scopes the key by unit with several, and hands it to sendScript', async () => {
  const { ctx, calls } = harness();
  assert.equal(await send(ctx, 'stop', 1, {}), true);
  assert.equal(calls.gcode.length, 1);
  assert.match(calls.gcode[0], /^ACEK_STOP/);
  assert.deepEqual(calls.pending, [['stop:1']]);
  const two = harness({ units: ['ace2k a', 'ace2k b'] });
  await send(two.ctx, 'stop', 1, {}, { unit: 'b' });
  assert.deepEqual(two.calls.pending, [['b|stop:1']]);
  assert.match(two.calls.gcode[0], /UNIT=b/);
});

test('send: a script that cannot be built is refused with a toast, and nothing is sent', async () => {
  const { ctx, calls } = harness();
  assert.equal(await send(ctx, 'no_such_action', 1, {}), false);
  assert.equal(calls.toast.length, 1);
  assert.match(calls.toast[0], /^toast\.refused/);
  assert.deepEqual(calls.gcode, []);
});

test('built: the script, or null after the refusal toast', () => {
  const { ctx, calls } = harness();
  assert.equal(
    built(ctx, () => 'X'),
    'X',
  );
  assert.equal(
    built(ctx, () => {
      throw new Error('bad');
    }),
    null,
  );
  assert.deepEqual(calls.toast, ['toast.refused {"msg":"bad"}']);
});

test('built: a refusal the page makes is said through the catalogue, with its values', () => {
  const { ctx, calls } = harness();
  for (const err of [new RefusedError('length_max', { max: 2000 }), new RefusedError('length_min')])
    assert.equal(
      built(ctx, () => {
        throw err;
      }),
      null,
    );
  assert.deepEqual(calls.toast, [
    'toast.refused {"msg":"refuse.length_max {\\"max\\":2000}"}',
    'toast.refused {"msg":"refuse.length_min {}"}',
  ]);
});
