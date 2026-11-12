const { test } = require('node:test')
const assert = require('node:assert/strict')
const { readFileSync } = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')

const requestId = '11111111-1111-4111-8111-111111111111'
const submissionId = '22222222-2222-4222-8222-222222222222'
const reviewId = '33333333-3333-4333-8333-333333333333'
const route = `/property-network/service-requests/${requestId}/review`
const review = { id: reviewId, stars: 5, comment: 'Clear <strong>progress</strong>\nand cleanup', created_at: '2026-11-12T18:00:00.000000+00:00' }
const emptyOwner = { request_id: requestId, review: null, can_submit: true }
const saved = { ...emptyOwner, review, can_submit: false }
const plain = value => JSON.parse(JSON.stringify(value))

class ServiceContractError extends Error {}
class ServiceInputError extends ServiceContractError {}

const compiled = ts.transpileModule(readFileSync(path.resolve(__dirname, '../apps/dashboard/src/api/serviceReview.ts'), 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS, esModuleInterop: false },
}).outputText

function harness(initial) {
  const calls = []
  let response
  function reply(data, cache = 'no-store') { response = { data, headers: { 'cache-control': cache } } }
  reply(initial)
  const client = Object.fromEntries(['get', 'post'].map(method => [method, async (...args) => {
    calls.push([method, ...args])
    return response
  }]))
  const api = {}
  vm.runInNewContext(compiled, { exports: api, require(name) {
    if (name === './axios') return { default: client }
    if (name === './serviceWorkflow') return { ServiceContractError, ServiceInputError }
    assert.fail(`Unexpected dependency ${name}`)
  } })
  return { api, calls, reply }
}

test('private review reads distinguish eligible owner, empty provider and immutable saved review', async () => {
  const h = harness(emptyOwner)
  const signal = new AbortController().signal
  assert.deepEqual(plain(await h.api.getServiceReview(requestId, signal)), emptyOwner)
  assert.equal(h.calls[0][1], route)
  assert.equal(h.calls[0][2].withCredentials, true)
  assert.equal(h.calls[0][2].signal, signal)
  const provider = { ...emptyOwner, can_submit: false }
  h.reply(provider)
  assert.deepEqual(plain(await h.api.getServiceReview(requestId)), provider)
  h.reply(saved, 'private, no-store')
  assert.deepEqual(plain(await h.api.getServiceReview(requestId)), saved)
})

test('review reads reject another request, extra identifying fields and unsafe caching', async () => {
  const h = harness(saved)
  for (const value of [
    { ...saved, request_id: submissionId }, { ...saved, can_submit: true }, { ...saved, can_submit: 0 },
    { ...saved, review: { ...review, owner_email: 'synthetic@example.com' } },
    { ...saved, review: { ...review, stars: 1.5 } }, { ...saved, review: { ...review, comment: 'a\u009fb' } },
    { ...saved, review: { ...review, created_at: '2026-11-12' } },
  ]) {
    h.reply(value)
    await assert.rejects(h.api.getServiceReview(requestId), ServiceContractError)
  }
  h.reply(saved, 'private, max-age=30')
  await assert.rejects(h.api.getServiceReview(requestId), ServiceContractError)
})

test('comment normalization permits blank and plain text, bounds characters and preserves a BOM', () => {
  const { api } = harness(null)
  assert.equal(api.normalizeServiceReviewComment('\u00a0\n\t'), null)
  assert.equal(api.normalizeServiceReviewComment(`\u00a0${review.comment}\t`), review.comment)
  assert.equal(api.normalizeServiceReviewComment(`\uFEFF${review.comment}\uFEFF`), `\uFEFF${review.comment}\uFEFF`)
  assert.equal(api.normalizeServiceReviewComment('🚪'.repeat(2000)).length, 4000)
  for (const invalid of ['🚪'.repeat(2001), 'a\u0000b', 'a\u007fb', '\u0085a', 'a\u009f']) {
    assert.equal(api.normalizeServiceReviewComment(invalid), undefined)
  }
})

test('submissions reject malformed ratings and comments before any network call', async () => {
  const h = harness(review)
  const payload = { submission_id: submissionId, stars: 5, comment: review.comment }
  for (const stars of [0, 6, 1.5, '5', true, NaN]) {
    await assert.rejects(h.api.submitServiceReview({ requestId, payload: { ...payload, stars } }), ServiceInputError)
  }
  for (const comment of ['', ` ${review.comment}`, 'a\u0000b', '🚪'.repeat(2001)]) {
    await assert.rejects(h.api.submitServiceReview({ requestId, payload: { ...payload, comment } }), ServiceInputError)
  }
  await assert.rejects(h.api.submitServiceReview({ requestId, payload: { ...payload, provider: 'synthetic@example.com' } }), ServiceInputError)
  assert.deepEqual(h.calls, [])
})

test('saved and actor-only receipt results bind the same request, key, rating and comment', async () => {
  const h = harness(review)
  const payload = { submission_id: submissionId, stars: 5, comment: review.comment }
  const action = { requestId, payload }
  assert.deepEqual(plain(await h.api.submitServiceReview(action)), review)
  assert.deepEqual(plain(await h.api.resolveServiceReview(action)), review)
  assert.deepEqual(plain(h.calls[0][2]), payload)
  assert.deepEqual(plain(h.calls.map(call => call.slice(0, 2))), [['post', route], ['get', `${route}/submissions/${submissionId}`]])
  h.reply({ ...review, stars: 4 })
  await assert.rejects(h.api.resolveServiceReview(action), ServiceContractError)
  h.reply({ ...review, comment: 'Different comment' })
  await assert.rejects(h.api.submitServiceReview(action), ServiceContractError)
  h.reply({ ...review, comment: null })
  assert.equal((await h.api.submitServiceReview({ requestId, payload: { ...payload, comment: null } })).comment, null)
})
