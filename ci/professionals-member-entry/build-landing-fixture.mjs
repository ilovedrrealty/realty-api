import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { existsSync, mkdirSync, readdirSync, writeFileSync } from 'node:fs'
import { createServer } from 'node:http'

const origin = 'http://127.0.0.1:8000'
const npm = '/usr/local/lib/node_modules/npm/bin/npm-cli.js'
const home = '/tmp/landing-home'
const globalConfig = '/tmp/landing-global-npmrc'
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
const requests = { list: 0, detail: 0, unexpected: 0 }
const server = createServer({ maxHeaderSize: 8192 }, (request, response) => {
  const { headers, method, url: target } = request
  if (method !== 'GET' || headers.host !== '127.0.0.1:8000' || headers.authorization !== undefined ||
      headers.cookie !== undefined || headers['proxy-authorization'] !== undefined ||
      headers['transfer-encoding'] !== undefined || (headers['content-length'] ?? '0') !== '0' ||
      !target?.startsWith('/') || target.startsWith('//') || target.length > 256) {
    requests.unexpected++
    response.writeHead(400).end()
    return
  }
  const url = new URL(target, origin)
  if (url.hash || url.pathname + url.search !== target) {
    requests.unexpected++
    response.writeHead(400).end()
    return
  }
  const query = [...url.searchParams.entries()]
  let payload
  if (url.origin === origin && url.pathname === '/listings' &&
      query.length === 3 && url.searchParams.get('page') === '1' &&
      url.searchParams.get('page_size') === '60' && url.searchParams.get('include_aggregates') === 'false') {
    requests.list++
    payload = { items: [{ id: listingId, created_at: date }], total: 1 }
  } else if (url.origin === origin &&
             url.pathname === '/listings/' + listingId && query.length === 0) {
    requests.detail++
    payload = listing
  } else {
    requests.unexpected++
    response.writeHead(404).end()
    return
  }
  response.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store',
    'Connection': 'close' }).end(JSON.stringify(payload))
})

const phaseMarker = (phase, status) => console.log('PROFESSIONALS_LANDING_PHASE phase=' + phase + ' status=' + status)
const signals = new Set(['SIGHUP', 'SIGINT', 'SIGKILL', 'SIGABRT', 'SIGSEGV', 'SIGTERM'])
function requireCheck(value) {
  if (!value) throw new Error('GUARD_FAILED')
}

async function run(phase, script, heap) {
  phaseMarker(phase, 'started')
  const child = spawn(process.execPath, [npm, 'run', script, '--workspace=landing'], {
    cwd: '/work',
    stdio: ['ignore', 'inherit', 'inherit'],
    env: {
      PATH: '/usr/local/bin:/usr/bin:/bin', HOME: home, CI: 'true',
      NODE_OPTIONS: '--max-old-space-size=' + heap,
      NEXT_TELEMETRY_DISABLED: '1', TURBO_TELEMETRY_DISABLED: '1',
      npm_config_userconfig: home + '/.npmrc', npm_config_globalconfig: globalConfig,
      npm_config_audit: 'false', npm_config_fund: 'false', npm_config_update_notifier: 'false',
      NEXT_PUBLIC_API_URL: origin,
      NEXT_PUBLIC_GOOGLE_CLIENT_ID: 'synthetic-ci.apps.googleusercontent.com',
    },
  })
  const [code, signal] = await once(child, 'close')
  const status = Number.isInteger(code) && code >= 0 && code <= 255 ? code : 'none'
  const cause = signal === null ? 'none' : signals.has(signal) ? signal : 'other'
  console.log('PROFESSIONALS_LANDING_NATIVE phase=' + phase + ' exit=' + status + ' signal=' + cause)
  requireCheck(code === 0 && signal === null)
  phaseMarker(phase, 'pass')
}

let phase = 'env'
let listening = false
const errors = new Set(['EACCES', 'EADDRINUSE', 'EADDRNOTAVAIL', 'ENOENT', 'ENOTDIR', 'EEXIST', 'EPERM'])
function reportFailure(stage, error) {
  const code = errors.has(error?.code) ? error.code : 'GUARD_FAILED'
  console.error('PROFESSIONALS_LANDING_FAILURE phase=' + stage + ' code=' + code)
  process.exitCode = 1
}

try {
  requireCheck(process.platform === 'linux' && process.arch === 'x64' && process.version === 'v24.22.1' &&
    process.cwd() === '/work' && process.env.NEXT_PUBLIC_API_URL === origin && existsSync(npm))
  mkdirSync(home)
  writeFileSync(home + '/.npmrc', '', { flag: 'wx' })
  writeFileSync(globalConfig, '', { flag: 'wx' })
  phaseMarker(phase, 'pass')

  phase = 'sys_net'
  requireCheck(readdirSync('/sys/class/net').length === 1 && existsSync('/sys/class/net/lo'))
  phaseMarker(phase, 'pass')

  phase = 'fixture_bind'
  server.listen({ host: '127.0.0.1', port: 8000, exclusive: true })
  await once(server, 'listening')
  listening = true
  const address = server.address()
  requireCheck(address?.address === '127.0.0.1' && address.port === 8000)
  phaseMarker(phase, 'pass')

  phase = 'npm_build'
  await run(phase, 'build', 1536)
  phase = 'typecheck'
  await run(phase, 'typecheck', 2048)

  phase = 'fixture_counts'
  requireCheck(requests.list > 0 && requests.detail > 0 && requests.unexpected === 0 &&
    requests.list + requests.detail <= 128)
  console.log('PROFESSIONALS_LANDING_FIXTURE list=' + requests.list + ' detail=' + requests.detail + ' unexpected=0')
  phaseMarker(phase, 'pass')
} catch (error) {
  reportFailure(phase, error)
} finally {
  if (listening) {
    try {
      const closed = once(server, 'close')
      server.close()
      server.closeAllConnections()
      await closed
      phaseMarker('fixture_close', 'pass')
    } catch (error) {
      reportFailure('fixture_close', error)
    }
  }
}
