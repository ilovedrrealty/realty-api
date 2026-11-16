import { lstat, readFile } from 'node:fs/promises'
import { chromium } from 'playwright-core'

const dashboard = 'http://127.0.0.1:4175'
const api = 'http://127.0.0.1:8000'
const fixturePath = '/browser-fixtures/fixtures.json'
const uuid = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/
const roles = ['owner', 'buyer', 'realtor', 'admin']
const stayCases = ['inWindow', 'outsideWindow', 'pending', 'cancelled', 'unclaimed']
const english = 'Walk through the main entrance and follow the blue sign.'
const spanish = 'Use la entrada principal y siga el letrero azul.'
const checks = []
let stage = 'fixture'

class CheckFailure extends Error {}
function check(condition, label) {
  if (!condition) throw new CheckFailure(label)
}

async function readFixture() {
  check(process.platform === 'linux' && process.env.GITHUB_ACTIONS === 'true'
    && process.env.GITHUB_REPOSITORY === 'ilovedrrealty/realty-api', 'ISOLATED_HOSTED_RUNNER_REQUIRED')
  check(process.env.ILDR_BROWSER_FIXTURES === fixturePath, 'EXACT_FIXTURE_PATH_REQUIRED')
  const info = await lstat(fixturePath)
  check(info.isFile() && (info.mode & 0o777) === 0o600 && info.size < 4096, 'PRIVATE_FIXTURE_REQUIRED')
  const data = JSON.parse(await readFile(fixturePath, 'utf8'))
  const ids = [data.listingId, data.foreignListingId, ...stayCases.map(key => data.stays?.[key])]
  check(ids.every(id => typeof id === 'string' && uuid.test(id)) && new Set(ids).size === ids.length,
    'EXACT_FIXTURE_IDS_REQUIRED')
  check(roles.every(role => data.accounts?.[role]?.email === `browser-${role}@example.com`
    && /^[a-zA-Z0-9_-]{32}$/.test(data.accounts[role].password)), 'EXACT_SYNTHETIC_ACCOUNTS_REQUIRED')
  return data
}

function backendResponse(response, path, method) {
  const url = new URL(response.url())
  return url.origin === api && url.pathname === path && response.request().method() === method
}

function privateResponse(response) {
  const headers = response.headers()
  check((headers['cache-control'] ?? '').includes('no-store') && (headers.vary ?? '').toLowerCase().includes('cookie')
    && (headers.vary ?? '').toLowerCase().includes('authorization'), 'PRIVATE_GUIDE_RESPONSE_HEADERS_REQUIRED')
}

async function call(context, path, method = 'GET', body = undefined) {
  return context.request.fetch(api + path, { method, data: body, headers: { Origin: dashboard }, maxRedirects: 0 })
}

async function status(context, path, expected) {
  const response = await call(context, path)
  check(expected.includes(response.status()), 'GUIDE_SCOPE_STATUS_MISMATCH')
  privateResponse(response)
  await response.dispose()
}

async function createSession(browser, account = null, mobile = false) {
  const viewport = mobile ? { width: 390, height: 844 } : { width: 1366, height: 900 }
  const context = await browser.newContext({ viewport, serviceWorkers: 'block' })
  context.setDefaultTimeout(12_000)
  await context.route('**/*', route => {
    const origin = new URL(route.request().url()).origin
    return origin === dashboard || origin === api ? route.continue() : route.abort('blockedbyclient')
  })
  if (mobile) {
    await context.addInitScript(() => localStorage.setItem('ildr_lang', 'es'))
  }
  if (account !== null) {
    const login = await call(context, '/auth/login', 'POST', account)
    check(login.status() === 200, 'SYNTHETIC_SIGN_IN_FAILED')
    await login.dispose()
    const cookies = await context.cookies(api)
    check(cookies.some(cookie => cookie.name === 'ildr_token' && cookie.httpOnly), 'SESSION_COOKIE_REQUIRED')
  }
  return context
}

async function openBookings(context, path, role) {
  const page = await context.newPage()
  const identityPromise = page.waitForResponse(response => backendResponse(response, '/auth/me', 'GET'))
  await page.goto(dashboard + path, { waitUntil: 'domcontentloaded' })
  const identity = await identityPromise
  check(identity.ok() && (await identity.json()).role === role, 'EXACT_BOOKING_ROLE_REQUIRED')
  return page
}

function guideButton(page, booking, label) {
  return page.getByRole('button', { name: label, exact: true }).and(
    page.locator(`[aria-controls="arrival-guide-${booking}"]`),
  )
}

async function main() {
  const fixture = await readFixture()
  const ownerPath = `/bookings/listings/${fixture.listingId}/arrival-guide`
  const foreignPath = `/bookings/listings/${fixture.foreignListingId}/arrival-guide`
  const guestPath = id => `/bookings/${id}/arrival-guide`
  const browser = await chromium.launch({
    headless: true,
    timeout: 20_000,
    proxy: { server: 'http://127.0.0.1:9', bypass: '127.0.0.1' },
    args: ['--disable-background-networking', '--disable-component-update', '--disable-sync', '--no-first-run'],
  })
  try {
    stage = 'owner_edit'
    const owner = await createSession(browser, fixture.accounts.owner)
    const ownerPage = await openBookings(owner, '/owner-bookings?workspace=owner', 'owner')
    await ownerPage.getByText('Synthetic arrival house', { exact: true }).first().click()
    await ownerPage.getByRole('button', { name: 'Manage arrival guide', exact: true }).click()
    await ownerPage.getByRole('group', { name: 'English', exact: true }).getByLabel('Arrival directions', { exact: true }).fill(english)
    await ownerPage.getByRole('group', { name: 'Español', exact: true }).getByLabel('Arrival directions', { exact: true }).fill(spanish)
    const savedPromise = ownerPage.waitForResponse(response => backendResponse(response, ownerPath, 'PUT'))
    await ownerPage.getByRole('button', { name: 'Save guide', exact: true }).click()
    const saved = await savedPromise
    check(saved.status() === 200, 'OWNER_SAVE_REQUIRED')
    privateResponse(saved)
    const written = await saved.json()
    check(written.version === 1 && written.content?.en?.directions === english
      && written.content?.es?.directions === spanish, 'OWNER_BILINGUAL_GUIDE_REQUIRED')
    checks.push('owner_bilingual_edit')

    stage = 'authorization_and_scope'
    const guest = await createSession(browser, fixture.accounts.buyer, true)
    const realtor = await createSession(browser, fixture.accounts.realtor)
    const admin = await createSession(browser, fixture.accounts.admin)
    const anonymous = await createSession(browser)
    await status(anonymous, guestPath(fixture.stays.inWindow), [401])
    await status(realtor, guestPath(fixture.stays.inWindow), [404])
    await status(admin, guestPath(fixture.stays.inWindow), [404])
    await status(owner, foreignPath, [403, 404])
    await status(realtor, ownerPath, [403, 404])
    await status(admin, ownerPath, [403, 404])
    for (const key of ['outsideWindow', 'pending', 'cancelled', 'unclaimed']) {
      await status(guest, guestPath(fixture.stays[key]), [404])
    }
    for (const endpoint of ['/bookings/mine', `/listings/${fixture.listingId}`]) {
      const result = await call(guest, endpoint)
      check(result.ok(), 'BOOKING_OR_LISTING_UNAVAILABLE')
      const text = await result.text()
      check(!text.includes(spanish) && !text.includes(english), 'GUIDE_LEAKED_OUTSIDE_PRIVATE_ENDPOINT')
      await result.dispose()
    }
    checks.push('role_dates_and_response_privacy')

    stage = 'mobile_guest_view'
    const page = await openBookings(guest, '/bookings?workspace=buyer', 'buyer')
    const future = guideButton(page, fixture.stays.outsideWindow, 'Ver guía de llegada')
    await future.click()
    const unavailable = 'No hay una guía de llegada disponible para esta reserva.'
    await page.getByText(unavailable, { exact: true }).waitFor({ state: 'visible' })
    const available = guideButton(page, fixture.stays.inWindow, 'Ver guía de llegada')
    const readPromise = page.waitForResponse(response => backendResponse(response, guestPath(fixture.stays.inWindow), 'GET'))
    await available.click()
    const read = await readPromise
    check(read.status() === 200, 'CONFIRMED_GUEST_GUIDE_REQUIRED')
    privateResponse(read)
    const details = await read.json()
    check(Object.keys(details).sort().join(',') === 'available_until,content'
      && Date.parse(details.available_until) > Date.now(), 'GUEST_RESPONSE_MINIMAL_AND_TIME_BOUND')
    await page.getByText(spanish, { exact: true }).waitFor({ state: 'visible' })
    check(await page.getByText(english, { exact: true }).count() === 0, 'GUEST_LANGUAGE_SCOPE_MISMATCH')
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'MOBILE_HORIZONTAL_OVERFLOW')
    await guideButton(page, fixture.stays.inWindow, 'Cerrar guía de llegada').click()
    check(await page.getByText(spanish, { exact: true }).count() === 0, 'CLOSED_GUIDE_CONTENT_STILL_RENDERED')
    checks.push('spanish_mobile_guest_read_and_close')

    stage = 'bounded_clear_and_conflict'
    const stale = await call(owner, ownerPath, 'PUT', { expected_version: 0, content: null })
    check(stale.status() === 409, 'OWNER_STALE_UPDATE_MUST_CONFLICT')
    privateResponse(stale)
    await stale.dispose()
    await ownerPage.getByRole('button', { name: 'Clear guide', exact: true }).click()
    const clearPromise = ownerPage.waitForResponse(response => backendResponse(response, ownerPath, 'PUT'))
    await ownerPage.getByRole('button', { name: 'Confirm clear', exact: true }).click()
    const cleared = await clearPromise
    check(cleared.status() === 200, 'OWNER_CLEAR_REQUIRED')
    privateResponse(cleared)
    const empty = await cleared.json()
    check(empty.version === 2 && empty.content === null, 'CLEAR_MUST_ADVANCE_VERSION_AND_REMOVE_CONTENT')
    await status(guest, guestPath(fixture.stays.inWindow), [404])
    checks.push('stale_write_denied_and_clear_revokes')
    console.log('STAY_ARRIVAL_BROWSER ' + JSON.stringify({ status: 'PASS', checks }))
  } finally {
    await browser.close()
  }
}

try {
  await main()
} catch (error) {
  const reason = error instanceof CheckFailure ? error.message : 'BROWSER_OPERATION_FAILED'
  console.log('STAY_ARRIVAL_BROWSER ' + JSON.stringify({ status: 'FAIL', stage, reason, checks }))
  process.exitCode = 1
}
