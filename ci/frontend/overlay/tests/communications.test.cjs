// UNRUN author definitions. Actual TS/TSX with synthetic axios/hooks + React SSR;
// no production client, authenticated session, worker, recipient or provider.
const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const React = require('react')
const runtime = require('react/jsx-runtime')
const { renderToStaticMarkup } = require('react-dom/server')
const root = path.resolve(__dirname, '../apps/dashboard/src')
const clone = value => JSON.parse(JSON.stringify(value))
const ID = '10000000-0000-4000-8000-000000000001'
const TIME = '2026-11-01T12:00:00Z'
const job = changes => ({ id: ID, event_kind: 'signup', channel: 'email', provider: 'resend', step: 'customer_email', status: 'pending', attempts: 0, max_attempts: 4, created_at: TIME, first_attempt_at: null, last_attempt_at: null, next_attempt_at: TIME, accepted_at: null, error_code: null, outcome_uncertain: false, can_retry: false, ...changes })
const report = (items = [job()], changes = {}) => ({ total: items.length, limit: 25, offset: 0, enabled: false, customer_messages_enabled: false, items, note: 'Provider acceptance is not inbox delivery.', ...changes })
const eligible = () => job({ status: 'failed', attempts: 1, first_attempt_at: TIME, last_attempt_at: TIME, error_code: 'resend_http_429', can_retry: true })
const compiled = file => ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText

function clientFixture(data, error) {
  const calls = [], module = { exports: {} }
  const axios = Object.fromEntries(['get', 'post'].map(method => [method, async (...args) => { calls.push({ method, args }); if (error) throw error; return { data } }]))
  vm.runInNewContext(compiled('api/communications.ts'), { module, exports: module.exports, require: name => { assert.equal(name, './axios'); return { __esModule: true, default: axios } } })
  return { ...module.exports, calls }
}

test('metadata client preserves bounded filters, cancellation and both server flags, without payload extras', async () => {
  const f = clientFixture(report([eligible()], { limit: 2, offset: 25, total: 40 })), signal = new AbortController().signal
  const value = await f.getCommunications({ event_kind: 'signup', status: 'failed', limit: 2, offset: 25 }, signal)
  assert.deepEqual(clone(f.calls[0].args[1].params), { limit: 2, offset: 25, status: 'failed', event_kind: 'signup' })
  assert.equal(f.calls[0].args[0], '/admin/communications')
  assert.equal(f.calls[0].args[1].signal, signal)
  assert.equal(value.enabled, false); assert.equal(value.customer_messages_enabled, false)
  assert.equal(value.items[0].event_kind, 'signup')
  assert.equal('note' in value, false)
})

test('client refuses invalid filters before any request and malformed reports instead of fake empty data', async () => {
  for (const filters of [{ offset: 10001 }, { limit: 101 }, { offset: -1 }, { limit: 1.5 }, { status: 'delivered' }, { event_kind: 'historic' }]) {
    const f = clientFixture(report())
    await assert.rejects(f.getCommunications(filters), /Invalid communications filters/)
    assert.equal(f.calls.length, 0)
  }
  for (const data of [{}, report([], { enabled: 'false' }), report([job(), job()]), report([job({ status: 'delivered' })]), report([job({ step: 'crm_contact' })]), report([job({ event_kind: 'historic' })]), report([job({ channel: 'sms' })])]) {
    await assert.rejects(clientFixture(data).getCommunications(), /Communications unavailable/)
  }
  assert.equal((await clientFixture(report([])).getCommunications()).items.length, 0)
})

test('client denies customer content/identifiers and unsafe retry flags while retaining accepted as accepted', async () => {
  for (const extras of [{ recipient: 'synthetic@example.invalid' }, { body: 'synthetic' }, { user_id: ID }, { lead_id: ID }, { error_code: 'synthetic@example.invalid' }, { outcome_uncertain: true, can_retry: true }, { status: 'accepted', accepted_at: TIME, attempts: 1, can_retry: true }, { attempts: 4, can_retry: true }]) {
    await assert.rejects(clientFixture(report([job({ ...eligible(), ...extras })])).getCommunications(), /Communications unavailable/)
  }
  const f = clientFixture(report([job({ status: 'accepted', accepted_at: TIME, attempts: 1, first_attempt_at: TIME, last_attempt_at: TIME, arbitrary_extra: 'not projected' })]))
  const result = await f.getCommunications()
  assert.equal(result.items[0].status, 'accepted')
  assert.equal('arbitrary_extra' in result.items[0], false)
})

test('retry is a single explicit JSON authorization preserving paused state; errors never resend', async () => {
  const f = clientFixture({ id: ID, status: 'pending', enabled: false })
  assert.equal((await f.authorizeCommunicationRetry(ID.toUpperCase())).enabled, false)
  assert.equal(f.calls.length, 1)
  assert.deepEqual(clone(f.calls[0]), { method: 'post', args: [`/admin/communications/${ID}/retry`, {}] })
  for (const [error, code] of [[{ response: { status: 409 } }, 'not_retryable'], [Error('synthetic response loss'), 'unconfirmed']]) {
    const failure = clientFixture(undefined, error)
    await assert.rejects(failure.authorizeCommunicationRetry(ID), value => value.code === code)
    assert.equal(failure.calls.length, 1)
  }
  const invalid = clientFixture()
  await assert.rejects(invalid.authorizeCommunicationRetry('invalid'), value => value.code === 'unconfirmed')
  assert.equal(invalid.calls.length, 0)
})

// Bounded component-local hook adapter. Only the actual page and its authorized
// child exist here; effects and event callbacks come from the actual TSX bytes.
function ui(responses, language = 'en', props = { user: { role: 'admin' }, workspace: 'Admin' }) {
  const translations = JSON.parse(fs.readFileSync(path.join(root, `locale/${language}/communications.json`), 'utf8'))
  const calls = [], frames = new Map(), wrappers = new WeakMap(), effects = []
  let current, tree, html = '', dirty = false, now = Date.parse(TIME), sequence = 0
  const t = (key, values = {}) => { const value = key.split('.').reduce((part, item) => part?.[item], translations); assert.equal(typeof value, 'string', `Missing ${language} ${key}`); return value.replace(/\{\{(\w+)\}\}/g, (_, name) => String(values[name] ?? '')) }
  function slot(kind) { const n = current.cursor++; if (!current.slots[n]) current.slots[n] = { kind }; assert.equal(current.slots[n].kind, kind); return current.slots[n] }
  const hooks = { ...React,
    useState(initial) { const state = slot('state'); if (!Object.hasOwn(state, 'value')) state.value = typeof initial === 'function' ? initial() : initial; return [state.value, next => { state.value = typeof next === 'function' ? next(state.value) : next; dirty = true }] },
    useRef(initial) { const state = slot('ref'); if (!Object.hasOwn(state, 'value')) state.value = { current: initial }; return state.value },
    useEffect(callback, deps) { const state = slot('effect'); if (!state.deps || deps.some((value, n) => value !== state.deps[n])) { state.deps = [...deps]; effects.push({ state, callback }) } },
  }
  function wrap(type) { if (!wrappers.has(type)) { const id = ++sequence; const Wrapper = received => { const previous = current; if (!frames.has(id)) frames.set(id, { cursor: 0, slots: [] }); current = frames.get(id); current.cursor = 0; try { current.tree = type(received); return current.tree } finally { current = previous } }; Wrapper.frameId = id; wrappers.set(type, Wrapper) } return wrappers.get(type) }
  const jsx = (type, received, key) => runtime.jsx(typeof type === 'function' ? wrap(type) : type, received, key)
  const jsxs = (type, received, key) => runtime.jsxs(typeof type === 'function' ? wrap(type) : type, received, key)
  class RetryError extends Error { constructor(code) { super(code); this.code = code } }
  const api = { COMMUNICATION_STATUSES: ['pending', 'processing', 'retry_wait', 'accepted', 'failed', 'needs_review', 'skipped'], CommunicationRetryError: RetryError }
  for (const name of ['getCommunications', 'authorizeCommunicationRetry']) api[name] = (...args) => { calls.push({ name, args }); assert.equal(typeof responses[name], 'function', `Unexpected ${name}`); return responses[name](...args) }
  const module = { exports: {} }
  class Clock extends Date { static now() { return now } }
  vm.runInNewContext(compiled('pages/admin/Communications.tsx'), { module, exports: module.exports, Date: Clock, Intl, AbortController, require(name) {
    if (name === 'react') return hooks
    if (name === 'react/jsx-runtime') return { ...runtime, jsx, jsxs }
    if (name === 'react-i18next') return { useTranslation: () => ({ t, i18n: { language } }) }
    assert.equal(name, '../../api/communications'); return api
  } })
  function render() { dirty = false; tree = jsx(module.exports.Communications, props); html = renderToStaticMarkup(tree); return html }
  async function settle() { for (let n = 0; n < 12; n++) { render(); for (const entry of effects.splice(0)) { entry.state.cleanup?.(); entry.state.cleanup = entry.callback() } for (let tick = 0; tick < 5; tick++) await Promise.resolve(); if (!dirty && !effects.length) return html } assert.fail('Component did not settle') }
  function nodes() { const found = []; function visit(value) { if (Array.isArray(value)) return value.forEach(visit); if (!React.isValidElement(value)) return; if (value.type.frameId) return visit(frames.get(value.type.frameId)?.tree); found.push(value); visit(value.props.children) } visit(tree); return found }
  function text(value) { return Array.isArray(value) ? value.map(text).join('') : React.isValidElement(value) ? value.type.frameId ? text(frames.get(value.type.frameId)?.tree) : text(value.props.children) : value == null ? '' : String(value) }
  function find(type, label) { const found = nodes().find(node => node.type === type && (typeof label === 'function' ? label(node) : text(node) === label)); assert.ok(found, `Missing ${type}: ${label}`); return found }
  return { calls, t, settle, render, find, nodes, get html() { return html }, text: () => text(tree), advance: ms => { now += ms }, RetryError,
    unmount() { for (const frame of frames.values()) for (const state of frame.slots) state.cleanup?.() } }
}

test('actual UI never fetches for non-Admin identity or a different Admin-selected workspace', async () => {
  for (const props of [{ user: { role: 'buyer' }, workspace: 'Admin' }, { user: { role: 'admin' }, workspace: 'Owner' }]) {
    const h = ui({}, 'en', props); await h.settle()
    assert.ok(h.text().includes(h.t('denied'))); assert.equal(h.calls.length, 0)
  }
})

test('actual EN/ES page reports paused flags and provider acceptance without messages or hidden recipients', async () => {
  for (const language of ['en', 'es']) {
    const h = ui({ getCommunications: async () => report([job({ status: 'accepted', attempts: 1, accepted_at: TIME })]) }, language)
    await h.settle()
    assert.ok(h.text().includes(h.t('accepted_note')))
    assert.ok(h.text().includes(h.t('statuses.accepted')))
    assert.ok(h.text().includes(h.t('flags.paused')))
    assert.ok(!h.nodes().some(node => node.type === 'input'))
    assert.equal(h.calls.length, 1)
    assert.ok(!h.text().includes('@'))
  }
})

test('actual load failure is unavailable, while bounded page navigation is an explicit read', async () => {
  const failed = ui({ getCommunications: async () => { throw Error('synthetic') } }); await failed.settle()
  assert.ok(failed.text().includes(failed.t('load_error')))
  assert.ok(!failed.text().includes(failed.t('empty')))
  const h = ui({ getCommunications: async filters => report([], { offset: filters.offset, total: 26 }) }); await h.settle()
  h.find('button', h.t('next')).props.onClick(); await h.settle()
  assert.equal(h.calls.length, 2)
  assert.equal(h.calls[1].args[0].offset, 25)
  assert.equal(h.calls[1].args[0].limit, 25)
  assert.equal(h.calls[0].args[1].aborted, true)
})

test('actual two-step retry uses one explicit action, synchronously blocks duplicates, preserves paused reply', async () => {
  let resolveRetry
  const h = ui({ getCommunications: async () => report([eligible()]), authorizeCommunicationRetry: () => new Promise(resolve => { resolveRetry = resolve }) })
  await h.settle()
  h.find('button', h.t('retry')).props.onClick(); await h.settle()
  assert.equal(h.calls.filter(call => call.name === 'authorizeCommunicationRetry').length, 0)
  const confirm = h.find('button', h.t('confirm.authorize'))
  const first = confirm.props.onClick(); confirm.props.onClick()
  assert.equal(h.calls.filter(call => call.name === 'authorizeCommunicationRetry').length, 1)
  resolveRetry({ id: ID, status: 'pending', enabled: false }); await first; await h.settle()
  assert.ok(h.text().includes(h.t('notices.authorized_paused')))
  assert.equal(h.calls.filter(call => call.name === 'authorizeCommunicationRetry').length, 1)
})

test('uncertain jobs have no retry control, stale authorization refreshes, response loss is not retried', async () => {
  const uncertain = ui({ getCommunications: async () => report([job({ status: 'needs_review', outcome_uncertain: true })]) }); await uncertain.settle()
  assert.ok(uncertain.text().includes(uncertain.t('uncertain')))
  assert.ok(!uncertain.nodes().some(node => node.type === 'button' && node.props.children === uncertain.t('retry')))
  const stale = ui({ getCommunications: async () => report([eligible()]) }); await stale.settle(); stale.advance(180001)
  stale.find('button', stale.t('retry')).props.onClick(); await stale.settle()
  assert.ok(stale.text().includes(stale.t('notices.stale')))
  assert.equal(stale.calls.filter(call => call.name === 'authorizeCommunicationRetry').length, 0)
  const lost = ui({ getCommunications: async () => report([eligible()]), authorizeCommunicationRetry: async () => { throw Error('synthetic lost response') } }); await lost.settle()
  lost.find('button', lost.t('retry')).props.onClick(); await lost.settle()
  await lost.find('button', lost.t('confirm.authorize')).props.onClick(); await lost.settle()
  assert.ok(lost.text().includes(lost.t('notices.unconfirmed')))
  assert.equal(lost.calls.filter(call => call.name === 'authorizeCommunicationRetry').length, 1)
})

test('a late stale report cannot replace the current filter, and unmount aborts the read', async () => {
  let resolveFirst
  const h = ui({ getCommunications: filters => filters.event_kind ? Promise.resolve(report([], { total: 0 })) : new Promise(resolve => { resolveFirst = resolve }) })
  await h.settle()
  h.find('select', node => node.props.value === '' && node.props.children[1]?.length === 2).props.onChange({ target: { value: 'signup' } })
  await h.settle()
  resolveFirst(report([job({ status: 'accepted', accepted_at: TIME, attempts: 1 })])); await h.settle()
  assert.ok(h.text().includes(h.t('empty')))
  assert.ok(!h.text().includes(h.t('reference', { id: ID })))
  assert.equal(h.calls[0].args[1].aborted, true)
  h.unmount(); assert.equal(h.calls[1].args[1].aborted, true)
})

test('EN/ES keys and interpolation agree; the new navigation target belongs only to Admin navigation', () => {
  function keys(value, prefix = '') { return Object.entries(value).flatMap(([key, item]) => typeof item === 'object' ? keys(item, prefix + key + '.') : [[prefix + key, [...item.matchAll(/\{\{(\w+)\}\}/g)].map(match => match[1]).sort()]]) }
  const en = JSON.parse(fs.readFileSync(path.join(root, 'locale/en/communications.json'), 'utf8'))
  const es = JSON.parse(fs.readFileSync(path.join(root, 'locale/es/communications.json'), 'utf8'))
  assert.deepEqual(keys(en), keys(es))
  const layout = fs.readFileSync(path.join(root, 'layouts/DashLayout.tsx'), 'utf8')
  const general = layout.slice(layout.indexOf('const GENERAL_NAV'), layout.indexOf('const ADMIN_NAV'))
  const admin = layout.slice(layout.indexOf('const ADMIN_NAV'), layout.indexOf('interface Props'))
  assert.equal(general.includes("view: 'communications'"), false)
  assert.equal((admin.match(/view: 'communications'/g) || []).length, 1)
  const dash = fs.readFileSync(path.join(root, 'pages/AdminDash.tsx'), 'utf8')
  assert.ok(dash.includes("case 'communications': return <Communications user={user} workspace=\"Admin\" />"))
  for (const language of ['en', 'es']) assert.equal(typeof JSON.parse(fs.readFileSync(path.join(root, `locale/${language}/nav.json`), 'utf8')).communications, 'string')
})
