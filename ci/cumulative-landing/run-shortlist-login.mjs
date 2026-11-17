import http from 'node:http'
import path from 'node:path'
import { randomBytes } from 'node:crypto'
import { readFile, readdir, realpath, stat } from 'node:fs/promises'
import { chromium } from 'playwright-core'
import listingFixture from '/listing-fixture/landing_fixture.cjs'
import { createEditorialFixture, verifyEditorial } from './editorial-browser.mjs'
import { createSpatialFixture, verifySpatial } from './spatial-browser.mjs'
import { createDevelopmentFixture, verifyDevelopment } from './development-browser.mjs'
import { createSellerValuationFixture, verifySellerValuation } from './seller-valuation-browser.mjs'

const host = '127.0.0.1'
const landing = `http://${host}:8918`
const api = `http://${host}:8917`
const dashboard = `http://${host}:4175`
const exportRoot = '/landing-export'
const editorial = createEditorialFixture(api)
const spatialSearch = createSpatialFixture(api)
const { LISTING_ID: listing, fixtureResponse } = listingFixture
const development = createDevelopmentFixture(api, listing)
const sellerValuation = createSellerValuationFixture()
const listingPath = `/listing/${listing}/`
const grant = 'b357412c-d096-47d6-8a77-30cf544e8d97'
const shortlist = { loginPath: `/login/?shortlist=${grant}`, destination: `/shortlists?grant=${grant}` }
const viewing = { loginPath: `/login/?viewing=${listing}`, destination: `/viewings?listing=${listing}` }
const languages = {
  en: { locale: 'en-US', signIn: 'Log in', viewingLink: 'Request a viewing' },
  es: { locale: 'es-DO', signIn: 'Iniciar sesión', viewingLink: 'Solicitar una visita' },
}
const email = 'shortlist-login-fixture@example.invalid'
const password = randomBytes(24).toString('base64url')
const token = randomBytes(24).toString('base64url')
const observations = { signedOutReads: 0, signedInReads: 0, rejectedLogin: 0, acceptedLogin: 0, listingReads: 0 }
const destinationCounts = new Map([[shortlist.destination, 0], [viewing.destination, 0]])
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
  const editorialResult = editorial.responseFor(request)
  if (editorialResult) return json(response, editorialResult.status, editorialResult.data, cors)
  const spatialResult = spatialSearch.responseFor(request)
  if (spatialResult) return json(response, spatialResult.status, spatialResult.data, cors)
  const developmentResult = development.responseFor(request)
  if (developmentResult) return json(response, developmentResult.status, developmentResult.data, cors)
  const sellerResult = await sellerValuation.responseFor(request)
  if (sellerResult) return json(response, sellerResult.status, sellerResult.data, cors)
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
  if (key === `GET /listings/${listing}`) {
    const result = fixtureResponse('GET', request.url, { host: `${host}:8917` }, 8917)
    const detail = JSON.parse(result.body)
    check(result.status === 200 && detail.id === listing, 'EXACT_LOOPBACK_LISTING_REQUIRED')
    observations.listingReads++
    // The hydrated listing view needs these arrays in addition to the static export input.
    return json(response, 200, { ...detail, features: [], video_links: [], included_utilities: [], status: 'active' }, cors)
  }
  if (key === `POST /listings/${listing}/view`) return json(response, 200, {}, cors)
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
  if (request.method !== 'GET' || !destinationCounts.has(request.url) || !hasCookie(request)) return json(response, 404, {})
  destinationCounts.set(request.url, destinationCounts.get(request.url) + 1)
  response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' })
  return response.end('<!doctype html><title>Synthetic dashboard destination</title><h1>Dashboard destination verified</h1>')
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

async function verifyLogin(browser, flow, language, verifyRejectedPassword = false) {
  const { locale, signIn, viewingLink } = languages[language]
  const context = await browser.newContext({ locale, viewport: { width: 1280, height: 900 }, serviceWorkers: 'block' })
  try {
    context.setDefaultTimeout(12_000)
    await context.route('**/*', route => [landing, api, dashboard].includes(new URL(route.request().url()).origin)
      ? route.continue() : route.abort('blockedbyclient'))
    await context.addInitScript(key => {
      if (!navigator.onLine) Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => true })
      if (window.location.protocol === 'http:') localStorage.setItem('ildr_lang', key)
    }, language)
    const page = await context.newPage()
    const apiResponse = (method, route, status) => page.waitForResponse(response => response.url() === api + route
      && response.request().method() === method && response.status() === status)
    const dashboardURL = dashboard + flow.destination
    if (flow === viewing) {
      stage = `viewing_listing_${language}`
      const detail = apiResponse('GET', `/listings/${listing}`, 200)
      const exported = await page.goto(landing + listingPath, { waitUntil: 'domcontentloaded' })
      check(exported?.ok() && page.url() === landing + listingPath, 'ACTUAL_EXPORTED_LISTING_REQUIRED')
      await detail
      const link = page.getByRole('link', { name: viewingLink, exact: true })
      await link.waitFor()
      check(await link.getAttribute('href') === dashboardURL, 'VIEWING_CTA_DASHBOARD_AND_LISTING_REQUIRED')
    }

    stage = 'anonymous_login'
    const unsigned = apiResponse('GET', '/auth/me', 401)
    const rendered = await page.goto(landing + flow.loginPath, { waitUntil: 'domcontentloaded' })
    check(rendered?.ok() && page.url() === landing + flow.loginPath, 'LOGIN_DOCUMENT_REQUIRED')
    await unsigned
    const formHeading = page.getByRole('heading', { name: signIn, exact: true })
    await formHeading.waitFor()
    const panel = formHeading.locator('..')
    await panel.locator('input[type=email]').fill(email)
    const passwordControl = panel.locator('input[type=password]')
    const submit = panel.getByRole('button', { name: signIn, exact: true })

    if (verifyRejectedPassword) {
      stage = 'wrong_password'
      await passwordControl.fill('deliberately-incorrect-synthetic-password')
      const failed = apiResponse('POST', '/auth/login', 401)
      await submit.click()
      await failed
      await page.getByText('Synthetic sign-in rejected', { exact: true }).waitFor()
      check(page.url() === landing + flow.loginPath && !(await context.cookies(api)).some(cookie => cookie.name === 'ildr_token'),
        'REJECTED_LOGIN_MUST_STAY_SIGNED_OUT')
    }

    stage = 'form_login_redirect'
    await passwordControl.fill(password)
    const login = apiResponse('POST', '/auth/login', 200)
    const redirected = page.waitForURL(dashboardURL)
    await submit.click()
    await login
    await redirected
    const marker = page.getByRole('heading', { name: 'Dashboard destination verified', exact: true })
    await marker.waitFor()
    const cookies = (await context.cookies(api)).filter(cookie => cookie.name === 'ildr_token')
    check(cookies.length === 1 && cookies[0].httpOnly && cookies[0].value === token, 'ACTUAL_HTTPONLY_COOKIE_REQUIRED')

    stage = 'existing_session_redirect'
    const signed = apiResponse('GET', '/auth/me', 200)
    const reopened = await page.goto(landing + flow.loginPath, { waitUntil: 'commit' })
    check(reopened?.ok(), 'LOGIN_REVISIT_REQUIRED')
    await signed
    await page.waitForURL(dashboardURL)
    await marker.waitFor()
  } finally {
    await context.close()
  }
}

async function main() {
  check(process.platform === 'linux' && process.env.GITHUB_ACTIONS === 'true'
    && process.env.GITHUB_REPOSITORY === 'ilovedrrealty/realty-api'
    && (await readdir('/sys/class/net')).join(',') === 'lo', 'HOSTED_LOOPBACK_REQUIRED')
  const servers = []
  let browser
  let editorialResult
  let spatialResult
  let developmentResult
  let sellerValuationResult
  try {
    stage = 'static_export'
    check(await realpath(exportRoot + '/login/index.html') === exportRoot + '/login/index.html'
      && (await stat(exportRoot + '/login/index.html')).isFile(), 'EXACT_LOGIN_EXPORT_REQUIRED')
    check(await realpath(exportRoot + listingPath + 'index.html') === exportRoot + listingPath + 'index.html'
      && (await stat(exportRoot + listingPath + 'index.html')).isFile(), 'EXACT_LISTING_EXPORT_REQUIRED')
    check(listing === '10000000-0000-0000-0000-000000000001', 'EXPECTED_SYNTHETIC_LISTING_REQUIRED')
    await listen(landing, serveExport, servers)
    await listen(api, serveAuth, servers)
    await listen(dashboard, serveDestination, servers)

    stage = 'browser'
    browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
    await verifyLogin(browser, shortlist, 'en', true)
    await verifyLogin(browser, viewing, 'en')
    await verifyLogin(browser, viewing, 'es')
    check(observations.signedOutReads >= 3 && observations.signedInReads >= 3 && observations.listingReads >= 2
      && observations.rejectedLogin === 1 && observations.acceptedLogin === 3
      && destinationCounts.get(shortlist.destination) === 2 && destinationCounts.get(viewing.destination) === 4,
      'SERVER_SIDE_SESSION_EVIDENCE_REQUIRED')
    stage = 'editorial_export'
    editorialResult = await verifyEditorial(browser, editorial, { landing, api }, exportRoot, next => { stage = next })
    stage = 'spatial_export'
    spatialResult = await verifySpatial(browser, spatialSearch, { landing, api }, next => { stage = next })
    stage = 'development_export'
    developmentResult = await verifyDevelopment(browser, development, { landing, api }, exportRoot, listing,
      next => { stage = next })
    stage = 'seller_export'
    sellerValuationResult = await verifySellerValuation(browser, sellerValuation, { landing, api }, exportRoot,
      next => { stage = next })
  } finally {
    try { if (browser) await browser.close() } finally {
      await Promise.all(servers.map(server => new Promise(resolve => {
        server.close(resolve)
        server.closeAllConnections()
      })))
    }
    check(servers.length === 3 && servers.every(server => !server.listening), 'LOOPBACK_CLEANUP_REQUIRED')
  }
  console.log('VIEWINGS_LANDING_LOGIN_BROWSER', JSON.stringify({ status: 'PASS', actual_export: true,
    synthetic_auth_endpoint: true, real_candidate_backend_in_this_container: false,
    rejected_password_without_cookie: true, http_only_cookie: true, shortlist_regression: true,
    viewing_cta_en_es: true, viewing_listing_redirect_en_es: true, existing_session_redirect: true,
    loopback_only: true, fixtures_closed: true }))
  console.log('EDITORIAL_RUNTIME_BROWSER', JSON.stringify(editorialResult))
  console.log('SPATIAL_SEARCH_BROWSER', JSON.stringify(spatialResult))
  console.log('DEVELOPMENT_INVENTORY_BROWSER', JSON.stringify(developmentResult))
  console.log('SELLER_VALUATION_INTAKE_BROWSER', JSON.stringify(sellerValuationResult))
}

const safeBrowserFailures = new Set([
  'ACTUAL_SEARCH_EXPORT_REQUIRED', 'SEARCH_FILTERS_MUST_SURVIVE_SHAPE_CHANGES', 'SAME_PUBLIC_EXCLUDED_COUNT_REQUIRED',
  'FULLSCREEN_MAP_SURFACE_REQUIRED', 'BROAD_RESULTS_IN_ACTIVE_SHAPE', 'SPA_MUST_NOT_BROADEN_MISSING_SCOPE',
  'LIST_RESPONSE_MISSING', 'MAP_RESPONSE_MISSING', 'MAP_SHIM_METHOD_MISSING',
  'MAP_INITIALIZATION_FAILED', 'BROWSER_PAGE_EXCEPTION', 'LOOPBACK_CLEANUP_REQUIRED',
  'DEVELOPMENT_GET_RESPONSE_MISSING', 'DEVELOPMENT_HTTP_2XX', 'DEVELOPMENT_HTTP_3XX',
  'DEVELOPMENT_HTTP_4XX', 'DEVELOPMENT_HTTP_5XX', 'DEVELOPMENT_HTTP_OTHER',
  'DEVELOPMENT_NO_STORE_RESPONSE_REQUIRED', 'DEVELOPMENT_PRIVATE_RESPONSE_LEAK', 'DEVELOPMENT_WITHDRAWN_CONTENT_VISIBLE',
  'ACTUAL_DEVELOPMENT_LISTING_EXPORT_REQUIRED', 'DEVELOPMENT_FLOOR_PLAN_SAFETY_REQUIRED',
  'DATED_DEVELOPMENT_PAYMENT_REQUIRED', 'DATED_DEVELOPMENT_UPDATE_REQUIRED', 'DEVELOPMENT_MOBILE_CARDS_REQUIRED',
  'DEVELOPMENT_PHASE_FILTER_REQUIRED', 'DEVELOPMENT_STALE_STOCK_VISIBLE',
  'DEVELOPMENT_STATIC_STOCK_OR_DRAFT_REQUIRED_ABSENT', 'DEVELOPMENT_PUBLIC_API_LIFECYCLE_REQUIRED',
  'SELLER_POST_RESPONSE_MISSING', 'SELLER_POST_CONTRACT_REJECTED', 'ACTUAL_SELLING_EXPORT_REQUIRED',
  'SELLER_PRIVATE_FORM_INCLUDED_COMMENTS', 'ORDINARY_SELLER_INCLUDES_VALUATION_FIELDS', 'SELLER_MOBILE_FORM_REQUIRED',
  'SELLER_CONSENT_REQUIRED', 'SELLER_SENT_WITHOUT_CONSENT', 'SELLER_FORM_NOT_PRESERVED_ON_RETRY',
  'SELLER_UNCONFIRMED_RECEIPT_NOT_ANNOUNCED', 'SELLER_ANONYMOUS_RECEIPT_REQUIRED',
  'SELLER_SYNTHETIC_PRIVATE_DATA_IN_STATIC_HTML', 'SELLER_SYNTHETIC_RECEIPT_COUNT_MISMATCH',
])
function browserFailureCode(error) {
  if (safeBrowserFailures.has(error?.message)) return error.message
  if (error?.name === 'TimeoutError') return 'PLAYWRIGHT_TIMEOUT'
  if (error?.name === 'TypeError') return 'JS_TYPE_ERROR'
  if (error?.name === 'SyntaxError') return 'JS_SYNTAX_ERROR'
  return 'UNCLASSIFIED_BROWSER_FAILURE'
}
function reportBrowserFailure(error, phase) {
  console.error(`VIEWINGS_LANDING_LOGIN_FAILED stage=${stage} code=${browserFailureCode(error)} phase=${phase}`)
  process.exitCode = 1
}
process.on('unhandledRejection', error => reportBrowserFailure(error, 'unhandled'))
main().catch(error => {
  reportBrowserFailure(error, 'caught')
})
