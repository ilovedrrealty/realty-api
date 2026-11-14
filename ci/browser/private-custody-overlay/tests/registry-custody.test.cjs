'use strict'
const { test } = require('node:test')
const assert = require('node:assert/strict')
const { readFileSync } = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')

const source = readFileSync(path.resolve(__dirname, '../apps/dashboard/src/api/registryCustody.ts'), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: {
  target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS, esModuleInterop: false,
} }).outputText
const id = n => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`
const base = '/property-network/registry-custody'
const fields = { title: 'Synthetic title', location: 'Synthetic district', address: '', property_class: 'house_villa', relationship: 'seller' }
const options = { property_id: id(2), property_version: 21, fields, pending_transfer_id: null,
  blockers: { active_projects: 0, open_requests: 0, unfinished_awards: 0 } }
const pending = { id: id(1), label: 'Synthetic transfer', view: 'received', status: 'pending', version: 1,
  expires_at: '2026-11-21T12:00:00Z', fields: null, property_id: null, actions: ['accept', 'decline'] }
const accepted = { ...pending, version: 2, status: 'accepted', actions: [], fields, property_id: id(2) }
const plain = value => JSON.parse(JSON.stringify(value))
class ServiceContractError extends Error {}
class ServiceInputError extends Error {}

function harness() {
  const calls = [], exports = {}
  let response
  const client = Object.fromEntries(['get', 'post'].map(method => [method, async (...args) => {
    calls.push([method, ...args])
    return response
  }]))
  const deps = { './axios': { default: client }, './propertyNetwork': { PRIVATE_PROPERTY_CLASSES: ['house_villa'] },
    './serviceWorkflow': { ServiceContractError, ServiceInputError }, './propertyManagement': {
      validCalendarDate: value => new Date(value).toISOString().slice(0, 10) === value,
    } }
  vm.runInNewContext(compiled, { exports, require(name) {
    assert.ok(Object.hasOwn(deps, name), `Unexpected module ${name}`)
    return deps[name]
  } })
  return { api: exports, calls, reply: (data, status = 200, cache = 'private, no-store') => {
    response = { data, status, headers: { 'cache-control': cache } }
  } }
}

test('recipient pending, list and former snapshots cannot reveal private registry fields; owner options match exact source', async () => {
  const h = harness(), signal = new AbortController().signal
  h.reply(options)
  assert.deepEqual(plain(await h.api.getCustodyOptions(id(2), signal)), options)
  assert.equal(h.calls[0][1], `${base}/options/${id(2)}`)
  assert.equal(h.calls[0][2].withCredentials, true)
  assert.equal(h.calls[0][2].signal, signal)
  for (const bad of [{ ...options, property_id: id(3) }, { ...options, fields: { ...fields, notes: 'private' } }]) {
    h.reply(bad)
    await assert.rejects(h.api.getCustodyOptions(id(2)), ServiceContractError)
  }
  for (const expected of [pending, accepted, { ...accepted, fields: null, property_id: null }]) {
    h.reply(expected)
    assert.deepEqual(plain(await h.api.getCustodyTransfer(id(1), 'received')), expected)
  }
  for (const bad of [{ ...pending, fields }, { ...pending, property_id: id(2) }, { ...accepted, address: 'leak' },
    { ...accepted, fields: { ...fields, notes: 'leak' } }, { ...accepted, fields: null }, { ...accepted, view: 'sent' }]) {
    h.reply(bad)
    await assert.rejects(h.api.getCustodyTransfer(id(1), 'received'), ServiceContractError)
  }
  h.reply({ view: 'received', items: [accepted], total: 1, page: 1, page_size: 30 })
  await assert.rejects(h.api.getCustodyTransfers('received', 1), ServiceContractError)
  h.reply(pending, 200, 'private')
  await assert.rejects(h.api.getCustodyTransfer(id(1), 'received'), ServiceContractError)
})

test('offer and accept require exact consent, sealed property version and original actor receipt', async () => {
  const h = harness(), signal = new AbortController().signal
  const offer = { operation: 'offer', property_id: id(2), expected_property_version: 21, recipient_user_code: 71,
    submission_id: id(3), label: '  Reviewed transfer  ', transfer_acknowledged: true }
  const staged = { id: id(1), version: 1, status: 'pending', operation: 'offer' }
  h.reply(staged, 201)
  assert.deepEqual(plain(await h.api.runCustodyTransfer(offer, signal)), staged)
  const { operation, ...body } = { ...offer, label: 'Reviewed transfer' }
  assert.deepEqual(plain(h.calls[0].slice(0, 3)), ['post', base, body])
  for (const invalid of [{ ...offer, transfer_acknowledged: false }, { ...offer, recipient_user_code: 0 }]) {
    assert.throws(() => h.api.prepareCustodyTransfer(invalid), ServiceInputError)
  }
  const decision = { id: id(1), operation: 'accept', submission_id: id(4), expected_version: 1, transfer_acknowledged: true }
  assert.throws(() => h.api.prepareCustodyTransfer({ ...decision, transfer_acknowledged: undefined }), ServiceInputError)
  assert.throws(() => h.api.prepareCustodyTransfer({ ...decision, operation: 'decline' }), ServiceInputError)
  const receipt = { id: id(1), version: 2, status: 'accepted', operation: 'accept' }
  h.reply(receipt)
  assert.deepEqual(plain(await h.api.runCustodyTransfer(decision, signal)), receipt)
  assert.deepEqual(plain(h.calls[1].slice(0, 3)), ['post', `${base}/${id(1)}/actions`, {
    submission_id: id(4), expected_version: 1, operation: 'accept', transfer_acknowledged: true,
  }])
  assert.deepEqual(plain(await h.api.resolveCustodyTransfer(decision, signal)), receipt)
  assert.deepEqual(h.calls[2].slice(0, 2), ['get', `${base}/submissions/${id(4)}`])
  h.reply({ ...receipt, version: 1 })
  await assert.rejects(h.api.resolveCustodyTransfer(decision, signal), ServiceContractError)
})

test('both elapsed and retired invitations read as expired without fields or actions', async () => {
  const h = harness()
  for (const version of [1, 2]) {
    const expired = { ...pending, version, status: 'expired', actions: [] }
    h.reply(expired)
    assert.deepEqual(plain(await h.api.getCustodyTransfer(id(1), 'received')), expired)
    h.reply({ view: 'received', items: [expired], total: 1, page: 1, page_size: 30 })
    const result = await h.api.getCustodyTransfers('received', 1)
    assert.deepEqual(plain(result.items), [expired])
  }
  h.reply({ ...pending, version: 3, status: 'expired', actions: [] })
  await assert.rejects(h.api.getCustodyTransfer(id(1), 'received'), ServiceContractError)
})
