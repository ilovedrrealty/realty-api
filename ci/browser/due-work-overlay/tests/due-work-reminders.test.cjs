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
const projectId = '11111111-1111-4111-8111-111111111111'
const itemId = '22222222-2222-4222-8222-222222222222'
const submission_id = '33333333-3333-4333-8333-333333333333'
const pathBase = `/property-network/projects/${projectId}/due-work-reminders`
const timeZone = 'America/Santo_Domingo'
const preference = { milestones_enabled: false, visits_enabled: false, lead_days: 7, version: 0 }
const empty = { project_id: projectId, as_of: '2026-11-12', time_zone: timeZone, preference, items: [], total: 0, page: 1, page_size: 30 }
const milestone = { kind: 'milestone', id: itemId, occurrence_version: 2, title: 'Clean pool filter', due_on: empty.as_of,
  starts_at: null, duration_minutes: null, state: 'active', snoozed_until: null, version: 0, attention: 'due' }
const visit = { ...milestone, kind: 'visit', title: 'Synthetic equipment visit', starts_at: '2026-11-13T01:30:00+00:00', duration_minutes: 60 }
const page = { ...empty, preference: { ...preference, milestones_enabled: true, visits_enabled: true, version: 1 }, items: [milestone, visit], total: 2 }
const plain = value => JSON.parse(JSON.stringify(value))
const never = () => assert.fail('Rendering must not write or send a reminder')

class ServiceContractError extends Error {}
class ServiceInputError extends ServiceContractError {}

const validCalendarDate = value => /^\d{4}-\d{2}-\d{2}$/.test(value) && value.slice(0, 4) !== '0000'
  && Number.isFinite(Date.parse(value)) && new Date(`${value}T12:00:00Z`).toISOString().slice(0, 10) === value
const todayInDR = date => new Intl.DateTimeFormat('sv-SE', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(date)
const validVisitMinutes = value => Number.isInteger(value) && value >= 15 && value <= 480

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

const apiSource = compile('api/dueWorkReminders.ts')
const panelSource = compile('components/network/DueWorkReminders.tsx')
const projectSource = compile('components/network/ProjectWorkspace.tsx')

function harness(initial) {
  const calls = []
  let response
  function reply(data, status = 200, cache = 'no-store') { response = { data, status, headers: { 'cache-control': cache } } }
  reply(initial)
  const client = Object.fromEntries(['get', 'post', 'put'].map(method => [method, async (...args) => { calls.push([method, ...args]); return response }]))
  const api = load(apiSource, { './axios': { default: client }, './propertyManagement': { todayInDR, validCalendarDate },
    './serviceWork': { validVisitMinutes, VISIT_TIME_ZONE: timeZone }, './serviceWorkflow': { ServiceContractError, ServiceInputError } })
  return { api, calls, reply }
}

test('due-work reads use the exact project and Santo Domingo projection for an overnight UTC visit', async () => {
  const h = harness(empty)
  const signal = new AbortController().signal
  assert.deepEqual(plain(await h.api.getDueWorkReminders(projectId, 1, signal)), empty)
  assert.deepEqual(plain(h.calls[0].slice(0, 2)), ['get', pathBase])
  assert.equal(h.calls[0][2].withCredentials, true)
  assert.equal(h.calls[0][2].signal, signal)
  h.reply(page)
  assert.deepEqual(plain(await h.api.getDueWorkReminders(projectId, 1)), page)
  for (const bad of [
    { ...page, project_id: itemId }, { ...page, time_zone: 'UTC' }, { ...page, page: 2 },
    { ...page, preference: { ...page.preference, milestones_enabled: false } },
    { ...page, items: [visit, { ...visit, occurrence_version: 3 }] },
    { ...page, items: [{ ...visit, due_on: '2026-11-13' }] },
    { ...page, items: [{ ...visit, occurrence_version: 0 }] },
    { ...page, items: [{ ...milestone, starts_at: visit.starts_at }] },
    { ...page, items: [{ ...visit, provider_email: 'synthetic@example.com' }] },
  ]) {
    h.reply(bad)
    await assert.rejects(h.api.getDueWorkReminders(projectId, 1), ServiceContractError)
  }
  h.reply(page, 200, 'private, max-age=30')
  await assert.rejects(h.api.getDueWorkReminders(projectId, 1), ServiceContractError)
})

test('only explicit personal preferences and exact occurrence actions are sent', async () => {
  const h = harness({ ...preference, milestones_enabled: true, version: 1 })
  const prefs = { projectId, submission_id, operation: 'preferences', expected_version: 0, milestones_enabled: true, visits_enabled: false, lead_days: 7 }
  await h.api.runDueWorkIntent(prefs)
  assert.deepEqual(plain(h.calls[0].slice(0, 3)), ['put', `${pathBase}/preferences`, { submission_id, expected_version: 0, milestones_enabled: true, visits_enabled: false, lead_days: 7 }])
  const snooze = { projectId, submission_id, operation: 'snooze', kind: 'milestone', id: itemId, occurrence_version: 2, expected_version: 0, snoozed_until: '2026-11-13' }
  h.reply({ ...milestone, version: 1, state: 'snoozed', attention: 'none', snoozed_until: '2026-11-13' })
  await h.api.runDueWorkIntent(snooze)
  assert.deepEqual(plain(h.calls[1].slice(0, 3)), ['post', `${pathBase}/milestone/${itemId}/actions`,
    { submission_id, expected_version: 0, occurrence_version: 2, operation: 'snooze', snoozed_until: '2026-11-13' }])
  for (const invalid of [{ ...snooze, occurrence_version: 0 }, { ...snooze, id: '../foreign' }, { ...snooze, snoozed_until: '2026-02-29' }, { ...prefs, lead_days: 31 }]) {
    await assert.rejects(h.api.runDueWorkIntent(invalid), ServiceInputError)
  }
  assert.equal(h.calls.length, 2)
})

test('receipts stay exact to the same occurrence and accept expired snoozes as active', async () => {
  const h = harness({ operation: 'snooze', result: { ...visit, version: 1, attention: 'overdue' } })
  const action = { projectId, submission_id, operation: 'snooze', kind: 'visit', id: itemId, occurrence_version: 2,
    expected_version: 0, snoozed_until: '2026-11-13' }
  assert.equal((await h.api.resolveDueWorkIntent(action)).result.state, 'active')
  assert.deepEqual(plain(h.calls[0].slice(0, 2)), ['get', `${pathBase}/submissions/${submission_id}`])
  for (const bad of [
    { operation: 'stop', result: { ...visit, version: 1 } }, { operation: 'snooze', result: visit },
    { operation: 'snooze', result: { ...visit, version: 1, occurrence_version: 3 } },
    { operation: 'snooze', result: { ...milestone, version: 1 } },
  ]) {
    h.reply(bad)
    await assert.rejects(h.api.resolveDueWorkIntent(action), ServiceContractError)
  }
})

test('occurrences distinguish same-source rebooking and actions stop per occurrence only', () => {
  const { api } = harness(page)
  assert.equal(api.sameDueWorkOccurrence(visit, { ...visit, occurrence_version: 3 }), false)
  assert.equal(api.sameDueWorkOccurrence(visit, milestone), false)
  assert.equal(api.sameDueWorkOccurrence(visit, { ...visit, state: 'dismissed' }), true)
  for (const [state, actions] of [['active', ['snooze', 'dismiss', 'stop']], ['snoozed', ['snooze', 'dismiss', 'restore', 'stop']],
    ['dismissed', ['restore', 'stop']], ['stopped', []]]) assert.deepEqual(plain(api.dueWorkActions({ ...visit, state })), actions)
})

function translations(language) {
  const labels = JSON.parse(readFileSync(path.join(src, `locale/${language}/property_network.json`), 'utf8'))
  return { useTranslation: () => ({ i18n: { language }, t: (key, values = {}) => {
    const lookup = name => name.split('.').reduce((entry, part) => entry?.[part], labels)
    const label = lookup(key) ?? lookup(`${key}_${values.count === 1 ? 'one' : 'other'}`)
    assert.equal(typeof label, 'string', `Missing ${language} translation ${key}`)
    return label.replace(/\{\{\s*(\w+)\s*\}\}/g, (_, name) => String(values[name] ?? ''))
  } }) }
}

function shared() {
  return { NetworkPanel: ({ title, children, headingId }) => React.createElement('section', { 'aria-labelledby': headingId }, React.createElement('h2', { id: headingId }, title), children),
    NetworkFreshness: () => null, NetworkDate: ({ value }) => React.createElement('time', { dateTime: value }, value),
    NetworkPagination: () => null, NetworkComposer: () => null, useNetworkDraftGuard() {}, networkError: () => 'error_access' }
}

test('default opt-outs are independent and the current visit renders in Santo Domingo in both languages', () => {
  function render(data, language, error = null) {
    const { DueWorkReminders } = load(panelSource, {
      react: React, 'react/jsx-runtime': jsxRuntime, 'react-i18next': translations(language), '../../api/dueWorkReminders': harness(data).api,
      '../../api/propertyManagement': { validCalendarDate }, '../../api/serviceWork': { VISIT_TIME_ZONE: timeZone },
      '../../hooks/useNetworkRead': { useNetworkRead: () => ({ data, error, checking: false, stale: false, refresh: never }) },
      './NetworkShared': shared(), './ServiceWorkflowShared': { useServiceAttempt: () => ({ busy: false, pending: null, denied: false, send: never }), ServiceMutationStatus: () => null },
    })
    return renderToStaticMarkup(React.createElement(DueWorkReminders, { projectId, parentFresh: true }))
  }
  for (const language of ['en', 'es']) {
    const disabled = render(empty, language), enabled = render(page, language)
    assert.equal((disabled.match(/type="checkbox"/g) || []).length, 2)
    assert.doesNotMatch(disabled, /checked=|Synthetic equipment visit|Clean pool filter/)
    assert.match(enabled, /Synthetic equipment visit/)
    assert.match(enabled, /Clean pool filter/)
    assert.match(enabled, /dateTime="2026-11-13T01:30:00\+00:00"/i)
    const expectedTime = new Intl.DateTimeFormat(language === 'en' ? 'en-US' : 'es-DO', {
      dateStyle: 'medium', timeStyle: 'short', timeZone,
    }).format(new Date(visit.starts_at))
    assert.ok(enabled.includes(expectedTime))
    assert.equal(render(page, language, { response: { status: 404 } }), '')
  }
})

test('project timeline mounts reminders only for actual owner and professional on live projects', () => {
  const project = { id: projectId, title: 'Synthetic project', summary: '', property_title: 'Synthetic property', property_id: itemId,
    status: 'open', my_role: 'owner', progress: { total: 0, completed: 0, percent: null } }
  function render(row) {
    const { ProjectDetail } = load(projectSource, {
      react: React, 'react/jsx-runtime': jsxRuntime, 'react-i18next': translations('en'), axios: { isAxiosError: () => false },
      '../../api/propertyNetwork': {}, '../../../../../packages/professionals/src': {},
      '../../hooks/useNetworkRead': { useNetworkRead: key => ({ data: key.startsWith('project:') ? row : null, checking: false, stale: false, refresh: never }) },
      './NetworkShared': shared(), './DueWorkReminders': { DueWorkReminders: () => React.createElement('p', {}, 'Mounted due-work panel') },
    })
    return renderToStaticMarkup(React.createElement(ProjectDetail, { id: projectId, onBack: never }))
  }
  assert.match(render(project), /Mounted due-work panel/)
  assert.match(render({ ...project, my_role: 'professional', status: 'in_progress' }), /Mounted due-work panel/)
  for (const row of [{ ...project, my_role: 'admin' }, { ...project, my_role: 'viewer' }, { ...project, my_role: 'manager' },
    { ...project, status: 'archived' }, { ...project, status: 'completed' }]) assert.doesNotMatch(render(row), /Mounted due-work panel/)
})
