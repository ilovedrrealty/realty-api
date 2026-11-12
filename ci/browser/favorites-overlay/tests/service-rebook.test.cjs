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
const requestId = '11111111-1111-4111-8111-111111111111'
const sourceId = '22222222-2222-4222-8222-222222222222'
const otherId = '33333333-3333-4333-8333-333333333333'
const route = `/property-network/service-requests/${requestId}/rebook-context`
const context = { request_id: requestId, category_id: 'pool',
  source: { kind: 'private', id: sourceId, title: 'Synthetic Villa', property_class: 'house_villa' },
  provider: { user_code: 305, member_alias: 'Current Provider' } }
const listing = { ...context, source: { ...context.source, kind: 'listing' } }
const plain = value => JSON.parse(JSON.stringify(value))

class ServiceContractError extends Error {}

function compile(file) {
  return ts.transpileModule(readFileSync(path.join(src, file), 'utf8'), { compilerOptions: {
    target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS, esModuleInterop: false, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText
}

const apiSource = compile('api/serviceRebook.ts')
const uiSource = compile('components/network/ServiceRequestCreate.tsx')

function load(code, dependencies) {
  const exports = {}
  vm.runInNewContext(code, { exports, require(name) {
    assert.ok(Object.hasOwn(dependencies, name), `Unexpected dependency ${name}`)
    return dependencies[name]
  } })
  return exports
}

function harness(initial) {
  const calls = []
  let response, error
  function reply(data, cache = 'no-store') { response = { data, headers: { 'cache-control': cache } }; error = undefined }
  reply(initial)
  const api = load(apiSource, {
    './axios': { default: { get: async (...args) => { calls.push(args); if (error) throw error; return response } } },
    './propertyNetwork': { PRIVATE_PROPERTY_CLASSES: ['house_villa', 'vacation_rental'] },
    './serviceWorkflow': { ServiceContractError },
  })
  return { api, calls, reply, deny: failure => { error = failure } }
}

test('rebooking reads one exact credentialed private context for either property source kind', async () => {
  const h = harness(context)
  const signal = new AbortController().signal
  assert.deepEqual(plain(await h.api.getServiceRebookContext(requestId, signal)), context)
  assert.equal(h.calls[0][0], route)
  assert.equal(h.calls[0][1].withCredentials, true)
  assert.equal(h.calls[0][1].signal, signal)
  h.reply(listing, 'private, no-store')
  assert.deepEqual(plain(await h.api.getServiceRebookContext(requestId)), listing)
})

test('rebooking rejects mismatched scopes, unsafe caching and any extra private history projection', async () => {
  const h = harness(context)
  await assert.rejects(h.api.getServiceRebookContext('../unknown'), ServiceContractError)
  assert.equal(h.calls.length, 0)
  for (const value of [
    { ...context, request_id: otherId }, { ...context, old_brief: 'Private scope' }, { ...context, category_id: 'INVALID' },
    { ...context, source: { ...context.source, kind: 'project' } }, { ...context, source: { ...context.source, address: 'Secret' } },
    { ...context, provider: { ...context.provider, email: 'synthetic@example.com' } }, { ...context, provider: { ...context.provider, user_code: 0 } },
  ]) {
    h.reply(value)
    await assert.rejects(h.api.getServiceRebookContext(requestId), ServiceContractError)
  }
  h.reply(context, 'private, max-age=30')
  await assert.rejects(h.api.getServiceRebookContext(requestId), ServiceContractError)
  const denied = { response: { status: 404 } }
  h.deny(denied)
  await assert.rejects(h.api.getServiceRebookContext(requestId), error => error === denied)
})

test('scope and exact preview checks distinguish updated names from changed selection', () => {
  const { api } = harness(context)
  const renamed = { ...context, source: { ...context.source, title: 'New label' }, provider: { ...context.provider, member_alias: 'New alias' } }
  assert.equal(api.sameRebookSelection(context, renamed), true)
  assert.equal(api.sameRebookPreview(context, renamed), false)
  assert.equal(api.sameRebookPreview(context, context), true)
  for (const change of [
    listing, { ...context, request_id: otherId }, { ...context, category_id: 'different' },
    { ...context, source: { ...context.source, id: otherId } }, { ...context, source: { ...context.source, property_class: 'vacation_rental' } },
  ]) {
    assert.equal(api.sameRebookScope(context, change), false)
    assert.equal(api.sameRebookSelection(context, change), false)
  }
  const swapped = { ...context, provider: { ...context.provider, user_code: 306 } }
  assert.equal(api.sameRebookScope(context, swapped), true)
  assert.equal(api.sameRebookSelection(context, swapped), false)
})

function renderDraft(rebook, freshContext, error = null, language = 'en') {
  const queries = []
  const translations = JSON.parse(readFileSync(path.join(src, `locale/${language}/property_network.json`), 'utf8'))
  const t = (key, values = {}) => {
    const label = key.split('.').reduce((entry, part) => entry?.[part], translations)
    assert.equal(typeof label, 'string', `Missing ${language} translation ${key}`)
    return label.replace(/\{\{(\w+)\}\}/g, (_, name) => String(values[name] ?? ''))
  }
  const never = () => assert.fail('Opening a draft did not authorize sending or source/discovery calls')
  const api = harness(context).api
  const { ServiceRequestCreate } = load(uiSource, {
    react: React, 'react/jsx-runtime': jsxRuntime, 'react-i18next': { useTranslation: () => ({ t, i18n: { language } }) },
    '../../api/propertyNetwork': { getPrivateProperties: never, getPrivateProperty: never, getNetworkProperties: never },
    '../../api/serviceRebook': api,
    '../../api/serviceFavorites': { saveServiceFavorite: never },
    '../../api/serviceWorkflow': { getServiceProviders: never, ServiceContractError },
    '../../../../../packages/professionals/src': { professionalLabel: labels => labels[language] },
    '../../hooks/useNetworkRead': { useNetworkRead: (key, _load, enabled) => {
      queries.push({ key, enabled })
      return { data: enabled && key.startsWith('service-rebook-draft') ? freshContext : null,
        error: enabled && key.startsWith('service-rebook-draft') ? error : null, checking: false, stale: false, refresh: never }
    } },
    './NetworkShared': {
      NetworkPanel: ({ title, children, headingId }) => React.createElement('section', { 'aria-labelledby': headingId }, React.createElement('h2', { id: headingId }, title), children),
      NetworkFreshness: () => null, NetworkPagination: () => null, useNetworkDraftGuard() {}, networkError: () => 'error_access',
    },
    './ServiceWorkflowShared': { useServiceMutation: () => ({ pending: null, busy: false, denied: false, send: never }), ServiceMutationStatus: () => null },
    './ServiceFavorites': { useFavoriteChange: () => ({ busy: false, run: never }), FavoriteChangeStatus: () => null },
  })
  const options = { property_classes: [{ id: 'house_villa', label: { en: 'House or villa', es: 'Casa o villa' } }],
    categories: [{ id: 'pool', label: { en: 'Pool care', es: 'Cuidado de piscinas' } }] }
  const markup = renderToStaticMarkup(React.createElement(ServiceRequestCreate, { rebook, options, role: 'owner', actorCode: 10, onCreated: never }))
  return { markup, queries }
}

test('both repeat drafts start blank with fixed current context and without discovery or writes', () => {
  for (const [rebook, language] of [[context, 'en'], [listing, 'es']]) {
    const { markup, queries } = renderDraft(rebook, rebook, null, language)
    assert.match(markup, language === 'en' ? /Repeat service request/ : /Repetir solicitud de servicio/)
    assert.match(markup, /Synthetic Villa/)
    assert.match(markup, /Current Provider/)
    assert.match(markup, /<textarea[^>]*><\/textarea>/)
    assert.equal((markup.match(/value=""/g) || []).length, 4)
    assert.doesNotMatch(markup, /<select|<input[^>]+checkbox/)
    assert.equal((markup.match(/<button[^>]+disabled/g) || []).length, 2)
    assert.deepEqual(queries.filter(row => row.enabled).map(row => row.key), [`service-rebook-draft:${requestId}`])
  }
})

test('unavailable and swapped providers show no source or substitute provider; normal draft retains registry selection', () => {
  for (const { markup } of [renderDraft(context, null, { response: { status: 404 } }),
    renderDraft(context, { ...context, provider: { user_code: 407, member_alias: 'Replacement Provider' } })]) {
    assert.match(markup, /no longer available to request again/)
    assert.doesNotMatch(markup, /Synthetic Villa|Current Provider|Replacement Provider/)
    assert.equal((markup.match(/<button[^>]+disabled/g) || []).length, 2)
  }
  const normal = renderDraft(null, null)
  assert.match(normal.markup, /<select/)
  assert.doesNotMatch(normal.markup, /Repeat service request/)
  assert.deepEqual(normal.queries.filter(row => row.enabled).map(row => row.key), ['service-source:private:1'])
})
