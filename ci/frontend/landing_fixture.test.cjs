'use strict'
// Unrun pure contract definitions. No server start, socket, API or app import.
const { test } = require('node:test')
const assert = require('node:assert/strict')
const { LISTING_ID, validPort, fixtureResponse } = require('./landing_fixture.cjs')
const headers = { host: '127.0.0.1:8917' }

test('synthetic inventory obeys the actual nonempty static-export page contract', () => {
  const reply = fixtureResponse('GET', '/listings?page=1&page_size=60&include_aggregates=false', headers, 8917)
  assert.equal(reply.status, 200)
  assert.deepEqual(JSON.parse(reply.body), { page: 1, page_size: 60, total: 1,
    items: [{ id: LISTING_ID, created_at: '2026-10-25T12:00:00Z' }] })
  assert.match(LISTING_ID, /^[a-f\d]{8}(?:-[a-f\d]{4}){3}-[a-f\d]{12}$/i)
})

test('same known ID has the complete metadata shape with no remote photos or contact fields', () => {
  const reply = fixtureResponse('GET', `/listings/${LISTING_ID}`, headers, 8917)
  assert.equal(reply.status, 200)
  const detail = JSON.parse(reply.body)
  assert.deepEqual(Object.keys(detail).sort(), ['id', 'title', 'description', 'type', 'transaction', 'price', 'location', 'images', 'share_image_url', 'bedrooms', 'bathrooms', 'area_sqft', 'tag', 'tags', 'updated_at'].sort())
  assert.equal(detail.id, LISTING_ID); assert.equal(detail.transaction, 'sale')
  assert.equal(typeof detail.price, 'number'); assert.ok(Number.isFinite(detail.price))
  assert.equal(typeof detail.title, 'string'); assert.ok(detail.title.startsWith('Synthetic'))
  assert.deepEqual(detail.images, []); assert.equal(detail.share_image_url, null)
  assert.ok(!/https?:\/\/|email|phone|token|password/i.test(reply.body))
})

test('unknown, mutation, credential, body and malformed pagination requests refuse', () => {
  for (const [method, target, sent] of [
    ['POST', '/listings', headers], ['HEAD', `/listings/${LISTING_ID}`, headers],
    ['GET', '/unknown', headers], ['GET', '/listings/foreign-id', headers],
    ['GET', '/x/../listings?page=1&page_size=60&include_aggregates=false', headers],
    ['GET', '/listings?page=2&page_size=60&include_aggregates=false', headers],
    ['GET', '/listings?page=1&page=1&page_size=60&include_aggregates=false', headers],
    ['GET', '/listings?page=1&page_size=60&include_aggregates=false&token=x', headers],
    ['GET', `/listings/${LISTING_ID}?x=y`, headers], ['GET', 'https://external.invalid/listings', headers],
    ['GET', '/listings', { ...headers, authorization: 'synthetic' }],
    ['GET', '/listings', { ...headers, Cookie: 'synthetic' }],
    ['GET', '/listings', { ...headers, 'content-length': '1' }],
    ['GET', '/listings', { host: 'external.invalid:8917' }],
  ]) assert.notEqual(fixtureResponse(method, target, sent, 8917).status, 200)
})

test('only the explicit bounded loopback port range is accepted', () => {
  for (const port of [8900, 8917, 8999]) assert.equal(validPort(port), true)
  for (const port of [0, 8000, 8899, 9000, 8917.5, '8917', NaN, Infinity]) assert.equal(validPort(port), false)
})
