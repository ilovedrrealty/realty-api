'use strict'
// Hosted CI only. All input is synthetic. Exported files remain in runner scratch.
const fs = require('node:fs')
const crypto = require('node:crypto')
const { spawn } = require('node:child_process')
const { startFixture, LISTING_ID } = require('/fixture/landing_fixture.cjs')

const NPM = '/usr/local/lib/node_modules/npm/bin/npm-cli.js'
const WORK = '/work'
const GENERATED = `${WORK}/apps/landing/next-env.d.ts`
const MAX_LOG = 2 * 1024 * 1024
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex')
const requireCheck = (condition, code) => { if (!condition) throw new Error(code) }

function checkCgroup() {
  requireCheck(fs.readFileSync('/sys/fs/cgroup/memory.max', 'utf8').trim() === '8589934592' &&
    /^oom_kill 0$/m.test(fs.readFileSync('/sys/fs/cgroup/memory.events', 'utf8')), 'BOUNDED_CGROUP_REQUIRED')
}

async function phase(name, args, seconds, heap) {
  checkCgroup()
  const child = spawn(process.execPath, args, { cwd: WORK, detached: true, stdio: ['ignore', 'pipe', 'pipe'],
    env: { PATH: '/usr/local/bin:/usr/bin:/bin', HOME: '/tmp/landing-home', CI: 'true',
      NODE_OPTIONS: `--max-old-space-size=${heap}`, NEXT_TELEMETRY_DISABLED: '1', TURBO_TELEMETRY_DISABLED: '1',
      npm_config_userconfig: '/tmp/landing-home/.npmrc', npm_config_globalconfig: '/tmp/landing-global-npmrc',
      npm_config_audit: 'false', npm_config_fund: 'false', npm_config_update_notifier: 'false',
      NEXT_PUBLIC_API_URL: 'http://127.0.0.1:8917', NEXT_PUBLIC_DASHBOARD_URL: 'http://127.0.0.1:4175',
      NEXT_PUBLIC_GOOGLE_CLIENT_ID: 'synthetic-ci.apps.googleusercontent.com',
      NEXT_PUBLIC_GOOGLE_MAPS_API_KEY: 'synthetic-map-fixture-only' } })
  const streams = { stdout: [], stderr: [] }
  const size = { stdout: 0, stderr: 0 }
  let stop = null, force
  function stopChild(reason) {
    if (stop) return
    stop = reason
    try { process.kill(-child.pid, 'SIGTERM') } catch (error) { if (error.code !== 'ESRCH') throw error }
    force = setTimeout(() => { try { process.kill(-child.pid, 'SIGKILL') } catch {} }, 2000)
  }
  const timeout = setTimeout(() => stopChild('CHILD_DEADLINE'), seconds * 1000)
  for (const name of Object.keys(streams)) child[name].on('data', bytes => {
    const left = Math.max(0, MAX_LOG - size[name])
    if (left) streams[name].push(bytes.subarray(0, left))
    size[name] += bytes.length
    if (size[name] > MAX_LOG) stopChild('CHILD_OUTPUT_BOUND')
  })
  let code, signal
  try {
    [code, signal] = await new Promise((resolve, reject) => { child.once('error', reject); child.once('close', (...result) => resolve(result)) })
  } finally { clearTimeout(timeout); clearTimeout(force) }
  let groupAbsent = false
  for (let retry = 0; retry < 100; retry++) {
    try { process.kill(-child.pid, 0) } catch (error) { if (error.code === 'ESRCH') { groupAbsent = true; break } throw error }
    await new Promise(resolve => setTimeout(resolve, 20))
  }
  if (!groupAbsent) { try { process.kill(-child.pid, 'SIGKILL') } catch {} }
  const out = Buffer.concat(streams.stdout)
  const err = Buffer.concat(streams.stderr)
  console.log('CUMULATIVE_LANDING_PHASE', JSON.stringify({ name, native_exit: code, signal, stopped_reason: stop,
    child_group_absent: groupAbsent, stdout_bytes: size.stdout, stderr_bytes: size.stderr,
    stdout_sha256: sha(out), stderr_sha256: sha(err) }))
  // All logs originate in the isolated, public/synthetic source environment.
  console.log(`CUMULATIVE_LANDING_STDOUT ${name}\n${out.toString('utf8')}\nCUMULATIVE_LANDING_STDERR ${name}\n${err.toString('utf8')}\nCUMULATIVE_LANDING_END ${name}`)
  checkCgroup()
  requireCheck(code === 0 && signal === null && stop === null && groupAbsent, 'NATIVE_PHASE_FAILED')
  return out.toString('utf8')
}

function checkGenerated(before, after) {
  if (before.equals(after)) return false
  const tidy = bytes => bytes.toString('utf8').trim().replace(/\r\n/g, '\n')
  const variant = text => text.replace(/^\/\/\/ <reference path="\.\/\.next\/types\/routes\.d\.ts" \/>$/m,
    'import "./.next/types/routes.d.ts";')
  requireCheck(variant(tidy(before)) === variant(tidy(after)), 'UNEXPECTED_GENERATED_TYPE_SOURCE_CHANGE')
  return true
}

async function main() {
  requireCheck(process.cwd() === WORK && process.argv.length === 2 && process.platform === 'linux' && process.arch === 'x64' &&
    process.version === 'v24.22.1' && JSON.parse(fs.readFileSync('/usr/local/lib/node_modules/npm/package.json', 'utf8')).version === '11.19.1', 'EXACT_RUNTIME_REQUIRED')
  requireCheck(fs.readdirSync('/sys/class/net').length === 1 && fs.existsSync('/sys/class/net/lo'), 'LOOPBACK_ONLY_REQUIRED')
  fs.mkdirSync('/tmp/landing-home')
  fs.writeFileSync('/tmp/landing-home/.npmrc', '', { flag: 'wx' })
  fs.writeFileSync('/tmp/landing-global-npmrc', '', { flag: 'wx' })
  const fixtureContract = await phase('fixture_contract', ['--test', '--test-concurrency=1', '--test-reporter=tap', '/fixture/landing_fixture.test.cjs'], 30, 192)
  requireCheck(/^# tests 4$/m.test(fixtureContract) && /^# pass 4$/m.test(fixtureContract) &&
    /^# fail 0$/m.test(fixtureContract) && /^# skipped 0$/m.test(fixtureContract), 'FIXTURE_CONTRACT_NOT_ACCEPTED')
  const before = fs.readFileSync(GENERATED)
  const fixture = await startFixture(8917)
  const counts = { inventory: 0, detail: 0, refused: 0, total: 0 }
  fixture.server.prependListener('request', (request, response) => {
    counts.total++
    if (counts.total > 128) { fixture.server.closeAllConnections(); return }
    response.once('finish', () => {
      if (response.statusCode !== 200) counts.refused++
      else if (request.url.startsWith('/listings?')) counts.inventory++
      else if (request.url === `/listings/${LISTING_ID}`) counts.detail++
    })
  })
  let generated = false
  try {
    await phase('landing_declared_full_build_synthetic_input', [NPM, 'run', 'build', '--workspace=landing'], 480, 1536)
    await phase('landing_full_strict_typecheck_with_generated_routes', [NPM, 'run', 'typecheck', '--workspace=landing'], 90, 2048)
    generated = checkGenerated(before, fs.readFileSync(GENERATED))
    const output = `${WORK}/apps/landing/out`
    const html = fs.readFileSync(`${output}/listing/${LISTING_ID}/index.html`, 'utf8')
    const sitemap = fs.readFileSync(`${output}/sitemap.xml`, 'utf8')
    requireCheck(html.includes('Synthetic CI villa') && html.includes('application/ld+json') && html.includes('"@type":"Product"') &&
      sitemap.includes(`/listing/${LISTING_ID}/`) && fs.statSync(`${output}/index.html`).isFile(), 'SYNTHETIC_EXPORT_AND_METADATA_REQUIRED')
    requireCheck(counts.inventory >= 1 && counts.detail >= 1 && counts.refused === 0 && counts.total <= 128 &&
      counts.total === counts.inventory + counts.detail, 'ACTUAL_SYNTHETIC_REQUESTS_REQUIRED')
  } finally {
    await fixture.close()
    requireCheck(!fixture.server.listening, 'FIXTURE_CLOSE_REQUIRED')
  }
  // Restore only the reviewed Next-generated declaration in scratch; the host then checks all 473 inputs again.
  if (generated) fs.writeFileSync(GENERATED, before)
  console.log('CUMULATIVE_LANDING_RESULT', JSON.stringify({ status: 'PASS', fixture: 'loopback-only', counts,
    generated_next_env_checked_and_restored_if_changed: generated, fixture_closed: true,
    synthetic_listing_export_and_structured_metadata: true, output_must_not_be_deployed: true }))
}

main().catch(error => { console.error(`CUMULATIVE_LANDING_FAILED ${error.message}`); process.exitCode = 1 })
