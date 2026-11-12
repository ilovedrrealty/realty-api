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
const route = '/property-network/service-favorites'
const id = '11111111-1111-4111-8111-111111111111'
const hiddenId = '22222222-2222-4222-8222-222222222222'
const favorite = { id, provider: { user_code: 515, member_alias: 'Synthetic Provider' }, saved_at: '2026-11-12T18:00:00.000000+00:00' }
const unavailable = { ...favorite, id: hiddenId, provider: null }
const page = { items: [favorite, unavailable], total: 32, page: 2, page_size: 30 }
const plain = value => JSON.parse(JSON.stringify(value))
const never = () => assert.fail('Rendering must not perform a mutation')

class ServiceContractError extends Error {}

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

const apiSource = compile('api/serviceFavorites.ts')
const listSource = compile('components/network/ServiceFavorites.tsx')
const workflowSource = compile('components/network/ServiceWorkflow.tsx')

function harness(data, status = 200) {
  const calls = []
  let response, failure
  function reply(body, code, cache = 'no-store') { response = { data: body, status: code, headers: { 'cache-control': cache } }; failure = undefined }
  reply(data, status)
  const client = Object.fromEntries(['get', 'post', 'delete'].map(method => [method, async (...args) => {
    calls.push([method, ...args])
    if (failure) throw failure
    return response
  }]))
  const api = load(apiSource, { './axios': { default: client }, './serviceWorkflow': { ServiceContractError } })
  return { api, calls, reply, deny: error => { failure = error } }
}

test('favorites read exact authenticated no-store pagination with unavailable providers as null', async () => {
  const h = harness(page)
  const signal = new AbortController().signal
  assert.deepEqual(plain(await h.api.getServiceFavorites(2, signal)), page)
  assert.equal(h.calls[0][0], 'get')
  assert.equal(h.calls[0][1], route)
  assert.deepEqual(plain(h.calls[0][2].params), { page: 2 })
  assert.equal(h.calls[0][2].signal, signal)
  assert.equal(h.calls[0][2].withCredentials, true)
})

test('favorites reject stale-cache data, duplicate identities and identifying extras', async () => {
  const h = harness(page)
  await assert.rejects(h.api.getServiceFavorites(0), ServiceContractError)
  assert.equal(h.calls.length, 0)
  for (const value of [
    { ...page, page: 1 }, { ...page, page_size: 20 }, { ...page, total: 1 },
    { ...page, items: [favorite, { ...unavailable, id }] },
    { ...page, items: [favorite, { ...favorite, id: hiddenId }] },
    { ...page, items: [{ ...unavailable, previous_alias: 'Private data' }] },
    { ...page, items: [{ ...favorite, provider: { ...favorite.provider, email: 'synthetic@example.com' } }] },
    { ...page, items: [{ ...favorite, saved_at: '2026-11-12' }] },
  ]) {
    h.reply(value, 200)
    await assert.rejects(h.api.getServiceFavorites(2), ServiceContractError)
  }
  h.reply(page, 200, 'private, max-age=30')
  await assert.rejects(h.api.getServiceFavorites(2), ServiceContractError)
})

test('saving uses only the selected provider code and requires its private 201 response', async () => {
  const h = harness(favorite, 201)
  await assert.rejects(h.api.saveServiceFavorite(0), ServiceContractError)
  assert.equal(h.calls.length, 0)
  assert.deepEqual(plain(await h.api.saveServiceFavorite(515)), favorite)
  assert.deepEqual(plain(h.calls[0].slice(0, 3)), ['post', route, { provider_user_code: 515 }])
  h.reply(unavailable, 201)
  await assert.rejects(h.api.saveServiceFavorite(515), ServiceContractError)
  h.reply(favorite, 201)
  await assert.rejects(h.api.saveServiceFavorite(516), ServiceContractError)
  h.reply(favorite, 200)
  await assert.rejects(h.api.saveServiceFavorite(515), ServiceContractError)
})

test('removing sends one JSON DELETE for the exact favorite and verifies a private 204', async () => {
  const h = harness('', 204)
  await assert.rejects(h.api.removeServiceFavorite('../other'), ServiceContractError)
  assert.equal(h.calls.length, 0)
  await h.api.removeServiceFavorite(id)
  assert.deepEqual(plain(h.calls[0].slice(0, 2)), ['delete', `${route}/${id}`])
  assert.deepEqual(plain(h.calls[0][2].data), {})
  assert.equal(h.calls[0][2].headers['Content-Type'], 'application/json')
  assert.equal(h.calls[0][2].withCredentials, true)
  h.reply({ ignored: true }, 204)
  await assert.rejects(h.api.removeServiceFavorite(id), ServiceContractError)
  const denied = { response: { status: 404 } }
  h.deny(denied)
  await assert.rejects(h.api.removeServiceFavorite(id), error => error === denied)
  assert.equal(h.calls.length, 3)
})

function translation(language) {
  const translations = JSON.parse(readFileSync(path.join(src, `locale/${language}/property_network.json`), 'utf8'))
  return { useTranslation: () => ({ t: (key, values = {}) => {
    const label = key.split('.').reduce((entry, part) => entry?.[part], translations)
    assert.equal(typeof label, 'string', `Missing ${language} translation ${key}`)
    return label.replace(/\{\{(\w+)\}\}/g, (_, name) => String(values[name] ?? ''))
  } }) }
}

function shared() {
  return {
    NetworkPanel: ({ title, children, headingId }) => React.createElement('section', { 'aria-labelledby': headingId }, React.createElement('h2', { id: headingId }, title), children),
    NetworkFreshness: () => null, NetworkDate: () => null, NetworkPagination: () => null,
    networkError: () => 'error_access', useNetworkDraftGuard() {},
  }
}

test('saved list displays current alias but only a generic unavailable row in English and Spanish', () => {
  for (const [language, generic, remove] of [['en', 'Professional unavailable', 'Remove saved professional'],
    ['es', 'Profesional no disponible', 'Quitar profesional guardado']]) {
    const { ServiceFavorites } = load(listSource, {
      react: React, 'react/jsx-runtime': jsxRuntime, 'react-i18next': translation(language),
      '../../api/serviceFavorites': { getServiceFavorites: never, removeServiceFavorite: never, SERVICE_FAVORITE_PAGE_SIZE: 30 },
      '../../hooks/useNetworkRead': { useNetworkRead: () => ({ data: page, checking: false, stale: true, refresh: never }) },
      './NetworkShared': shared(),
    })
    const markup = renderToStaticMarkup(React.createElement(ServiceFavorites, { actorId: id }))
    assert.match(markup, /Synthetic Provider/)
    const rows = markup.match(/<li\b[^>]*>.*?<\/li>/g)
    assert.equal(rows.length, 2)
    assert.match(rows[1], new RegExp(generic))
    assert.match(rows[1], new RegExp(remove))
    assert.doesNotMatch(rows[1], /Synthetic Provider|515|mailto|whatsapp/)
    assert.equal((markup.match(/<button type="button" disabled/g) || []).length, 2)
    assert.doesNotMatch(markup, new RegExp(`${id}|${hiddenId}|Request again`))
  }
})

test('only non-Admin dashboards offer their personal saved-professionals tab', () => {
  const { ServiceWorkflow } = load(workflowSource, {
    react: React, 'react/jsx-runtime': jsxRuntime, 'react-i18next': translation('en'),
    '../../api/serviceWorkflow': { getServiceRequests: never },
    '../../hooks/useNetworkRead': { useNetworkRead: () => ({ data: null, checking: false, stale: true, refresh: never }) },
    './NetworkShared': shared(),
    './ServicePreferences': { ServicePreferences: never }, './ServiceRequestCreate': { ServiceRequestCreate: never },
    './ServiceRequestDetail': { ServiceRequestDetail: never }, './ServiceFavorites': { ServiceFavorites: never },
  })
  const dashboard = role => renderToStaticMarkup(React.createElement(ServiceWorkflow, { options: {}, role, actorId: id, actorCode: 10, onProject: never }))
  assert.match(dashboard('owner'), /<button[^>]*>Saved professionals<\/button>/)
  assert.match(dashboard('buyer'), /<button[^>]*>Saved professionals<\/button>/)
  assert.doesNotMatch(dashboard('admin'), /Saved professionals/)
})
