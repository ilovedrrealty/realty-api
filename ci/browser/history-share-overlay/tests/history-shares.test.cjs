'use strict'

const { test } = require('node:test')
const assert = require('node:assert/strict')
const { readFileSync } = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')

const source = readFileSync(path.resolve(__dirname, '../apps/dashboard/src/api/historyShares.ts'), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: {
  target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS, esModuleInterop: false,
} }).outputText
const id = value => `00000000-0000-4000-8000-${String(value).padStart(12, '0')}`
const base = '/property-network/history-shares'
const pending = { id: id(1), label: 'Pool maintenance', view: 'received', status: 'pending', version: 1,
  expires_at: '2026-11-20T17:00:00.123456Z', item_count: 1, items: null, actions: ['accept', 'decline'] }
const summary = { occurred_on: '2026-11-12', service_category_ids: ['pool_service'], summary: 'Filter replaced.' }
const accepted = { ...pending, status: 'accepted', version: 2, items: [summary], actions: ['revoke'] }
const plain = value => JSON.parse(JSON.stringify(value))
class ServiceContractError extends Error {}
class ServiceInputError extends Error {}

function harness(initial) {
  const calls = [], exports = {}
  let result = initial
  const client = Object.fromEntries(['get', 'post'].map(method => [method, async (...args) => {
    calls.push([method, ...args])
    return result
  }]))
  const dependencies = {
    './axios': { default: client }, './serviceWorkflow': { ServiceContractError, ServiceInputError },
    './propertyManagement': { validCalendarDate: value => /^\d{4}-\d{2}-\d{2}$/.test(value)
      && Number.isFinite(Date.parse(value)) && new Date(`${value}T12:00:00Z`).toISOString().slice(0, 10) === value },
  }
  vm.runInNewContext(compiled, { exports, require(name) {
    assert.ok(Object.hasOwn(dependencies, name), `Unexpected dependency ${name}`)
    return dependencies[name]
  } })
  return { api: exports, calls, reply: (data, status = 200, cache = 'private, no-store') => {
    result = { data, status, headers: { 'cache-control': cache } }
  } }
}

test('history projections require current no-store and refuse early summaries or private source fields', async () => {
  const h = harness()
  h.reply(pending)
  assert.deepEqual(plain(await h.api.getHistoryShare(id(1), 'received')), pending)
  h.reply(accepted)
  assert.deepEqual(plain(await h.api.getHistoryShare(id(1), 'received')), accepted)
  assert.equal(h.calls[0][1], `${base}/${id(1)}`)
  assert.equal(h.calls[0][2].withCredentials, true)
  for (const bad of [{ ...pending, items: [summary] }, { ...accepted, items: [{ ...summary, raw_body: 'private' }] },
    { ...accepted, source_id: id(9) }, { ...accepted, id: id(9) }, { ...accepted, actions: ['accept'] }]) {
    h.reply(bad)
    await assert.rejects(h.api.getHistoryShare(id(1), 'received'), ServiceContractError)
  }
  h.reply(accepted, 200, 'private')
  await assert.rejects(h.api.getHistoryShare(id(1), 'received'), ServiceContractError)
  h.reply({ view: 'received', items: [accepted], total: 1, page: 1, page_size: 30 })
  await assert.rejects(h.api.getHistoryShares('received', 1), ServiceContractError)
})

test('creation options are exact to the same property and anchor and contain safe metadata only', async () => {
  const h = harness()
  const source = { kind: 'listing', id: id(2) }, anchor = id(3)
  const option = { update_id: id(4), project_title: 'Completed maintenance', occurred_on: summary.occurred_on,
    service_category_ids: summary.service_category_ids }
  const envelope = { source_kind: 'listing', source_id: source.id, anchor_project_id: anchor,
    professionals: [{ user_code: 71, display_name: 'Synthetic professional' }], items: [option], page: 2, total: 31, page_size: 30 }
  h.reply(envelope)
  const signal = new AbortController().signal
  assert.deepEqual(plain(await h.api.getHistoryShareOptions(source, anchor, 2, signal)), envelope)
  assert.deepEqual(plain(h.calls[0].slice(0, 2)), ['get', `${base}/options/listing/${source.id}`])
  assert.deepEqual(plain(h.calls[0][2].params), { anchor_project_id: anchor, page: 2 })
  assert.equal(h.calls[0][2].signal, signal)
  for (const bad of [{ ...envelope, anchor_project_id: id(9) }, { ...envelope, items: [{ ...option, body: 'private' }] },
    { ...envelope, professionals: [{ ...envelope.professionals[0], email: 'hidden@example.invalid' }] }]) {
    h.reply(bad)
    await assert.rejects(h.api.getHistoryShareOptions(source, anchor, 2), ServiceContractError)
  }
})

test('explicit creation and acceptance keep exact payloads and actor receipt versions', async () => {
  const h = harness()
  const signal = new AbortController().signal
  const intent = { operation: 'create', submission_id: id(5), input: {
    source_kind: 'private', source_id: id(2), anchor_project_id: id(3), recipient_user_code: 71,
    label: '  Pool maintenance  ', expires_in_days: 7, items: [{ update_id: id(4), summary: ' Filter replaced. ' }],
  } }
  const staged = { id: id(1), operation: 'create', status: 'pending', version: 1 }
  h.reply(staged, 201)
  assert.deepEqual(plain(await h.api.runHistoryShare(intent, signal)), staged)
  assert.deepEqual(plain(h.calls[0].slice(0, 3)), ['post', base, { submission_id: intent.submission_id,
    ...intent.input, label: 'Pool maintenance', items: [{ update_id: id(4), summary: 'Filter replaced.' }] }])
  assert.equal(h.calls[0][3].signal, signal)
  assert.throws(() => h.api.prepareHistoryShare({ ...intent, input: { ...intent.input, items: [...intent.input.items, ...intent.input.items] } }), ServiceInputError)
  const decision = { id: id(1), operation: 'accept', expected_version: 1, submission_id: id(6) }
  const receipt = { id: id(1), operation: 'accept', status: 'accepted', version: 2 }
  h.reply(receipt)
  assert.deepEqual(plain(await h.api.runHistoryShare(decision, signal)), receipt)
  assert.deepEqual(plain(h.calls[1].slice(0, 3)), ['post', `${base}/${id(1)}/actions`, {
    submission_id: id(6), operation: 'accept', expected_version: 1 }])
  assert.deepEqual(plain(await h.api.resolveHistoryShare(decision, signal)), receipt)
  assert.deepEqual(h.calls[2].slice(0, 2), ['get', `${base}/submissions/${id(6)}`])
  h.reply({ ...receipt, id: id(9) })
  await assert.rejects(h.api.resolveHistoryShare(decision, signal), ServiceContractError)
})
