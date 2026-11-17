import { lstat, readFile } from 'node:fs/promises'
import { chromium } from 'playwright-core'

const dashboard = 'http://127.0.0.1:4175'
const landing = 'http://127.0.0.1:8917'
const api = 'http://127.0.0.1:8000'
const fixturePath = '/browser-fixtures/fixtures.json'
const idShape = /^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/
const roles = ['owner', 'buyer', 'realtor', 'admin']
const firstTitle = 'Browser rehearsal villa'
const secondTitle = 'Synthetic virtual coast listing'
const firstNote = 'Synthetic front entrance preference'
const secondNote = 'Synthetic afternoon video preference'
const meet = 'https://meet.google.com/abc-defg-hij'
const checks = []
let step = 'fixture'

class CheckFailure extends Error {}
function check(condition, reason) {
  if (!condition) throw new CheckFailure(reason)
}

async function readFixture() {
  check(process.platform === 'linux' && process.env.GITHUB_ACTIONS === 'true'
    && process.env.GITHUB_REPOSITORY === 'ilovedrrealty/realty-api'
    && process.env.ILDR_BROWSER_FIXTURES === fixturePath, 'CONFINED_FIXTURE_REQUIRED')
  const stat = await lstat(fixturePath)
  check(stat.isFile() && (stat.mode & 0o777) === 0o600 && stat.size <= 4096, 'PRIVATE_FIXTURE_REQUIRED')
  const data = JSON.parse(await readFile(fixturePath, 'utf8'))
  const ids = [data.realtorListing, data.adminListing, data.unassignedListing]
  check(ids.every(id => typeof id === 'string' && idShape.test(id)) && new Set(ids).size === ids.length
    && /^\d{4}-\d{2}-\d{2}$/.test(data.day), 'EXACT_VIEWING_FIXTURE_REQUIRED')
  check(roles.every(role => data.accounts?.[role]?.email === `browser-${role}@example.com`
    && /^[a-zA-Z0-9_-]{32}$/.test(data.accounts[role].password)), 'EXACT_SYNTHETIC_ACCOUNTS_REQUIRED')
  return data
}

function fromApi(response, path, method = 'GET') {
  const url = new URL(response.url())
  return url.origin === api && url.pathname === path && response.request().method() === method
}

function noCache(response) {
  const headers = response.headers()
  const vary = (headers.vary ?? '').toLowerCase()
  check((headers['cache-control'] ?? '').includes('no-store') && vary.includes('cookie')
    && vary.includes('authorization'), 'PRIVATE_VIEWING_RESPONSE_REQUIRED')
}

function call(context, path, method = 'GET', data = undefined) {
  return context.request.fetch(api + path, {
    method, data, headers: { Origin: dashboard }, maxRedirects: 0, timeout: 12_000,
  })
}

async function readPrivate(context, path, allowed = [200]) {
  const response = await call(context, path)
  check(allowed.includes(response.status()), 'EXPECTED_VIEWING_SCOPE_REQUIRED')
  noCache(response)
  const data = await response.json()
  await response.dispose()
  return data
}

async function session(browser, account = null, spanish = false) {
  const context = await browser.newContext({ viewport: spanish ? { width: 390, height: 844 } : { width: 1366, height: 900 },
    serviceWorkers: 'block' })
  context.setDefaultTimeout(12_000)
  await context.route('**/*', route => {
    const url = new URL(route.request().url())
    if (url.origin === dashboard || url.origin === api) return route.continue()
    if (url.origin === landing && url.pathname === '/login' && route.request().isNavigationRequest()) {
      return route.fulfill({ contentType: 'text/html', body: '<!doctype html><title>Fixture sign-in</title>' })
    }
    return route.abort('blockedbyclient')
  })
  await context.addInitScript(useSpanish => {
    if (!navigator.onLine) Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => true })
    if (useSpanish) localStorage.setItem('ildr_lang', 'es')
  }, spanish)
  if (account) {
    const login = await call(context, '/auth/login', 'POST', account)
    check(login.status() === 200 && (await context.cookies(api)).some(cookie => cookie.name === 'ildr_token' && cookie.httpOnly),
      'REAL_SYNTHETIC_SESSION_REQUIRED')
    await login.dispose()
  }
  return context
}

async function visit(context, path, role) {
  const page = await context.newPage()
  await page.bringToFront()
  const record = page.waitForResponse(response => fromApi(response, '/auth/me'))
  await page.goto(dashboard + path, { waitUntil: 'domcontentloaded' })
  const identity = await record
  check(identity.ok() && (await identity.json()).role === role, 'EXACT_WORKSPACE_ROLE_REQUIRED')
  await page.waitForFunction(() => document.visibilityState === 'visible' && navigator.onLine)
  return page
}

function actionPath(item) { return `/viewings/${item.id}/action` }

async function main() {
  const fixture = await readFixture()
  const browser = await chromium.launch({ headless: true, timeout: 20_000,
    proxy: { server: 'http://127.0.0.1:9', bypass: '127.0.0.1' },
    args: ['--disable-background-networking', '--disable-component-update', '--disable-sync', '--no-first-run'] })
  try {
    step = 'viewing_login_handoff'
    const anonymous = await session(browser)
    await readPrivate(anonymous, '/viewings/mine', [401])
    const chosenPath = '/viewings?listing=' + fixture.realtorListing
    const unsigned = await anonymous.newPage()
    const login = unsigned.waitForURL(url => url.origin === landing && url.pathname === '/login'
      && url.searchParams.get('viewing') === fixture.realtorListing && Array.from(url.searchParams.keys()).length === 1)
    await unsigned.goto(dashboard + chosenPath, { waitUntil: 'domcontentloaded' })
    await login
    checks.push('selected_listing_preserved_at_sign_in')

    step = 'buyer_itinerary_editor'
    const buyer = await session(browser, fixture.accounts.buyer)
    await readPrivate(buyer, '/viewings/contact/' + fixture.unassignedListing, [404])
    const buyerPage = await visit(buyer, chosenPath, 'buyer')
    const titleInput = buyerPage.getByRole('textbox', { name: /^Itinerary title/ })
    const createButton = buyerPage.getByRole('button', { name: 'Create itinerary', exact: true })
    await titleInput.or(createButton).first().waitFor()
    if (!(await titleInput.isVisible())) await createButton.click()
    await titleInput.fill('Synthetic two-property itinerary')
    const first = buyerPage.getByRole('group', { name: firstTitle, exact: true })
    await first.locator('input[type="datetime-local"]').fill(fixture.day + 'T10:00')
    await first.getByRole('textbox', { name: /^Notes for this property/ }).fill(firstNote)
    step = 'buyer_add_second_stop'
    await buyerPage.getByRole('button', { name: 'Choose properties', exact: true }).click()
    await buyerPage.getByRole('button', { name: 'Add ' + secondTitle, exact: true }).click()
    const second = buyerPage.getByRole('group', { name: secondTitle, exact: true })
    await second.getByRole('combobox', { name: /^Viewing type/ }).selectOption('virtual')
    await second.locator('input[type="datetime-local"]').fill(fixture.day + 'T11:00')
    await second.getByRole('textbox', { name: /^Notes for this property/ }).fill(secondNote)
    step = 'buyer_submit_two_stops'
    const scheduled = buyerPage.waitForResponse(response => fromApi(response, '/viewings', 'POST'))
    await buyerPage.getByRole('button', { name: 'Send viewing requests', exact: true }).click()
    const submitted = await scheduled
    check(submitted.status() === 201, 'BROWSER_ITINERARY_CREATION_REQUIRED')
    noCache(submitted)
    const itinerary = await submitted.json()
    check(idShape.test(itinerary.id) && itinerary.items?.length === 2
      && itinerary.items.every(item => item.status === 'requested' && item.timezone === 'America/Santo_Domingo'),
      'TWO_UNCONFIRMED_VIEWING_STOPS_REQUIRED')
    const realtorItem = itinerary.items.find(item => item.listing.id === fixture.realtorListing)
    const adminItem = itinerary.items.find(item => item.listing.id === fixture.adminListing)
    check(realtorItem?.mode === 'in_person' && realtorItem.note === firstNote
      && adminItem?.mode === 'virtual' && adminItem.note === secondNote, 'EXACT_PROPERTY_CONTEXT_REQUIRED')
    checks.push('buyer_creates_two_property_itinerary_in_dominican_time')

    step = 'assigned_scope'
    const agent = await session(browser, fixture.accounts.realtor, true)
    const admin = await session(browser, fixture.accounts.admin)
    const unrelated = await session(browser, fixture.accounts.owner)
    await readPrivate(unrelated, '/viewings/assigned', [403, 404])
    const agentData = await readPrivate(agent, '/viewings/assigned')
    const adminData = await readPrivate(admin, '/viewings/assigned')
    check(agentData.items?.length === 1 && agentData.items[0].id === realtorItem.id
      && adminData.items?.length === 1 && adminData.items[0].id === adminItem.id
      && !JSON.stringify(agentData).includes(secondNote) && !JSON.stringify(adminData).includes(firstNote),
      'ASSIGNMENT_ISOLATION_REQUIRED')
    const contact = await readPrivate(buyer, '/viewings/contact/' + fixture.realtorListing)
    check(contact.agent_name === realtorItem.agent_name
      && contact.scheduling_url === 'https://calendly.com/isolated-fixture/property-viewing',
      'EXACT_VETTED_ASSIGNED_AGENT_SCHEDULING_LINK_REQUIRED')
    checks.push('realtor_and_explicit_admin_only_receive_their_own_stop')

    step = 'realtor_spanish_proposal'
    const agentPage = await visit(agent, '/viewings', 'realtor')
    await agentPage.getByRole('button', { name: 'Proponer otra hora', exact: true }).click()
    await agentPage.locator('input[type="datetime-local"]').fill(fixture.day + 'T14:30')
    await agentPage.getByRole('textbox', { name: /^Lugar de encuentro/ }).fill('Entrada principal de prueba')
    await agentPage.getByRole('checkbox', { name: /^Confirmar disponibilidad/ }).check()
    const proposal = agentPage.waitForResponse(response => fromApi(response, actionPath(realtorItem), 'POST'))
    await agentPage.getByRole('button', { name: 'Guardar cambios de la visita', exact: true }).click()
    const proposed = await proposal
    check(proposed.status() === 200, 'REAL_REALTOR_PROPOSAL_REQUIRED')
    noCache(proposed)
    const alternate = await proposed.json()
    check(alternate.status === 'proposed' && Date.parse(alternate.starts_at) === Date.parse(fixture.day + 'T14:30:00-04:00'),
      'PROPOSAL_MUST_STAY_UNCONFIRMED')
    check(await agentPage.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'SPANISH_MOBILE_OVERFLOW')

    step = 'buyer_accept_proposed_time'
    await buyerPage.bringToFront()
    await buyerPage.reload({ waitUntil: 'domcontentloaded' })
    const confirmBuyer = buyerPage.waitForResponse(response => fromApi(response, actionPath(realtorItem), 'POST'))
    await buyerPage.getByRole('button', { name: 'Accept proposed time', exact: true }).click()
    await buyerPage.getByRole('button', { name: 'Save viewing update', exact: true }).click()
    const confirmed = await confirmBuyer
    check(confirmed.status() === 200 && (await confirmed.json()).status === 'confirmed', 'BUYER_ACCEPTANCE_REQUIRED')
    noCache(confirmed)
    checks.push('spanish_mobile_agent_proposes_and_buyer_accepts')

    step = 'assigned_admin_virtual_confirmation'
    const adminPage = await visit(admin, '/viewings', 'admin')
    await adminPage.getByRole('button', { name: 'Confirm viewing', exact: true }).click()
    await adminPage.getByRole('textbox', { name: /^Meeting link/ }).fill(meet)
    await adminPage.getByRole('checkbox', { name: /^Confirm availability/ }).check()
    const confirmAdmin = adminPage.waitForResponse(response => fromApi(response, actionPath(adminItem), 'POST'))
    await adminPage.getByRole('button', { name: 'Save viewing update', exact: true }).click()
    const virtual = await confirmAdmin
    check(virtual.status() === 200, 'EXPLICIT_ADMIN_VIRTUAL_CONFIRMATION_REQUIRED')
    noCache(virtual)
    const virtualData = await virtual.json()
    check(virtualData.status === 'confirmed' && virtualData.meeting_url === meet, 'CONFIRMED_PRIVATE_VIRTUAL_LINK_REQUIRED')
    const final = await readPrivate(buyer, '/viewings/mine')
    const finalPlan = final.items?.find(item => item.id === itinerary.id)
    check(finalPlan?.items.length === 2 && finalPlan.items.every(item => item.status === 'confirmed'),
      'TWO_CONFIRMED_STOPS_IN_BUYER_ITINERARY_REQUIRED')
    checks.push('explicit_admin_confirms_virtual_stop_and_buyer_sees_both')
    console.log('PROPERTY_VIEWINGS_BROWSER ' + JSON.stringify({ status: 'PASS', checks }))
  } finally {
    await browser.close()
  }
}

try {
  await main()
} catch (error) {
  const reason = error instanceof CheckFailure ? error.message
    : error?.name === 'TimeoutError' ? 'TIMED_OUT' : 'BROWSER_OPERATION_FAILED'
  console.log('PROPERTY_VIEWINGS_BROWSER ' + JSON.stringify({ status: 'FAIL', step, reason, checks }))
  process.exitCode = 1
}
