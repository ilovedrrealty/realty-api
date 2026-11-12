'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const { readFileSync } = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const React = require('react')
const jsxRuntime = require('react/jsx-runtime')
const { renderToStaticMarkup } = require('react-dom/server')

const src = path.resolve(__dirname, '../apps/dashboard/src')
const sourceId = '11111111-1111-4111-8111-111111111111'
const requestId = '22222222-2222-4222-8222-222222222222'
const visitStart = '2026-11-13T01:30:00+00:00'
const timeZone = 'America/Santo_Domingo'
const property = { kind: 'private', id: sourceId, title: 'Synthetic property' }
const card = { request_id: requestId, title: 'Synthetic service', stage: 'visit_proposed', desired_start_on: '2026-11-12',
  quote_valid_until: '2026-11-11', starts_at: visitStart, actions: ['withdraw_offer', 'cancel_visit'], source: property }
const page = { view: 'managed', as_of: '2026-11-12', time_zone: timeZone, items: [card], total: 1, page: 1, page_size: 30 }
const managed = { view: 'managed', page: 1 }
const received = { view: 'received', page: 1 }
const plain = value => JSON.parse(JSON.stringify(value))

function load(file, dependencies) {
  const exports = {}
  const code = ts.transpileModule(readFileSync(path.join(src, file), 'utf8'), { compilerOptions: {
    target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: false,
  } }).outputText
  vm.runInNewContext(code, { exports, require(name) {
    assert.ok(Object.hasOwn(dependencies, name), `Unexpected dependency ${name}`)
    return dependencies[name]
  } })
  return exports
}

class ServiceContractError extends Error {}
const validCalendarDate = value => /^\d{4}-\d{2}-\d{2}$/.test(value) && value.slice(0, 4) !== '0000'
  && Number.isFinite(Date.parse(value)) && new Date(`${value}T12:00:00Z`).toISOString().slice(0, 10) === value

function loadApi(data, cache = 'no-store') {
  const calls = []
  const api = load('api/serviceOperations.ts', {
    './axios': { default: { get: async (...args) => { calls.push(args); return { data, headers: { 'cache-control': cache } } } } },
    './propertyManagement': { validCalendarDate }, './serviceWork': { VISIT_TIME_ZONE: timeZone }, './serviceWorkflow': { ServiceContractError },
  })
  return { ...api, calls }
}

test('managed operations read the exact private source pair with the authenticated read only', async () => {
  const api = loadApi(page)
  const controller = new AbortController()
  const result = await api.getServiceOperations({ ...managed, source: { kind: property.kind, id: property.id } }, controller.signal)
  assert.deepEqual(plain(result), page)
  assert.equal(api.calls.length, 1)
  const [endpoint, options] = api.calls[0]
  assert.equal(endpoint, '/property-network/service-operations')
  assert.deepEqual(plain(options.params), { view: 'managed', page: 1, source_kind: 'private', source_id: sourceId })
  assert.equal(options.withCredentials, true)
  assert.equal(options.signal, controller.signal)
  assert.equal(options.headers, undefined)
  await assert.rejects(api.getServiceOperations({ ...received, source: property }), ServiceContractError)
  assert.equal(api.calls.length, 1)
})

test('the received response cannot carry managed source data or managed-only actions', async () => {
  const receivedPage = { ...page, view: 'received', items: [{ ...card, source: null, actions: ['accept_visit'] }] }
  const api = loadApi(receivedPage)
  assert.deepEqual(plain(await api.getServiceOperations(received)), receivedPage)
  assert.deepEqual(plain(api.calls[0][1].params), received)
  for (const invalid of [{ ...card }, { ...card, source: null, actions: ['award'] }]) {
    await assert.rejects(loadApi({ ...receivedPage, items: [invalid] }).getServiceOperations(received), ServiceContractError)
  }
})

test('rejects invalid dates, extra private fields, wrong source scope, duplicates and cacheable replies', async () => {
  for (const invalid of [
    { ...card, desired_start_on: '2026-02-30' }, { ...card, starts_at: '2026-11-13T01:30:00' },
    { ...card, starts_at: '2026-02-30T01:30:00+00:00' }, { ...card, phone: 'hidden' },
    { ...card, actions: ['cancel_visit', 'cancel_visit'] },
  ]) await assert.rejects(loadApi({ ...page, items: [invalid] }).getServiceOperations(managed), ServiceContractError)
  await assert.rejects(loadApi(page).getServiceOperations({ ...managed, source: { kind: 'listing', id: sourceId } }), ServiceContractError)
  await assert.rejects(loadApi({ ...page, items: [card, card], total: 2 }).getServiceOperations(managed), ServiceContractError)
  await assert.rejects(loadApi(page, 'private, max-age=10').getServiceOperations(managed), ServiceContractError)
})

const copy = lang => JSON.parse(readFileSync(path.join(src, `locale/${lang}/property_network.json`), 'utf8'))
function translator(lang) {
  const words = copy(lang)
  return () => ({ t: key => key.split('.').reduce((part, name) => part?.[name], words) ?? key, i18n: { language: lang } })
}

function renderOperations(lang, data, state = {}) {
  const useTranslation = translator(lang)
  const { NetworkDate, NetworkPanel } = load('components/network/NetworkShared.tsx', {
    react: React, 'react/jsx-runtime': jsxRuntime, 'react-i18next': { useTranslation }, axios: {},
    '../../api/propertyNetwork': {}, '../../api/propertyManagement': {}, '../../hooks/useNetworkRead': {},
  })
  const { ServiceOperations } = load('components/network/ServiceOperations.tsx', {
    react: React, 'react/jsx-runtime': jsxRuntime, 'react-i18next': { useTranslation }, '../../api/serviceOperations': {},
    '../../hooks/useNetworkRead': { useNetworkRead: () => ({ data, stale: false, error: null, ...state }) },
    './NetworkShared': { NetworkDate, NetworkPanel, NetworkFreshness: () => null, NetworkPagination: () => null,
      networkError: () => 'error_access' },
  })
  return renderToStaticMarkup(React.createElement(ServiceOperations, { scope: { view: data.view, page: 1 }, actorId: 'synthetic' }))
}

test('Spanish renders the retained confirmed visit in Santo Domingo, and denied or stale data disappears', () => {
  const markup = renderOperations('es', page)
  assert.match(markup, /Operaciones de servicios/)
  assert.match(markup, /Visita confirmada/)
  const spanishVisit = new Intl.DateTimeFormat('es-DO', { dateStyle: 'medium', timeStyle: 'short', timeZone }).format(new Date(visitStart))
  assert.ok(markup.includes(spanishVisit), 'visit must keep the prior Dominican calendar date')
  assert.match(markup, /Synthetic property/)
  assert.match(markup, /Retirar oferta/)
  assert.doesNotMatch(renderOperations('en', page, { stale: true }), /Synthetic service|Synthetic property/)
  assert.doesNotMatch(renderOperations('en', page, { error: new Error('denied') }), /Synthetic service|Synthetic property/)
})

function workflowHarness() {
  const slots = []
  let offset = 0
  const useState = initial => {
    const index = offset++
    if (!(index in slots)) slots[index] = initial
    return [slots[index], value => { slots[index] = value }]
  }
  const useRef = () => ({ current: null })
  const leaf = () => null
  const Ops = () => null
  const Detail = () => null
  const { ServiceWorkflow } = load('components/network/ServiceWorkflow.tsx', {
    react: { useState, useRef, useEffect() {} }, 'react/jsx-runtime': jsxRuntime, 'react-i18next': { useTranslation: translator('en') },
    '../../api/serviceWorkflow': {}, '../../hooks/useNetworkRead': {}, './NetworkShared': { allowNetworkNavigation: () => true },
    './ServicePreferences': { ServicePreferences: leaf }, './ServiceFavorites': { ServiceFavorites: leaf },
    './ServiceRequestCreate': { ServiceRequestCreate: leaf }, './ServiceRequestDetail': { ServiceRequestDetail: Detail },
    './ServiceOperations': { ServiceOperations: Ops },
  })
  const render = role => { offset = 0; return ServiceWorkflow({ role, actorId: 'synthetic' }) }
  return { render, Ops, Detail }
}

function find(element, matches) {
  if (Array.isArray(element)) return element.map(item => find(item, matches)).find(Boolean)
  if (!React.isValidElement(element)) return undefined
  return matches(element) ? element : find(element.props.children, matches)
}

test('native details preserve received or managed page and filter on back; generic Admin cannot mount operations', () => {
  const { render, Ops, Detail } = workflowHarness()
  const isTab = element => element.type === 'button' && element.props.children === 'Service operations'
  assert.equal(find(render('admin'), isTab), undefined)
  find(render('owner'), isTab).props.onClick()
  const view = element => element.type === Ops
  const native = element => element.type === Detail
  const scopes = [{ view: 'received', page: 2 }, { view: 'managed', page: 3, source: property }]
  for (const scope of scopes) {
    find(render('owner'), view).props.onScope(scope)
    find(render('owner'), view).props.onOpen(requestId, scope.view)
    const detail = find(render('owner'), native)
    assert.equal(detail.props.id, requestId)
    assert.equal(detail.props.view, scope.view)
    assert.equal(detail.props.backLabel, 'Back to service operations')
    assert.equal(find(render('owner'), view), undefined)
    detail.props.onBack()
    assert.deepEqual(find(render('owner'), view).props.scope, scope)
  }
  find(render('owner'), view).props.onOpen(requestId, 'managed')
  assert.equal(find(render('admin'), native), undefined)
  assert.equal(find(render('admin'), view), undefined)
})

test('every server stage and action has an English and Spanish label', () => {
  const { SERVICE_OPERATIONS_STAGES, SERVICE_OPERATIONS_ACTIONS } = loadApi(page)
  for (const language of ['en', 'es']) {
    const { stages, actions } = copy(language).operations
    assert.deepEqual(Object.keys(stages).sort(), [...SERVICE_OPERATIONS_STAGES].sort())
    assert.deepEqual(Object.keys(actions).sort(), [...new Set(Object.values(SERVICE_OPERATIONS_ACTIONS).flat())].sort())
  }
})
