const { test } = require('node:test')
const assert = require('node:assert/strict')
const { readFileSync } = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')

const requestId = '11111111-1111-4111-8111-111111111111'
const submissionId = '22222222-2222-4222-8222-222222222222'
const messageId = '33333333-3333-4333-8333-333333333333'
const recipientId = '44444444-4444-4444-8444-444444444444'
const otherRecipientId = '55555555-5555-4555-8555-555555555555'
const route = `/property-network/service-requests/${requestId}/conversation`
const negotiationRoute = `/property-network/service-requests/${requestId}/recipients/${recipientId}/conversation`
const row = { id: messageId, sender: 'owner', body: 'Plain <strong>text</strong>\nSecond line', created_at: '2026-11-12T18:00:00.000000+00:00' }
const page = { request_id: requestId, items: [row], total: 31, page: 2, page_size: 30 }
const plain = value => JSON.parse(JSON.stringify(value))

class ServiceContractError extends Error {}
class ServiceInputError extends ServiceContractError {}

function harness(initial) {
  const calls = []
  let response, error
  const reply = (data, cache = 'no-store') => { response = { data, headers: { 'cache-control': cache } }; error = undefined }
  reply(initial)
  const client = Object.fromEntries(['get', 'post'].map(method => [method, async (...args) => {
    calls.push([method, ...args])
    if (error) throw error
    return response
  }]))
  const exports = {}
  const file = path.resolve(__dirname, '../apps/dashboard/src/api/serviceConversation.ts')
  const compiled = ts.transpileModule(readFileSync(file, 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS, esModuleInterop: false },
  }).outputText
  vm.runInNewContext(compiled, { exports, require(name) {
    if (name === './axios') return { default: client }
    if (name === 'axios') return { isAxiosError: cause => cause?.isAxiosError === true }
    if (name === './serviceWorkflow') return { ServiceContractError, ServiceInputError }
    assert.fail(`Unexpected dependency ${name}`)
  } })
  return { api: exports, calls, reply, deny: status => { error = { isAxiosError: true, response: { status } } } }
}

test('conversation reads require exact private request, role projection, pagination and no-store', async () => {
  const h = harness(page)
  const signal = new AbortController().signal
  assert.deepEqual(plain(await h.api.getServiceConversation(requestId, 2, signal)), page)
  assert.equal(h.calls[0][1], route)
  assert.deepEqual(plain(h.calls[0][2].params), { page: 2 })
  assert.equal(h.calls[0][2].withCredentials, true)
  assert.equal(h.calls[0][2].signal, signal)

  for (const bad of [
    { ...page, request_id: submissionId }, { ...page, page: 1 }, { ...page, page_size: 100 },
    { ...page, items: [row, row] }, { ...page, items: [{ ...row, sender: 'admin' }] },
    { ...page, items: [{ ...row, email: 'synthetic@example.com' }] },
    { ...page, items: [{ ...row, created_at: '2026-11-12' }] },
  ]) {
    h.reply(bad)
    await assert.rejects(h.api.getServiceConversation(requestId, 2), ServiceContractError)
  }
  h.reply(page, 'private, max-age=60')
  await assert.rejects(h.api.getServiceConversation(requestId, 2), ServiceContractError)
})

test('sending and resolving an uncertain message retain its exact key, text and sender', async () => {
  const h = harness(row)
  const payload = { submission_id: submissionId, body: row.body }
  const action = { requestId, sender: 'owner', payload }
  assert.deepEqual(plain(await h.api.sendConversationMessage(action)), row)
  assert.deepEqual(plain(await h.api.resolveConversationMessage(action)), row)
  assert.deepEqual(plain(h.calls.map(call => call.slice(0, 2))), [['post', route], ['get', `${route}/submissions/${submissionId}`]])
  assert.deepEqual(plain(h.calls[0][2]), payload)

  await assert.rejects(h.api.sendConversationMessage({ ...action, payload: { ...payload, recipient: 'synthetic@example.com' } }), ServiceInputError)
  assert.equal(h.calls.length, 2)
  h.reply({ ...row, sender: 'provider' })
  await assert.rejects(h.api.resolveConversationMessage(action), ServiceContractError)
  h.reply({ ...row, body: 'Different message' })
  await assert.rejects(h.api.sendConversationMessage(action), ServiceContractError)
})

test('stored Unicode edge characters survive readback without changing outgoing trim', async () => {
  const stored = { ...row, body: `\uFEFF${row.body}\uFEFF` }
  const h = harness({ ...page, items: [stored] })
  assert.deepEqual(plain((await h.api.getServiceConversation(requestId, 2)).items), [stored])
  assert.equal(h.api.conversationBody(stored.body), row.body)
  for (const body of ['', 'x'.repeat(2001), 'a\u009fb']) {
    h.reply({ ...page, items: [{ ...row, body }] })
    await assert.rejects(h.api.getServiceConversation(requestId, 2), ServiceContractError)
  }
})

test('plain text is trimmed and bounded by characters while preserving markup literally', () => {
  const { api } = harness(null)
  assert.equal(api.conversationBody(`  ${row.body}\t`), row.body)
  assert.equal(api.conversationBody('🚪'.repeat(2000)).length, 4000)
  for (const value of ['\n\t', '🚪'.repeat(2001), 'a\u0000b', 'a\u007fb', 'a\u0085b', 'a\u009fb']) {
    assert.equal(api.conversationBody(value), null)
  }
})

test('pre-award access is explicit, private, recipient-bound, and hides a denied or unavailable thread', async () => {
  const access = { request_id: requestId, recipient_id: recipientId, can_open_conversation: true }
  const h = harness(access)
  assert.equal(await h.api.getServiceNegotiationAccess(requestId, recipientId), true)
  assert.equal(h.calls[0][1], `${negotiationRoute}/access`)
  assert.equal(h.calls[0][2].withCredentials, true)
  h.deny(404)
  assert.equal(await h.api.getServiceNegotiationAccess(requestId, recipientId), false)
  h.reply({ ...access, recipient_id: otherRecipientId })
  await assert.rejects(h.api.getServiceNegotiationAccess(requestId, recipientId), ServiceContractError)
  h.reply({ ...access, can_open_conversation: false })
  await assert.rejects(h.api.getServiceNegotiationAccess(requestId, recipientId), ServiceContractError)
  h.reply(access, 'private, max-age=60')
  await assert.rejects(h.api.getServiceNegotiationAccess(requestId, recipientId), ServiceContractError)
  assert.ok(h.calls.every(call => call[1] === `${negotiationRoute}/access`))
})

test('pre-award messages and receipts remain on the selected recipient route', async () => {
  const nested = { ...page, recipient_id: recipientId }
  const h = harness(nested)
  assert.deepEqual(plain(await h.api.getServiceConversation(requestId, 2, undefined, recipientId)), nested)
  assert.equal(h.calls[0][1], negotiationRoute)
  h.reply({ ...nested, recipient_id: otherRecipientId })
  await assert.rejects(h.api.getServiceConversation(requestId, 2, undefined, recipientId), ServiceContractError)
  h.reply(page)
  await assert.rejects(h.api.getServiceConversation(requestId, 2, undefined, recipientId), ServiceContractError)
  h.reply(nested)
  await assert.rejects(h.api.getServiceConversation(requestId, 2), ServiceContractError)

  const payload = { submission_id: submissionId, body: row.body }
  const action = { requestId, recipientId, sender: 'owner', payload }
  h.reply(row)
  await h.api.sendConversationMessage(action)
  await h.api.resolveConversationMessage(action)
  assert.deepEqual(plain(h.calls.slice(-2).map(call => call.slice(0, 2))), [
    ['post', negotiationRoute], ['get', `${negotiationRoute}/submissions/${submissionId}`],
  ])
  assert.deepEqual(plain(h.calls.at(-2)[2]), payload)
  await assert.rejects(h.api.sendConversationMessage({ ...action, recipientId: 'not-an-id' }), ServiceInputError)
  assert.equal(h.calls.length, 6)
})
