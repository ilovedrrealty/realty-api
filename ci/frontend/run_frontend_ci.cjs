'use strict'
// CI-only. Unmodified application builds, synthetic input, no deployable output.
const fs = require('node:fs')
const path = require('node:path')
const crypto = require('node:crypto')
const { spawn } = require('node:child_process')
const { startFixture, LISTING_ID } = require('./landing_fixture.cjs')
const WORK = '/work', SOURCE = '/source', CI = '/ci'
const NPM = '/usr/local/lib/node_modules/npm/bin/npm-cli.js'
const MAX_LOG = 2 * 1024 * 1024
const manifest = JSON.parse(fs.readFileSync(`${CI}/frontend-overlay.json`, 'utf8'))
const mode = process.argv[2]
const report = { schema_version: 1, mode, status: 'FAIL', source_tree: manifest.frontend_candidate_tree,
  ci_commit: process.env.FE_CI_COMMIT, run_id: process.env.FE_CI_RUN_ID, attempt: 1,
  phases: [], source_unchanged: false, strict_configs_unchanged: false,
  fixture: null, generated_next_env_changed: false, real_api_provider_or_production_actions: false,
  live_listing_publication_proven: false, build_outputs_confined_to_owned_volume: true,
  outputs_uploaded_cached_or_deployed: false }
function need(ok, code) { if (!ok) throw new Error(code) }
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex')
function file(relative, root) {
  need(typeof relative === 'string' && !relative.includes('\\') && !relative.startsWith('/')
    && relative.split('/').every(part => part && part !== '.' && part !== '..'), 'SOURCE_PATH_REFUSED')
  let current = root
  for (const part of relative.split('/')) {
    current = path.join(current, part)
    need(!fs.lstatSync(current).isSymbolicLink(), 'SOURCE_SYMLINK_REFUSED')
  }
  need(fs.statSync(current).isFile(), 'SOURCE_REGULAR_FILE_REQUIRED')
  return current
}
function sourceCheck(root, allowGenerated = false) {
  let changedNextEnv = false
  for (const [relative, expected] of Object.entries(manifest.application_files)) {
    const bytes = fs.readFileSync(file(relative, root))
    const equal = bytes.length === expected.bytes && sha(bytes) === expected.sha256
    if (allowGenerated && relative === 'apps/landing/next-env.d.ts' && !equal) changedNextEnv = true
    else need(equal, 'SOURCE_BYTES_CHANGED')
  }
  return changedNextEnv
}
function cgroup() {
  need(fs.readFileSync('/sys/fs/cgroup/memory.max', 'utf8').trim() === '8589934592', 'EXACT_8_GIB_CGROUP_REQUIRED')
  const events = Object.fromEntries(fs.readFileSync('/sys/fs/cgroup/memory.events', 'utf8').trim().split('\n').map(line => {
    const [key, value] = line.split(' '); return [key, Number(value)]
  }))
  need(Number.isSafeInteger(events.oom_kill) && events.oom_kill === 0, 'CGROUP_OOM_KILL_REFUSED')
  return events
}
function cleanEnv(heap, install = false) {
  return { PATH: '/usr/local/bin:/usr/bin:/bin', HOME: '/tmp/frontend-home', CI: 'true',
    NODE_OPTIONS: `--max-old-space-size=${heap}`, NEXT_TELEMETRY_DISABLED: '1', TURBO_TELEMETRY_DISABLED: '1',
    npm_config_userconfig: '/tmp/frontend-home/.npmrc', npm_config_globalconfig: '/tmp/frontend-global-npmrc',
    npm_config_registry: 'https://registry.npmjs.org/', npm_config_audit: 'false', npm_config_fund: 'false',
    npm_config_cache: '/work/.npm-cache', npm_config_ignore_scripts: install ? 'true' : 'false', npm_config_update_notifier: 'false',
    NEXT_PUBLIC_API_URL: 'http://127.0.0.1:8917', VITE_API_URL: 'http://127.0.0.1:8917',
    NEXT_PUBLIC_GOOGLE_CLIENT_ID: 'synthetic-ci.apps.googleusercontent.com',
    VITE_GOOGLE_CLIENT_ID: 'synthetic-ci.apps.googleusercontent.com' }
}
async function phase(name, args, heap, seconds = 300, install = false) {
  cgroup()
  const began = Date.now(), child = spawn(process.execPath, args, { cwd: WORK,
    env: cleanEnv(heap, install), stdio: ['ignore', 'pipe', 'pipe'], detached: true })
  const output = { stdout: { chunks: [], bytes: 0, hash: crypto.createHash('sha256') },
    stderr: { chunks: [], bytes: 0, hash: crypto.createHash('sha256') } }
  let killed = null, settled = false
  const kill = reason => {
    if (settled || killed) return
    killed = reason
    try { process.kill(-child.pid, 'SIGTERM') } catch (error) { if (error.code !== 'ESRCH') killed = 'CHILD_STOP_UNVERIFIED' }
    force = setTimeout(() => { try { process.kill(-child.pid, 'SIGKILL') } catch {} }, 2000)
  }
  let force
  const timer = setTimeout(() => kill('CHILD_DEADLINE'), seconds * 1000)
  const capture = (stream, data) => {
    const item = output[stream], remaining = Math.max(0, MAX_LOG - item.bytes)
    item.hash.update(data); item.bytes += data.length
    if (remaining) item.chunks.push(data.subarray(0, remaining))
    if (item.bytes > MAX_LOG) kill('CHILD_OUTPUT_BOUND')
  }
  child.stdout.on('data', data => capture('stdout', data))
  child.stderr.on('data', data => capture('stderr', data))
  let native
  try {
    native = await new Promise((resolve, reject) => { child.once('error', reject); child.once('close', (code, signal) => resolve({ code, signal })) })
  } finally { settled = true; clearTimeout(timer); clearTimeout(force) }
  let groupAbsent = false
  for (let wait = 0; wait < 100; wait++) {
    try { process.kill(-child.pid, 0) } catch (error) { if (error.code === 'ESRCH') { groupAbsent = true; break }; throw error }
    await new Promise(resolve => setTimeout(resolve, 20))
  }
  if (!groupAbsent) { try { process.kill(-child.pid, 'SIGKILL') } catch {} }
  const stdout = Buffer.concat(output.stdout.chunks).toString('utf8'), stderr = Buffer.concat(output.stderr.chunks).toString('utf8')
  const entry = { name, native_exit: native.code, signal: native.signal, stopped_reason: killed, child_group_absent: groupAbsent,
    elapsed_ms: Date.now() - began, stdout_bytes: output.stdout.bytes, stderr_bytes: output.stderr.bytes,
    stdout_sha256: output.stdout.hash.digest('hex'), stderr_sha256: output.stderr.hash.digest('hex') }
  report.phases.push(entry)
  process.stdout.write(`FRONTEND_PHASE_BEGIN ${name}\n` + stdout.slice(0, MAX_LOG))
  process.stdout.write(`\nFRONTEND_PHASE_STDERR ${name}\n` + stderr.slice(0, MAX_LOG) + `\nFRONTEND_PHASE_END ${name}\n`)
  cgroup()
  need(native.code === 0 && native.signal === null && killed === null && groupAbsent, 'PHASE_NATIVE_OR_REAP_FAILURE')
  return stdout
}
function tap(stdout, count) {
  for (const [name, value] of Object.entries({ tests: count, pass: count, fail: 0, cancelled: 0, skipped: 0, todo: 0 })) {
    const lines = [...stdout.matchAll(new RegExp(`^# ${name} ([0-9]+)$`, 'gm'))]
    need(lines.length === 1 && Number(lines[0][1]) === value, 'EXACT_TEST_COUNTS_REQUIRED')
  }
  const names = [...stdout.matchAll(/^ok [0-9]+ - (.+)$/gm)].map(match => match[1])
  need(names.length === count && new Set(names).size === count, 'UNIQUE_ACTUAL_TEST_NAMES_REQUIRED')
  return names
}
async function main() {
  need(['install', 'validate'].includes(mode) && process.argv.length === 3, 'EXACT_MODE_REQUIRED')
  need(process.platform === 'linux' && process.arch === 'x64' && process.version === 'v24.22.1', 'EXACT_LINUX_NODE_REQUIRED')
  need(process.cwd() === WORK && /^[a-f0-9]{40}$/.test(report.ci_commit || '')
    && /^[0-9]+$/.test(report.run_id || '') && /^[a-f0-9]{40}$/.test(report.source_tree || ''), 'FIXED_CI_IDENTITY_REQUIRED')
  need(Object.keys(manifest.application_files).length === 425 && Object.keys(manifest.overlays).length === 48, 'EXACT_SOURCE_SCOPE_REQUIRED')
  fs.mkdirSync('/tmp/frontend-home', { recursive: true })
  fs.writeFileSync('/tmp/frontend-home/.npmrc', '', { flag: 'wx' })
  fs.writeFileSync('/tmp/frontend-global-npmrc', '', { flag: 'wx' })
  report.cgroup_before = cgroup()
  sourceCheck(SOURCE)
  const tool = JSON.parse(fs.readFileSync('/usr/local/lib/node_modules/npm/package.json', 'utf8'))
  need(tool.version === '11.19.1', 'EXACT_BUNDLED_NPM_REQUIRED')
  report.node = process.version; report.npm = tool.version
  report.source_package_manager = 'npm@10.7.0'
  report.toolchain_distinction = 'Pinned Linux Node image bundles npm11.19.1; declared npm10.7.0 field is preserved, not represented as the executable version.'
  if (mode === 'install') {
    need(fs.readdirSync(WORK).length === 0, 'EMPTY_OWNED_VOLUME_REQUIRED')
    for (const [relative, expected] of Object.entries(manifest.application_files)) {
      need(!/(^|\/)\.env($|\.)/.test(relative) || relative.endsWith('/.env.example'), 'ENV_SOURCE_REFUSED')
      need(!relative.endsWith('.npmrc'), 'NPM_CONFIG_SOURCE_REFUSED')
      const target = path.join(WORK, relative); fs.mkdirSync(path.dirname(target), { recursive: true })
      fs.copyFileSync(file(relative, SOURCE), target, fs.constants.COPYFILE_EXCL)
      fs.chmodSync(target, expected.mode === '100755' ? 0o755 : 0o644)
    }
    sourceCheck(WORK)
    await phase('locked_dependency_install_no_lifecycle', [NPM, 'ci', '--ignore-scripts', '--no-audit', '--no-fund', '--include=dev', '--include=optional'], 1024, 360, true)
    fs.rmSync('/work/.npm-cache', { recursive: true, force: true })
    sourceCheck(WORK)
    const lock = JSON.parse(fs.readFileSync('/work/package-lock.json', 'utf8'))
    report.installed_tools = {}
    for (const name of ['typescript', 'react', 'react-dom', 'next', 'vite', '@next/swc-linux-x64-gnu', '@rollup/rollup-linux-x64-gnu', '@esbuild/linux-x64', '@tailwindcss/oxide-linux-x64-gnu']) {
      const key = `node_modules/${name}`, installed = JSON.parse(fs.readFileSync(`/work/${key}/package.json`, 'utf8'))
      need(installed.version === lock.packages[key].version, 'LOCKED_LINUX_TOOL_VERSION_REQUIRED')
      report.installed_tools[name] = installed.version
    }
  } else {
    sourceCheck(WORK)
    report.fixture_contract_test_names = tap(await phase('fixture_contract_4', ['--test', '--test-concurrency=1', '--test-reporter=tap', '/ci/landing_fixture.test.cjs'], 192), 4)
    const currentTests = ['tests/network-freshness.test.cjs', 'tests/property-network.test.cjs', 'tests/property-network-ui.test.cjs']
    const expected = currentTests.flatMap(relative =>
      [...fs.readFileSync(`/work/${relative}`, 'utf8').matchAll(/^test\('([^']+)'/gm)].map(match => match[1]))
    need(expected.length === 53 && new Set(expected).size === 53, 'EXACT_53_SOURCE_DEFINITIONS_REQUIRED')
    const actual = tap(await phase('current_53_contract_pure_and_mocked_tsx', ['--test', '--test-concurrency=1', '--test-reporter=tap', ...currentTests], 192), 53)
    need(expected.every(name => actual.includes(name)), 'ALL_CURRENT_53_NAMES_REQUIRED')
    report.current_test_names = actual
    await phase('dashboard_full_strict_typecheck', [NPM, 'run', 'typecheck', '--workspace=dashboard'], 2048)
    await phase('dashboard_declared_full_build', [NPM, 'run', 'build', '--workspace=dashboard'], 1536)
    const fixture = await startFixture(8917)
    const address = fixture.server.address()
    need(address && address.address === '127.0.0.1' && address.port === 8917, 'OWNED_FIXTURE_BINDING_REQUIRED')
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
    try {
      await phase('landing_declared_full_build_synthetic_input', [NPM, 'run', 'build', '--workspace=landing'], 1536, 480)
      await phase('landing_full_strict_typecheck_with_generated_routes', [NPM, 'run', 'typecheck', '--workspace=landing'], 2048)
      need(counts.inventory >= 1 && counts.detail >= 1 && counts.refused === 0 && counts.total <= 128, 'ACTUAL_SYNTHETIC_ROUTE_READS_REQUIRED')
      const html = fs.readFileSync(`/work/apps/landing/out/listing/${LISTING_ID}/index.html`, 'utf8')
      const sitemap = fs.readFileSync('/work/apps/landing/out/sitemap.xml', 'utf8')
      need(html.includes('Synthetic CI villa') && html.includes('application/ld+json') && html.includes('"@type":"Product"')
        && sitemap.includes(`/listing/${LISTING_ID}/`), 'SYNTHETIC_EXPORT_ROUTE_AND_METADATA_REQUIRED')
      need(fs.statSync('/work/apps/dashboard/dist/index.html').isFile(), 'DASHBOARD_OUTPUT_REQUIRED')
      report.fixture = { host: '127.0.0.1', port: 8917, inventory_count: 1, counts,
        synthetic_route_and_metadata_checked: true, output_must_never_be_deployed: true, closed: false }
    } finally {
      await fixture.close()
      need(fixture.server.listening === false, 'FIXTURE_CLOSE_REQUIRED')
      if (report.fixture) report.fixture.closed = true
    }
    report.generated_next_env_changed = sourceCheck(WORK, true)
  }
  sourceCheck(SOURCE)
  report.source_unchanged = true; report.strict_configs_unchanged = true
  report.cgroup_after = cgroup()
  report.status = mode === 'install' ? 'PASS_LOCKED_INSTALL_NO_LIFECYCLE' : 'PASS_FULL_CHECKS_SYNTHETIC_LANDING_INPUT_NOT_RELEASE'
}
main().catch(error => { report.failure_code = /^[A-Z0-9_]+$/.test(error.message) ? error.message : 'UNCLASSIFIED_ENTRY_FAILURE'; process.exitCode = 1 })
  .finally(() => { process.stdout.write('FRONTEND_CI_RESULT '+JSON.stringify(report)+'\n') })
