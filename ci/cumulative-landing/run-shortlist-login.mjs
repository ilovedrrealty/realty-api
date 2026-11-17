import http from 'node:http'
import path from 'node:path'
import { randomBytes } from 'node:crypto'
import { readFile, readdir, realpath, stat } from 'node:fs/promises'
import { chromium } from 'playwright-core'

const host = '127.0.0.1'
const landing = `http://${host}:8918`
const api = `http://${host}:8917`
const dashboard = `http://${host}:4175`
const exportRoot = '/landing-export'
const grant = 'b357412c-d096-47d6-8a77-30cf544e8d97'
const loginPath = `/login/?shortlist=${grant}`
const destination = `/shortlists?grant=${grant}`
const email = 'shortlist-login-fixture@example.invalid'
const password = randomBytes(24).toString('base64url')
const token = randomBytes(24).toString('base64url')
const observations = { signedOutReads: 0, signedInReads: 0, rejectedLogin: 0, acceptedLogin: 0, destinationWithCookie: 0 }
const mime = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.json': 'application/json', '.svg': 'image/svg+xml',
  '.txt': 'text/plain; charset=utf-8', '.png': 'image/png', '.jpg': 'image/jpeg',
  '.ico': 'image/x-icon', '.woff2': 'font/woff2', '.webp': 'image/webp',
}
let stage = 'host'

function check(condition, code) {
  if (!condition) throw new Error(code)
}

function json(response, status, body, headers = {}) {
  response.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store', ...headers })
  response.end(JSON.stringify(body))
}

function hasCookie(request) {
  return (request.headers.cookie || '').split(';').map(value => value.trim()).includes(`ildr_token=${token}`)
}

async function credentialBody(request) {
  let length = 0
  const chunks = []
  for await (const chunk of request) {
    length += chunk.length
    if (length > 1024) return null
    chunks.push(chunk)
  }
  try { return JSON.parse(Buffer.concat(chunks).toString('utf8')) } catch { return null }
}

async function serveAuth(request, response) {
  if (request.headers.origin !== landing) return json(response, 403, { detail: 'Loopback fixture origin required' })
  const cors = {
    'Access-Control-Allow-Origin': landing, 'Access-Control-Allow-Credentials': 'true',
    'Access-Control-Allow-Headers': 'Content-Type', 'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    Vary: 'Origin',
  }
  const key = `${request.method} ${request.url}`
  if (request.method === 'OPTIONS') {
    response.writeHead(204, cors)
    return response.end()
  }
  if (key === 'GET /auth/me') {
    if (!hasCookie(request)) {
      observations.signedOutReads++
      return json(response, 401, { detail: 'Fixture user is signed out' }, cors)
    }
    observations.signedInReads++
    return json(response, 200, { id: '62616133-85ef-401b-bcc2-f242dca7f46f', email, role: 'buyer',
      display_name: 'Synthetic Buyer', phone: null, avatar_url: null }, cors)
  }
  if (key === 'POST /auth/login') {
    const credentials = await credentialBody(request)
    if (credentials?.email !== email || credentials?.password !== password) {
      observations.rejectedLogin++
      return json(response, 401, { detail: 'Synthetic sign-in rejected' }, cors)
    }
    observations.acceptedLogin++
    return json(response, 200, { expires_in: 900 },
      { ...cors, 'Set-Cookie': `ildr_token=${token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=900` })
  }
  if (key === 'GET /stats/platform') {
    return json(response, 200, { active_listings: 1, registered_users: 1, total_value: 100000, realtors: 0 }, cors)
  }
  return json(response, 404, { detail: 'Outside synthetic auth contract' }, cors)
}

async function serveExport(request, response) {
  if (request.method !== 'GET' && request.method !== 'HEAD') return json(response, 405, {})
  const pathname = decodeURIComponent(new URL(request.url, landing).pathname)
  const filename = path.resolve(exportRoot, '.' + pathname + (pathname.endsWith('/') ? 'index.html' : ''))
  if (!filename.startsWith(exportRoot + '/')) return json(response, 404, {})
  const content = await stat(filename).catch(() => null)
  if (!content?.isFile() || content.size > 20 * 1024 * 1024 || await realpath(filename) !== filename) {
    return json(response, 404, {})
  }
  response.writeHead(200, { 'Content-Type': mime[path.extname(filename)] || 'application/octet-stream' })
  return response.end(request.method === 'HEAD' ? undefined : await readFile(filename))
}

function serveDestination(request, response) {
  if (request.method !== 'GET' || request.url !== destination || !hasCookie(request)) return json(response, 404, {})
  observations.destinationWithCookie++
  response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' })
  return response.end('<!doctype html><title>Synthetic shortlist destination</title><h1>Shortlist destination verified</h1>')
}

async function listen(url, handler, servers) {
  const server = http.createServer((request, response) => {
    Promise.resolve(handler(request, response)).catch(() => {
      if (!response.headersSent) json(response, 500, {})
      else response.destroy()
    })
  })
  servers.push(server)
  await new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(Number(new URL(url).port), host, resolve)
  })
}

async function main() {
  check(process.platform === 'linux' && process.env.GITHUB_ACTIONS === 'true'
    && process.env.GITHUB_REPOSITORY === 'ilovedrrealty/realty-api'
    && (await readdir('/sys/class/net')).join(',') === 'lo', 'HOSTED_LOOPBACK_REQUIRED')
  const servers = []
  let browser
  try {
    stage = 'static_export'
    check(await realpath(exportRoot + '/login/index.html') === exportRoot + '/login/index.html'
      && (await stat(exportRoot + '/login/index.html')).isFile(), 'EXACT_LOGIN_EXPORT_REQUIRED')
    await listen(landing, serveExport, servers)
    await listen(api, serveAuth, servers)
    await listen(dashboard, serveDestination, servers)

    stage = 'browser'
    browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
    const context = await browser.newContext({ locale: 'en-US', viewport: { width: 1280, height: 900 }, serviceWorkers: 'block' })
    context.setDefaultTimeout(12_000)
    await context.route('**/*', route => [landing, api, dashboard].includes(new URL(route.request().url()).origin)
      ? route.continue() : route.abort('blockedbyclient'))
    await context.addInitScript(() => {
      if (!navigator.onLine) Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => true })
    })
    const page = await context.newPage()
    function apiResponse(method, route, status) {
      return page.waitForResponse(response => response.url() === api + route
        && response.request().method() === method && response.status() === status)
    }

    stage = 'anonymous_login'
    const unsigned = apiResponse('GET', '/auth/me', 401)
    const rendered = await page.goto(landing + loginPath, { waitUntil: 'domcontentloaded' })
    check(rendered?.ok() && page.url() === landing + loginPath, 'LOGIN_DOCUMENT_REQUIRED')
    await unsigned
    const formHeading = page.getByRole('heading', { name: 'Log in', exact: true })
    await formHeading.waitFor()
    const panel = formHeading.locator('..')
    await panel.locator('input[type=email]').fill(email)
    const passwordControl = panel.locator('input[type=password]')
    const submit = panel.getByRole('button', { name: 'Log in', exact: true })

    stage = 'wrong_password'
    await passwordControl.fill('deliberately-incorrect-synthetic-password')
    const failed = apiResponse('POST', '/auth/login', 401)
    await submit.click()
    await failed
    await page.getByText('Synthetic sign-in rejected', { exact: true }).waitFor()
    check(page.url() === landing + loginPath && !(await context.cookies(api)).some(cookie => cookie.name === 'ildr_token'),
      'REJECTED_LOGIN_MUST_STAY_SIGNED_OUT')

    stage = 'form_login_redirect'
    await passwordControl.fill(password)
    const login = apiResponse('POST', '/auth/login', 200)
    const redirected = page.waitForURL(dashboard + destination)
    await submit.click()
    await login
    await redirected
    const landingMarker = page.getByRole('heading', { name: 'Shortlist destination verified', exact: true })
    await landingMarker.waitFor()
    const cookies = (await context.cookies(api)).filter(cookie => cookie.name === 'ildr_token')
    check(cookies.length === 1 && cookies[0].httpOnly && cookies[0].value === token, 'ACTUAL_HTTPONLY_COOKIE_REQUIRED')

    stage = 'existing_session_redirect'
    const signed = apiResponse('GET', '/auth/me', 200)
    const reopened = await page.goto(landing + loginPath, { waitUntil: 'commit' })
    check(reopened?.ok(), 'LOGIN_REVISIT_REQUIRED')
    await signed
    await page.waitForURL(dashboard + destination)
    await landingMarker.waitFor()
    check(observations.signedOutReads >= 1 && observations.signedInReads >= 1
      && observations.rejectedLogin === 1 && observations.acceptedLogin === 1
      && observations.destinationWithCookie === 2, 'SERVER_SIDE_SESSION_EVIDENCE_REQUIRED')
  } finally {
    try { if (browser) await browser.close() } finally {
      await Promise.all(servers.map(server => new Promise(resolve => {
        server.close(resolve)
        server.closeAllConnections()
      })))
    }
    check(servers.length === 3 && servers.every(server => !server.listening), 'LOOPBACK_CLEANUP_REQUIRED')
  }
  console.log('SHORTLIST_LANDING_LOGIN_BROWSER', JSON.stringify({ status: 'PASS', actual_export: true,
    synthetic_auth_endpoint: true, real_candidate_backend_in_this_container: false,
    rejected_password_without_cookie: true, http_only_cookie: true, exact_grant_redirect: true,
    existing_session_redirect: true, loopback_only: true, fixtures_closed: true }))
}

main().catch(() => {
  console.error(`SHORTLIST_LANDING_LOGIN_FAILED stage=${stage}`)
  process.exitCode = 1
})
