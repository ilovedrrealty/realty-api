import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { readdirSync } from 'node:fs'
import { createServer } from 'node:http'

const origin = 'http://127.0.0.1:8000'
const listingId = '10000000-0000-4000-8000-000000000001'
const date = '2026-01-01T00:00:00Z'
const listing = {
  id: listingId,
  title: 'Synthetic fixture listing',
  description: 'Synthetic static export data.',
  type: 'house',
  transaction: 'sell',
  price: 100000,
  location: 'Synthetic area',
  images: [],
  share_image_url: null,
  bedrooms: 2,
  bathrooms: 1,
  area_sqft: 1000,
  tag: null,
  tags: [],
  created_at: date,
  updated_at: date,
}
assert.deepEqual(readdirSync('/sys/class/net'), ['lo'])
assert.equal(process.env.NEXT_PUBLIC_API_URL, origin)

const requests = { list: 0, detail: 0, unexpected: 0 }
const server = createServer((request, response) => {
  const url = new URL(request.url, origin)
  const query = [...url.searchParams.entries()]
  let payload
  if (request.method === 'GET' && url.origin === origin && url.pathname === '/listings' &&
      query.length === 3 && url.searchParams.get('page') === '1' &&
      url.searchParams.get('page_size') === '60' && url.searchParams.get('include_aggregates') === 'false') {
    requests.list++
    payload = { items: [{ id: listingId, created_at: date }], total: 1 }
  } else if (request.method === 'GET' && url.origin === origin &&
             url.pathname === '/listings/' + listingId && query.length === 0) {
    requests.detail++
    payload = listing
  } else {
    requests.unexpected++
    response.writeHead(404).end()
    return
  }
  response.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify(payload))
})

async function run(script) {
  const child = spawn('npm', ['run', script, '--workspace=landing'], { stdio: 'inherit' })
  const [code, signal] = await once(child, 'exit')
  assert.equal(signal, null)
  assert.equal(code, 0)
}

try {
  server.listen(8000, '127.0.0.1')
  await once(server, 'listening')
  await run('build')
  await run('typecheck')
  assert.ok(requests.list > 0 && requests.detail > 0 && requests.unexpected === 0)
  console.log('PROFESSIONALS_LANDING_FIXTURE list=' + requests.list + ' detail=' + requests.detail + ' unexpected=0')
} finally {
  server.close()
}
