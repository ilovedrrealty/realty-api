const { test } = require('node:test')
const assert = require('node:assert/strict')
const { readFileSync } = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const React = require('react')
const jsxRuntime = require('react/jsx-runtime')
const { renderToStaticMarkup } = require('react-dom/server')

const src = path.resolve(__dirname, '../apps/dashboard/src')
const source = { kind: 'private', id: '11111111-1111-4111-8111-111111111111' }
const listing = { ...source, kind: 'listing' }
const id = '22222222-2222-4222-8222-222222222222'
const submission_id = '33333333-3333-4333-8333-333333333333'
const base = `/property-network/maintenance-reminders/private/${source.id}`
const settings = { enabled: false, lead_days: 7, version: 0 }
const item = { id, title: 'Clean pool filter', repeat: 'monthly', anchor_on: '2026-11-12', due_on: '2026-11-12', snoozed_until: null,
  status: 'active', version: 1, attention: 'none' }
const page = { source, as_of: '2026-11-12', time_zone: 'America/Santo_Domingo', preference: settings,
  items: [item], total: 1, page: 1, page_size: 30 }
const plain = value => JSON.parse(JSON.stringify(value))
const never = () => assert.fail('Rendering cannot perform a maintenance reminder write')

class ServiceContractError extends Error {}
class ServiceInputError extends ServiceContractError {}

const validCalendarDate = value => /^\d{4}-\d{2}-\d{2}$/.test(value) && value.slice(0, 4) !== '0000'
  && Number.isFinite(Date.parse(value)) && new Date(`${value}T12:00:00Z`).toISOString().slice(0, 10) === value

function compile(file) {
  return ts.transpileModule(readFileSync(path.join(src, file), 'utf8'), { compilerOptions: {
    target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS, esModuleInterop: false, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText
}

function load(code, dependencies) {
  const exports = {}
  vm.runInNewContext(code, { exports, require(name) {
    assert.ok(Object.hasOwn(dependencies, name), `Unexpected dependency ${name}`)
    return dependencies[name]
  } })
  return exports
}

const apiSource = compile('api/maintenanceReminders.ts')
const panelSource = compile('components/network/MaintenanceReminders.tsx')

function harness(data, status = 200) {
  const calls = []
  let response
  function reply(body, code = 200, cache = 'no-store') { response = { data: body, status: code, headers: { 'cache-control': cache } } }
  reply(data, status)
  const client = Object.fromEntries(['get', 'post', 'put'].map(method => [method, async (...args) => {
    calls.push([method, ...args])
    return response
  }]))
  const api = load(apiSource, { './axios': { default: client }, './propertyManagement': { validCalendarDate },
    './serviceWorkflow': { ServiceContractError, ServiceInputError } })
  return { api, calls, reply }
}

test('maintenance reads authenticate each exact source and require Santo Domingo dates and no-store', async () => {
  const h = harness(page)
  const signal = new AbortController().signal
  assert.deepEqual(plain(await h.api.getMaintenanceReminders(source, 1, signal)), page)
  assert.deepEqual(plain(h.calls[0].slice(0, 2)), ['get', base])
  assert.equal(h.calls[0][2].withCredentials, true)
  assert.equal(h.calls[0][2].signal, signal)
  assert.deepEqual(plain(h.calls[0][2].params), { page: 1 })
  h.reply({ ...page, source: listing })
  assert.deepEqual(plain(await h.api.getMaintenanceReminders(listing, 1)), { ...page, source: listing })
  for (const bad of [
    { ...page, source: listing }, { ...page, source: { ...source, id } }, { ...page, time_zone: 'UTC' },
    { ...page, as_of: '2026-11-31' }, { ...page, page: 2 }, { ...page, page_size: 50 },
    { ...page, items: [{ ...item, address: 'Private address' }] },
    { ...page, items: [{ ...item, attention: 'due' }] },
    { ...page, items: [item, item], total: 2 }, { ...page, preference: { ...settings, enabled: true } },
  ]) {
    h.reply(bad)
    await assert.rejects(h.api.getMaintenanceReminders(source, 1), ServiceContractError)
  }
  h.reply(page, 200, 'private, max-age=30')
  await assert.rejects(h.api.getMaintenanceReminders(source, 1), ServiceContractError)
})

test('only explicit preference and schedule commands send exact declared fields', async () => {
  const h = harness({ enabled: true, lead_days: 3, version: 1 })
  const preferences = { source, submission_id, operation: 'preferences', expected_version: 0, enabled: true, lead_days: 3 }
  assert.deepEqual(plain(await h.api.runReminderIntent(preferences)), { operation: 'preferences', result: { enabled: true, lead_days: 3, version: 1 } })
  assert.deepEqual(plain(h.calls[0].slice(0, 3)), ['put', `${base}/preferences`, { submission_id, expected_version: 0, enabled: true, lead_days: 3 }])
  const create = { source, submission_id, operation: 'create', title: item.title, repeat: item.repeat, first_due_on: item.anchor_on }
  h.reply(item, 201)
  assert.deepEqual(plain(await h.api.runReminderIntent(create)), { operation: 'create', result: item })
  assert.deepEqual(plain(h.calls[1].slice(0, 3)), ['post', base, { submission_id, title: item.title, repeat: item.repeat, first_due_on: item.anchor_on }])
  const snooze = { source, submission_id, operation: 'snooze', schedule_id: id, expected_version: 1, snoozed_until: '2026-11-13' }
  h.reply({ ...item, version: 2, snoozed_until: snooze.snoozed_until })
  await h.api.runReminderIntent(snooze)
  assert.deepEqual(plain(h.calls[2].slice(0, 3)), ['post', `${base}/${id}/actions`, { submission_id, expected_version: 1, operation: 'snooze', snoozed_until: '2026-11-13' }])
  const pause = { source, submission_id, operation: 'pause', schedule_id: id, expected_version: 2 }
  h.reply({ ...item, version: 3, status: 'paused' })
  await h.api.runReminderIntent(pause)
  assert.deepEqual(plain(h.calls[3].slice(0, 3)), ['post', `${base}/${id}/actions`, { submission_id, expected_version: 2, operation: 'pause' }])
})

test('invalid types, dates, version and repeat values are stopped before transport', async () => {
  const h = harness(null)
  const create = { source, submission_id, operation: 'create', title: item.title, repeat: item.repeat, first_due_on: item.anchor_on }
  for (const intent of [
    { ...create, title: null }, { ...create, title: '   ' }, { ...create, title: 'a\u0000b' }, { ...create, title: '🚪'.repeat(181) },
    { ...create, repeat: 'daily' }, { ...create, first_due_on: '2026-02-29' }, { ...create, source: { kind: 'project', id } },
    { source, submission_id, operation: 'preferences', expected_version: 0, enabled: true, lead_days: 31 },
    { source, submission_id, operation: 'snooze', expected_version: 0, schedule_id: id, snoozed_until: '2026-11-13' },
  ]) await assert.rejects(h.api.runReminderIntent(intent), ServiceInputError)
  assert.deepEqual(h.calls, [])
  assert.equal(h.api.normalizeReminderTitle('\u00a0Pool filter\t'), 'Pool filter')
  assert.equal(h.api.normalizeReminderTitle('🚪'.repeat(180)).length, 360)
})

test('own receipts bind the exact source, submission, operation and schedule while accepting later versions', async () => {
  const h = harness({ operation: 'complete', result: { ...item, version: 4, status: 'stopped', due_on: '2026-12-12' } })
  const intent = { source, submission_id, operation: 'complete', schedule_id: id, expected_version: 1 }
  assert.equal((await h.api.resolveReminderIntent(intent)).result.status, 'stopped')
  assert.deepEqual(plain(h.calls[0].slice(0, 2)), ['get', `${base}/submissions/${submission_id}`])
  for (const bad of [
    { operation: 'stop', result: { ...item, version: 2 } },
    { operation: 'complete', result: { ...item, id: source.id, version: 2 } },
    { operation: 'complete', result: item },
    { operation: 'complete', result: { ...item, version: 2, status: 'paused' } },
  ]) {
    h.reply(bad)
    await assert.rejects(h.api.resolveReminderIntent(intent), ServiceContractError)
  }
})

test('active and paused schedules offer only their available actions', () => {
  const { api } = harness(null)
  assert.deepEqual(plain(api.reminderActions(item)), ['complete', 'snooze', 'pause', 'stop'])
  assert.deepEqual(plain(api.reminderActions({ ...item, status: 'paused' })), ['resume', 'stop'])
  for (const status of ['completed', 'stopped']) assert.deepEqual(plain(api.reminderActions({ ...item, status })), [])
})

function renderPanel(data, error, language) {
  const labels = JSON.parse(readFileSync(path.join(src, `locale/${language}/property_network.json`), 'utf8'))
  const t = key => {
    const label = key.split('.').reduce((entry, part) => entry?.[part], labels)
    assert.equal(typeof label, 'string', `Missing ${language} translation ${key}`)
    return label
  }
  const { MaintenanceReminders } = load(panelSource, {
    react: React, 'react/jsx-runtime': jsxRuntime, 'react-i18next': { useTranslation: () => ({ t }) },
    '../../api/maintenanceReminders': harness(data).api,
    '../../api/propertyManagement': { validCalendarDate },
    '../../hooks/useNetworkRead': { useNetworkRead: () => ({ data, error, checking: false, stale: false, refresh: never }) },
    './NetworkShared': {
      NetworkPanel: ({ title, children, headingId }) => React.createElement('section', { 'aria-labelledby': headingId }, React.createElement('h2', { id: headingId }, title), children),
      NetworkFreshness: () => null, NetworkDate: ({ value }) => React.createElement('time', { dateTime: value }, value),
      NetworkPagination: () => null, useNetworkDraftGuard() {}, networkError: () => 'error_access',
    },
    './ServiceWorkflowShared': { useServiceAttempt: () => ({ pending: null, busy: false, denied: false, send: never }), ServiceMutationStatus: () => null },
  })
  return renderToStaticMarkup(React.createElement(MaintenanceReminders, { source }))
}

test('both languages render default-off reminders and clear content after access denial', () => {
  for (const [language, title, enable] of [['en', 'Maintenance reminders', 'Enable in-app reminders'],
    ['es', 'Recordatorios de mantenimiento', 'Activar recordatorios en el panel']]) {
    const markup = renderPanel(page, null, language)
    assert.match(markup, new RegExp(title))
    assert.match(markup, new RegExp(enable))
    assert.match(markup, /<input[^>]*type="checkbox"[^>]*>/)
    assert.doesNotMatch(markup, /checked=|Due today|Vence hoy/)
    assert.match(markup, /<time dateTime="2026-11-12">/i)
    assert.equal(renderPanel(page, { response: { status: 404 } }, language), '')
  }
})
