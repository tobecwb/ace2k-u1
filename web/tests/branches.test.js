// The small branches of the pure modules that the feature tests pass by: missing values and empty plans.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { esc } from '../js/esc.js';
import { setCatalog, t } from '../js/i18n.js';
import { planScript, planId, swapKinds, nextOptions, chosen, blocked } from '../js/print-plan.js';
import { startPrint } from '../js/printflow.js';
import { send, sendScript } from '../js/send.js';
import { loadEnglish } from './helpers.js';

test('esc: nothing becomes an empty string, specials are escaped', () => {
  assert.equal(esc(null), '');
  assert.equal(esc(undefined), '');
  assert.equal(esc(0), '0');
  assert.equal(esc('<a href="x">&\'</a>'), '&lt;a href=&quot;x&quot;&gt;&amp;&#39;&lt;/a&gt;');
});

test('setCatalog: no catalogue leaves the keys as they are', () => {
  setCatalog({ 'k.x': 'text' });
  assert.equal(t('k.x'), 'text');
  setCatalog(null);
  assert.equal(t('k.x'), 'k.x');
  setCatalog(undefined);
  assert.equal(t('k.x'), 'k.x');
});

test('planScript refuses a missing name; planId of no plan is null', () => {
  loadEnglish();
  assert.throws(() => planScript(null), { message: t('print.bad_name', { name: '' }) });
  assert.throws(() => planScript(undefined), /./);
  assert.equal(planId(null), null);
  assert.equal(planId({ file: 'a.gcode', mtime: 3 }), 'a.gcode\n3');
});

test('swapKinds: a tool with no lane, or a lane with no head, counts for nothing', () => {
  const headOf = (key) => ({ 'a:1': 0, 'a:2': 0 })[key] ?? null;
  const none = { stops: 0, free: 0 };
  assert.deepEqual(swapKinds([0, 1, 2, 0], { 0: 'a:1', 1: 'a:2', 3: 'a:9' }, headOf), { stops: 2, free: 0 });
  assert.deepEqual(swapKinds([5], {}, headOf), none);
  assert.deepEqual(swapKinds([0], { 0: null }, headOf), none);
});

test('nextOptions: a head ticked on joins the heads, unticked leaves; the last one off turns flow off', () => {
  const on = nextOptions({ flow: true, flowHeads: [0] }, { opt: 'flow_head', on: true, head: '2' }, [0, 2]);
  assert.deepEqual(on.flowHeads, [0, 2]);
  const off = nextOptions({ flow: true, flowHeads: [0, 2] }, { opt: 'flow_head', on: false, head: '2' }, [0, 2]);
  assert.deepEqual([off.flow, off.flowHeads], [true, [0]]);
  const none = nextOptions({ flow: true, flowHeads: [0] }, { opt: 'flow_head', on: false, head: '0' }, [0, 2]);
  assert.deepEqual([none.flow, none.flowHeads], [false, []]);
});

test('no plan: nothing chosen, nothing blocked', () => {
  assert.deepEqual(chosen(null, {}), {});
  assert.deepEqual(blocked(null, {}, []), { missing: [], wrong: [], unset: [] });
});

function printCtx(plan) {
  const sent = [];
  const app = {
    online: true,
    klippy: 'ready',
    pending: new Set(),
    print: { open: true, file: plan.file, choice: {}, choiceId: null },
    printOptions: {},
    ui: { tab: 'print', panel: null, sheet: false },
    status: { ace2k_u1: { hooked: true, plan }, ace2k: { lanes: [] } },
  };
  return {
    sent,
    ctx: {
      app,
      demo: false,
      t,
      toast() {},
      render() {},
      sendScript: async (script) => {
        sent.push(script);
        return true;
      },
    },
  };
}

test('startPrint: a plan that reports no lanes blocks its tool with a lane; one with no tools starts', async () => {
  const blockedOne = printCtx({ file: 'p.gcode', mtime: 1, tools: [{ lane: 'a:1', material: 'PLA' }] });
  await startPrint(blockedOne.ctx);
  assert.deepEqual(blockedOne.sent, []);
  const empty = printCtx({ file: 'q.gcode', mtime: 1, tools: [] });
  await startPrint(empty.ctx);
  assert.equal(empty.sent.length, 1);
  assert.match(empty.sent[0], /^ACEK_PLAN_SET/);
});

test('send: the default key of an action with no lane has no number', async () => {
  const pending = [];
  const app = {
    pending: new Set(),
    events: [],
    status: { ace2k: { lanes: [] } },
    conn: {
      gcode: async () => {
        pending.push([...app.pending]);
      },
    },
  };
  const ctx = {
    app,
    demo: false,
    t,
    now: () => 1,
    toast() {},
    render() {},
    sendScript: (...a) => sendScript(ctx, ...a),
  };
  assert.equal(await send(ctx, 'stop_all', null, {}), true);
  assert.deepEqual(pending, [['stop_all:']]);
});

test('sendScript: the key is released and the page redrawn even when the refusal handling itself throws', async () => {
  let renders = 0;
  const app = {
    pending: new Set(),
    events: [],
    status: { ace2k: { lanes: [] } },
    conn: {
      gcode: async () => {
        throw new Error('busy');
      },
    },
  };
  const ctx = {
    app,
    demo: false,
    t,
    now: () => 1,
    toast() {
      throw new Error('toast failed');
    },
    render() {
      renders += 1;
    },
  };
  await assert.rejects(sendScript(ctx, 'X', null, { key: 'k' }), /toast failed/);
  assert.deepEqual([app.pending.size, renders], [0, 2]);
});
