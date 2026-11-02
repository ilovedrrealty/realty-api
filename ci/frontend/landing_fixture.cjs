'use strict'

// Synthetic build input only. Never publish the output of a fixture-URL build.
const http = require('node:http')
const LISTING_ID = '10000000-0000-0000-0000-000000000001'
const HOST = '127.0.0.1'
const LIST = JSON.stringify({ page: 1, page_size: 60, total: 1,
  items: [{ id: LISTING_ID, created_at: '2026-10-25T12:00:00Z' }] })
const DETAIL = JSON.stringify({ id: LISTING_ID, title: 'Synthetic CI villa',
  description: 'Synthetic fixture for isolated build verification.', type: 'Villa',
  transaction: 'sale', price: 250000, location: 'Synthetic CI location',
  images: [], share_image_url: null, bedrooms: 3, bathrooms: 3,
  area_sqft: 1800, tag: null, tags: [], updated_at: '2026-10-25T12:00:00Z' })

function validPort(port) { return Number.isInteger(port) && port >= 8900 && port <= 8999 }
const refused = (status, code) => ({ status, body: JSON.stringify({ status: code }) })

/** Pure allowlist; no request payload, credentials or target is logged/echoed. */
function fixtureResponse(method, target, headers, port) {
  if (!validPort(port)) return refused(400, 'FIXTURE_PORT_REFUSED')
  if (method !== 'GET') return refused(405, 'FIXTURE_GET_ONLY')
  const normalized = Object.fromEntries(Object.entries(headers).map(([key, value]) => [key.toLowerCase(), value]))
  if (normalized.host !== `${HOST}:${port}`) return refused(400, 'FIXTURE_HOST_REFUSED')
  if (normalized.authorization !== undefined || normalized.cookie !== undefined
      || normalized['proxy-authorization'] !== undefined) return refused(400, 'FIXTURE_CREDENTIALS_REFUSED')
  if (normalized['transfer-encoding'] !== undefined
      || normalized['content-length'] !== undefined && normalized['content-length'] !== '0') return refused(400, 'FIXTURE_BODY_REFUSED')
  if (typeof target !== 'string' || target.length > 256 || !target.startsWith('/') || target.startsWith('//')) return refused(400, 'FIXTURE_TARGET_REFUSED')
  let url
  try { url = new URL(target, `http://${HOST}:${port}`) } catch { return refused(400, 'FIXTURE_TARGET_REFUSED') }
  if (url.hash || url.pathname+url.search !== target) return refused(400, 'FIXTURE_TARGET_REFUSED')
  if (url.pathname === '/listings') {
    const keys = [...url.searchParams.keys()]
    if (keys.length !== 3 || new Set(keys).size !== 3
        || !keys.every(key => ['page', 'page_size', 'include_aggregates'].includes(key))
        || url.searchParams.get('page') !== '1' || url.searchParams.get('page_size') !== '60'
        || url.searchParams.get('include_aggregates') !== 'false') return refused(400, 'FIXTURE_PAGINATION_REFUSED')
    return { status: 200, body: LIST }
  }
  if (url.pathname === `/listings/${LISTING_ID}` && url.search === '') return { status: 200, body: DETAIL }
  return refused(404, 'FIXTURE_ROUTE_REFUSED')
}

/** Caller owns this server object or the CLI's held child process and reaping. */
async function startFixture(port) {
  if (!validPort(port)) throw new Error('FIXTURE_PORT_REFUSED')
  const server = http.createServer({ maxHeaderSize: 8192 }, (request, response) => {
    let result
    try { result = fixtureResponse(request.method, request.url, request.headers, port) }
    catch { result = refused(400, 'FIXTURE_REQUEST_REFUSED') }
    response.writeHead(result.status, { 'Content-Type': 'application/json; charset=utf-8',
      'Content-Length': Buffer.byteLength(result.body), 'Cache-Control': 'no-store',
      'X-Content-Type-Options': 'nosniff', Connection: 'close', ...(result.status === 405 ? { Allow: 'GET' } : {}) })
    response.end(result.body)
  })
  server.maxConnections = 8
  server.requestTimeout = 5000
  server.headersTimeout = 4000
  server.keepAliveTimeout = 1000
  server.setTimeout(5000)
  await new Promise((resolve, reject) => {
    server.once('error', () => reject(new Error('FIXTURE_LISTEN_REFUSED')))
    server.listen({ host: HOST, port, exclusive: true }, resolve)
  })
  const address = server.address()
  if (!address || typeof address !== 'object' || address.address !== HOST || address.port !== port) {
    server.closeAllConnections(); server.close()
    throw new Error('FIXTURE_BINDING_REFUSED')
  }
  let closing
  function close() {
    if (closing) return closing
    closing = new Promise((resolve, reject) => {
      const force = setTimeout(() => server.closeAllConnections(), 1000)
      const deadline = setTimeout(() => reject(new Error('FIXTURE_CLOSE_UNVERIFIED')), 2000)
      server.close(error => {
        clearTimeout(force); clearTimeout(deadline)
        if (error) reject(new Error('FIXTURE_CLOSE_UNVERIFIED')); else resolve()
      })
      server.closeIdleConnections()
    })
    return closing
  }
  return { server, close, host: HOST, port }
}

module.exports = { LISTING_ID, validPort, fixtureResponse, startFixture }

if (require.main === module) {
  const args = process.argv.slice(2)
  if (args.length !== 2 || args[0] !== '--port' || !/^89[0-9]{2}$/.test(args[1]) || !validPort(Number(args[1]))) {
    process.stderr.write('LANDING_FIXTURE_ARGUMENTS_REFUSED\n'); process.exitCode = 1
  } else {
    startFixture(Number(args[1])).then(fixture => {
      process.stdout.write(JSON.stringify({ status: 'LANDING_FIXTURE_LISTENING', host: HOST,
        port: fixture.port, pid: process.pid, inventory_count: 1 })+'\n')
      let stopping = false
      const stop = () => {
        if (stopping) return
        stopping = true
        fixture.close().then(() => process.stdout.write('LANDING_FIXTURE_STOPPED\n'))
          .catch(() => { process.stderr.write('LANDING_FIXTURE_CLOSE_UNVERIFIED\n'); process.exitCode = 1 })
      }
      process.once('SIGTERM', stop); process.once('SIGINT', stop)
    }).catch(() => { process.stderr.write('LANDING_FIXTURE_START_REFUSED\n'); process.exitCode = 1 })
  }
}
