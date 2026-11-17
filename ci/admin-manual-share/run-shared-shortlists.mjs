import { lstat, readFile } from 'node:fs/promises'
import { chromium } from 'playwright-core'

const dashboard = 'http://127.0.0.1:4175'
const landing = 'http://127.0.0.1:8917'
const api = 'http://127.0.0.1:8000'
const fixturePath = '/browser-fixtures/fixtures.json'
const idShape = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/
const roles = ['owner', 'buyer', 'realtor', 'admin']
const title = 'Synthetic buyer and agent shortlist'
const notes = 'Compare the garden and the shaded terrace.'
const preferences = 'Two bedrooms and walkable to town.'
const agentNotes = 'Confirme primero si hay estacionamiento.'
const privateNote = 'PRIVATE_SYNTHETIC_RESEARCH_SENTINEL'
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
  check(stat.isFile() && (stat.mode & 0o777) === 0o600 && stat.size <= 4096, 'PRIVATE_SYNTHETIC_FIXTURE_REQUIRED')
  const data = JSON.parse(await readFile(fixturePath, 'utf8'))
  const ids = [data.activeListing, data.secondListing, data.inactiveListing]
  check(ids.every(id => typeof id === 'string' && idShape.test(id)) && new Set(ids).size === 3,
    'EXACT_SYNTHETIC_LISTINGS_REQUIRED')
  check(roles.every(role => data.accounts?.[role]?.email === `browser-${role}@example.com`
    && /^[a-zA-Z0-9_-]{32}$/.test(data.accounts[role].password))
    && Number.isSafeInteger(data.realtorCode) && data.realtorCode > 0, 'EXACT_SYNTHETIC_ACCOUNTS_REQUIRED')
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
    && vary.includes('authorization'), 'PRIVATE_RESPONSE_REQUIRED')
}

async function call(context, path, method = 'GET', data = undefined) {
  return context.request.fetch(api + path, {
    method, data, headers: { Origin: dashboard }, maxRedirects: 0, timeout: 12_000,
  })
}

async function requireStatus(context, path, codes, method = 'GET', body = undefined) {
  const response = await call(context, path, method, body)
  check(codes.includes(response.status()), 'API_STATUS_MISMATCH')
  noCache(response)
  await response.dispose()
}

async function createSession(browser, account = null, mobile = false) {
  const viewport = mobile ? { width: 390, height: 844 } : { width: 1366, height: 900 }
  const context = await browser.newContext({ viewport, serviceWorkers: 'block' })
  context.setDefaultTimeout(12_000)
  await context.route('**/*', route => {
    const target = new URL(route.request().url())
    if (target.origin === dashboard || target.origin === api) return route.continue()
    if (target.origin === landing && target.pathname === '/login' && route.request().isNavigationRequest()) {
      return route.fulfill({ contentType: 'text/html', body: '<!doctype html><title>Synthetic login</title>' })
    }
    return route.abort('blockedbyclient')
  })
  // Chromium treats a loopback-only container as offline. Exercise its real local API reads.
  await context.addInitScript(useSpanish => {
    if (!navigator.onLine) Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => true })
    if (useSpanish) localStorage.setItem('ildr_lang', 'es')
  }, mobile)
  if (account !== null) {
    const response = await call(context, '/auth/login', 'POST', account)
    check(response.status() === 200 && (await context.cookies(api)).some(cookie => cookie.name === 'ildr_token' && cookie.httpOnly),
      'REAL_SYNTHETIC_SESSION_REQUIRED')
    await response.dispose()
  }
  return context
}

async function visit(context, path, role) {
  const page = await context.newPage()
  const identityPromise = page.waitForResponse(response => fromApi(response, '/auth/me'))
  await page.goto(dashboard + path, { waitUntil: 'domcontentloaded' })
  const identity = await identityPromise
  check(identity.ok() && (await identity.json()).role === role, 'EXACT_TEST_ACCOUNT_REQUIRED')
  await page.bringToFront()
  await page.waitForFunction(() => document.visibilityState === 'visible' && navigator.onLine)
  return page
}

async function main() {
  const fixture = await readFixture()
  const browser = await chromium.launch({
    headless: true,
    timeout: 20_000,
    proxy: { server: 'http://127.0.0.1:9', bypass: '127.0.0.1' },
    args: ['--disable-background-networking', '--disable-component-update', '--disable-sync', '--no-first-run'],
  })
  try {
    step = 'buyer_sign_in'
    const buyer = await createSession(browser, fixture.accounts.buyer)
    await buyer.addInitScript(() => {
      Object.defineProperty(navigator, 'clipboard', { value: {
        writeText: async value => { window.syntheticCopiedShortlist = value },
      } })
    })
    step = 'buyer_shortlist_route'
    const page = await visit(buyer, '/shortlists', 'buyer')
    await page.bringToFront()
    step = 'buyer_open_editor'
    await page.getByRole('button', { name: 'Create shortlist', exact: true }).click()
    step = 'buyer_title_field'
    await page.getByLabel('Shortlist title', { exact: true }).fill(title)
    step = 'buyer_notes_and_preferences'
    await page.getByLabel('Shared notes', { exact: true }).fill(notes)
    await page.getByLabel('Shared preferences', { exact: true }).fill(preferences)
    step = 'buyer_public_picker'
    const catalogPromise = page.waitForResponse(response => fromApi(response, '/listings'))
    await page.getByRole('button', { name: 'Choose properties', exact: true }).click()
    const catalog = await catalogPromise
    check(catalog.status() === 200, 'PUBLIC_CATALOG_REQUIRED')
    const listingPage = await catalog.json()
    check(listingPage?.items?.some(item => item.id === fixture.activeListing) && !listingPage.items.some(item => item.id === fixture.inactiveListing),
      'FIXTURE_PUBLIC_ELIGIBILITY_MISMATCH')
    step = 'buyer_select_listing'
    await page.getByRole('button', { name: 'Add Browser rehearsal villa', exact: true }).click()
    step = 'buyer_save'
    const createPromise = page.waitForResponse(response => fromApi(response, '/shortlists/owned', 'POST'))
    await page.getByRole('button', { name: 'Save shortlist', exact: true }).click()
    const created = await createPromise
    check(created.status() === 201, 'BROWSER_CREATE_REQUIRED')
    noCache(created)
    const owned = await created.json()
    check(typeof owned.id === 'string' && idShape.test(owned.id) && owned.version === 1
      && owned.items?.length === 1 && owned.items[0]?.id === fixture.activeListing
      && owned.shared_notes === notes && owned.shared_preferences === preferences, 'EXACT_SHORTLIST_CONTENT_REQUIRED')
    const ownerPath = `/shortlists/owned/${owned.id}`
    await requireStatus(buyer, ownerPath, [404, 422], 'PUT', {
      expected_version: 1, title, shared_notes: notes, shared_preferences: preferences,
      listing_ids: [fixture.inactiveListing],
    })
    checks.push('browser_create_public_property_and_shared_fields')

    step = 'buyer_created_detail'
    await page.getByRole('heading', { name: title, exact: true }).waitFor()
    step = 'buyer_enter_recipient_code'
    await page.getByLabel('Recipient account code', { exact: true }).fill('#' + String(fixture.realtorCode).padStart(7, '0'))
    step = 'buyer_select_share_expiry'
    await page.getByRole('combobox', { name: /^Access duration/ }).selectOption('7')
    step = 'buyer_share_review'
    await page.getByRole('button', { name: 'Review share', exact: true }).click()
    const sharePromise = page.waitForResponse(response => fromApi(response, ownerPath + '/share', 'POST'))
    await page.getByRole('button', { name: 'Share shortlist', exact: true }).click()
    const shared = await sharePromise
    check(shared.ok(), 'NAMED_SHARE_REQUIRED')
    noCache(shared)
    const grant = (await shared.json()).share
    check(grant?.state === 'active' && idShape.test(grant.id) && Date.parse(grant.expires_at) > Date.now(),
      'TIME_BOUND_ASSIGNED_AGENT_GRANT_REQUIRED')
    const receiverPath = `/shortlists/received/${grant.id}`
    step = 'buyer_copy_share_link'
    await page.getByRole('button', { name: 'Copy private link', exact: true }).click()
    const privateLink = `/shortlists?grant=${grant.id}`
    check(await page.evaluate(() => window.syntheticCopiedShortlist) === dashboard + privateLink,
      'EXACT_SIGNED_IN_SHARE_LINK_REQUIRED')
    step = 'actor_scope_and_login_handoff'
    const anonymous = await createSession(browser)
    await requireStatus(anonymous, receiverPath, [401])
    const unauthorized = await createSession(browser, fixture.accounts.admin)
    await requireStatus(unauthorized, receiverPath, [403, 404])
    const unsigned = await anonymous.newPage()
    const login = unsigned.waitForURL(url => url.origin === landing && url.pathname === '/login'
      && url.searchParams.get('shortlist') === grant.id && Array.from(url.searchParams.keys()).length === 1)
    await unsigned.goto(dashboard + privateLink, { waitUntil: 'domcontentloaded' })
    await login
    checks.push('exact_grant_login_handoff_and_stranger_denial')

    step = 'realtor_mobile_edit'
    const agent = await createSession(browser, fixture.accounts.realtor, true)
    await requireStatus(agent, ownerPath, [403, 404])
    const mobile = await visit(agent, privateLink, 'realtor')
    await mobile.bringToFront()
    await mobile.getByText(notes, { exact: true }).waitFor({ state: 'visible' })
    check(await mobile.getByText(privateNote, { exact: true }).count() === 0, 'PRIVATE_RESEARCH_MUST_NOT_APPEAR')
    step = 'assigned_realtor_open_editor'
    await mobile.getByRole('button', { name: 'Editar lista', exact: true }).click()
    step = 'assigned_realtor_change_notes'
    const notesField = mobile.getByRole('textbox', { name: /^Notas compartidas/ })
    check(await notesField.inputValue() === notes, 'EXISTING_SHARED_NOTES_REQUIRED')
    await notesField.fill(agentNotes)
    step = 'assigned_realtor_save'
    const editPromise = mobile.waitForResponse(response => fromApi(response, receiverPath, 'PUT'))
    await mobile.getByRole('button', { name: 'Guardar lista', exact: true }).click()
    const edited = await editPromise
    check(edited.status() === 200, 'RECIPIENT_EDIT_REQUIRED')
    noCache(edited)
    const response = await edited.json()
    check(response.id === grant.id && response.shared_notes === agentNotes
      && !JSON.stringify(response).includes(privateNote), 'CROSS_PARTY_SHARED_FIELDS_ONLY')
    step = 'owner_reads_collaborator_change'
    const ownerRead = await call(buyer, ownerPath)
    check(ownerRead.ok() && (await ownerRead.json()).shared_notes === agentNotes, 'OWNER_MUST_SEE_RECIPIENT_EDIT')
    noCache(ownerRead)
    await ownerRead.dispose()
    check(await mobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'MOBILE_SHORTLIST_OVERFLOW')
    checks.push('spanish_mobile_assigned_realtor_collaboration')

    step = 'buyer_revoke'
    await page.bringToFront()
    await requireStatus(buyer, ownerPath, [409], 'PUT', { expected_version: owned.version, title, shared_notes: notes,
      shared_preferences: preferences })
    await page.getByRole('button', { name: 'Revoke access', exact: true }).click()
    const revokePath = ownerPath + '/shares/' + grant.id + '/revoke'
    const revokedPromise = page.waitForResponse(response => fromApi(response, revokePath, 'POST'))
    await page.getByRole('button', { name: 'Confirm revoke', exact: true }).click()
    const revoked = await revokedPromise
    check(revoked.ok(), 'REVOKE_REQUIRED')
    noCache(revoked)
    await requireStatus(agent, receiverPath, [404])
    await requireStatus(agent, receiverPath, [404], 'PUT', {
      expected_version: response.version, title, shared_notes: 'Synthetic denied text', shared_preferences: '',
    })
    step = 'recipient_revoked_view'
    await mobile.bringToFront()
    await mobile.reload({ waitUntil: 'domcontentloaded' })
    await mobile.getByText('Esta lista de propiedades no está disponible.', { exact: true }).waitFor({ state: 'visible' })
    check(await mobile.getByText(agentNotes, { exact: true }).count() === 0, 'REVOKED_CONTENT_STILL_VISIBLE')
    checks.push('stale_edit_denied_and_revocation_blocks_link')
    console.log('SHARED_SHORTLIST_BROWSER ' + JSON.stringify({ status: 'PASS', checks }))
  } finally {
    await browser.close()
  }
}

try {
  await main()
} catch (error) {
  const reason = error instanceof CheckFailure ? error.message
    : error?.name === 'TimeoutError' ? 'TIMED_OUT' : 'BROWSER_OPERATION_FAILED'
  console.log('SHARED_SHORTLIST_BROWSER ' + JSON.stringify({ status: 'FAIL', step, reason, checks }))
  process.exitCode = 1
}
