const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')

const source = fs.readFileSync(path.join(__dirname, '../apps/dashboard/src/lib/networkFreshness.ts'), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS } }).outputText
const exported = {}
vm.runInNewContext(compiled, { exports: exported, AbortController, Promise, Error })
const flush = async () => { for (let tick = 0; tick < 12; tick++) await Promise.resolve() }
function harness(load, initial = {}) {
  let now = 1000, next = 0, visible = initial.visible ?? true, online = initial.online ?? true
  const timers = new Map(), calls = [], data = [], states = [], errors = []
  const loop = exported.createNetworkReadLoop({
    load: signal => { calls.push(signal); return load(signal) }, visible: () => visible, online: () => online, now: () => now,
    setTimer: (callback, delay) => { const id = ++next; timers.set(id, { callback, due: now + delay }); return id }, clearTimer: id => timers.delete(id),
    onData: value => data.push(value), onState: state => states.push(state), onError: error => { errors.push(error); return initial.fatal ?? false },
  })
  return { loop, calls, data, states, errors, timers,
    visible(value) { visible = value }, online(value) { online = value },
    async advance(amount) {
      const end = now + amount
      for (let count = 0; count < 100; count++) {
        const due = [...timers.entries()].filter(([, timer]) => timer.due <= end).sort((a, b) => a[1].due - b[1].due)[0]
        if (!due) { now = end; await flush(); return }
        now = due[1].due; timers.delete(due[0]); due[1].callback(); await flush()
      }
      assert.fail('Unbounded timer scheduling')
    },
  }
}

test('another participant update and progress appear after the 30-second read without writes', async () => {
  let server = { updates: ['first'], percent: 0 }
  const h = harness(async () => ({ updates: [...server.updates], percent: server.percent }))
  try {
    await flush(); assert.equal(h.calls.length, 1)
    server = { updates: ['second participant', 'first'], percent: 50 }
    await h.advance(29999); assert.equal(h.calls.length, 1)
    await h.advance(1); assert.equal(h.calls.length, 2)
    assert.equal(h.data.at(-1).updates[0], 'second participant'); assert.equal(h.data.at(-1).percent, 50)
    assert.equal(h.states.at(-1).checkedAt, 31000); assert.equal(h.states.at(-1).stale, false)
  } finally { h.loop.dispose() }
})

test('overlapping focus/manual wakeups coalesce to one queued read and never overlap requests', async () => {
  const pending = []
  const h = harness(() => new Promise(resolve => pending.push(resolve)))
  try {
    await flush(); h.loop.refresh(); h.loop.refresh(); h.loop.refresh(); await flush()
    assert.equal(h.calls.length, 1)
    pending.shift()('first'); await flush(); assert.equal(h.calls.length, 2)
    pending.shift()('latest'); await flush(); assert.equal(h.data.at(-1), 'latest')
    assert.equal(h.timers.size, 1)
  } finally { h.loop.dispose() }
})

test('hidden/offline pages do not read; pause aborts and late data cannot replace the next visible read', async () => {
  const pending = [], h = harness(() => new Promise(resolve => pending.push(resolve)), { visible: false })
  try {
    await flush(); await h.advance(60000); assert.equal(h.calls.length, 0)
    h.visible(true); h.online(false); h.loop.refresh(); await flush(); assert.equal(h.calls.length, 0)
    h.online(true); h.loop.refresh(); await flush(); assert.equal(h.calls.length, 1)
    h.visible(false); h.loop.pause(); assert.equal(h.calls[0].aborted, true); assert.equal(h.timers.size, 0)
    pending.shift()('late private data'); await flush(); assert.equal(h.data.length, 0)
    h.visible(true); h.loop.refresh(); await flush(); pending.shift()('visible current data'); await flush()
    assert.deepEqual(h.data, ['visible current data'])
  } finally { h.loop.dispose() }
})

test('disposing an old ID/page aborts its request and ignores its late result after the new context loads', async () => {
  let oldResolve
  const old = harness(() => new Promise(resolve => { oldResolve = resolve }))
  await flush(); old.loop.dispose(); assert.equal(old.calls[0].aborted, true)
  const current = harness(async () => 'new ID page')
  try {
    await flush(); oldResolve('old ID page'); await flush()
    assert.equal(old.data.length, 0); assert.equal(old.timers.size, 0); assert.equal(current.data.at(-1), 'new ID page')
  } finally { current.loop.dispose() }
})

test('request deadline aborts, marks stale and schedules only a later read', async () => {
  const h = harness(signal => new Promise((resolve, reject) => signal.addEventListener('abort', () => reject(new Error('Synthetic abort')), { once: true })))
  try {
    await flush(); await h.advance(15000)
    assert.equal(h.calls[0].aborted, true); assert.equal(h.errors.length, 1); assert.equal(h.states.at(-1).stale, true)
    await h.advance(29999); assert.equal(h.calls.length, 1)
    await h.advance(1); assert.equal(h.calls.length, 2)
  } finally { h.loop.dispose() }
})

test('authorization denial stops recurring reads and cleanup prevents any subsequent callback', async () => {
  const h = harness(async () => { throw new Error('Synthetic denied') }, { fatal: true })
  await flush(); assert.equal(h.errors.length, 1); assert.equal(h.timers.size, 0)
  h.loop.refresh(); await h.advance(120000); assert.equal(h.calls.length, 1)
  const count = h.states.length; h.loop.dispose(); await flush(); assert.equal(h.states.length, count)
})

test('unmount before the initial microtask never starts the request', async () => {
  const h = harness(async () => 'not requested')
  h.loop.dispose(); await flush(); assert.equal(h.calls.length, 0); assert.equal(h.timers.size, 0)
})

test('English and Spanish freshness copy names a last check and a stale state, without push claims', () => {
  for (const language of ['en', 'es']) {
    const copy = JSON.parse(fs.readFileSync(path.join(__dirname, `../apps/dashboard/src/locale/${language}/property_network.json`), 'utf8'))
    for (const key of ['freshness_waiting', 'freshness_checked', 'freshness_checking', 'freshness_stale']) assert.equal(typeof copy[key], 'string')
    assert.ok(copy.freshness_checked.includes('{{time}}')); assert.ok(!/real.?time|push|tiempo real/i.test(copy.freshness_checked))
    assert.ok(copy.freshness_stale.length > 10)
  }
})
