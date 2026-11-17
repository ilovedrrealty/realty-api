import { lstat, readFile, readdir } from 'node:fs/promises'
import { chromium } from 'playwright-core'

const dashboard = 'http://127.0.0.1:4175'
const publicSite = 'http://127.0.0.1:8917'
const api = 'http://127.0.0.1:8000'
const fixtureFile = '/browser-fixtures/fixtures.json'
const idPattern = /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/
const ownTitle = 'Synthetic owned shortlist route'
const sharedTitle = 'Synthetic shared shortlist route'
const firstListing = 'Browser rehearsal villa'
const secondListing = 'Synthetic virtual coast listing'
const withdrawnListing = 'Synthetic withdrawn shortlist property'
const privateNotes = 'SYNTHETIC_STAY_EXPLORE_PRIVATE_NOTES'
const privatePreferences = 'SYNTHETIC_STAY_EXPLORE_PRIVATE_PREFERENCES'
const viewingTitle = 'Synthetic shortlist viewing request'
const viewingWrites = []
const checks = []
let stage = 'fixture'

class CheckFailure extends Error {}
function check(test, reason) {
  if (!test) throw new CheckFailure(reason)
}

async function readFixture() {
  check(process.platform === 'linux' && process.env.GITHUB_ACTIONS === 'true'
    && process.env.GITHUB_REPOSITORY === 'ilovedrrealty/realty-api'
    && process.env.ILDR_BROWSER_FIXTURES === fixtureFile, 'HOSTED_FIXTURE_REQUIRED')
  check((await readdir('/sys/class/net')).join(',') === 'lo', 'LOOPBACK_ONLY_REQUIRED')
  const stat = await lstat(fixtureFile)
  check(stat.isFile() && (stat.mode & 0o777) === 0o600 && stat.size <= 4096, 'PRIVATE_FIXTURE_REQUIRED')
  const value = JSON.parse(await readFile(fixtureFile, 'utf8'))
  const ids = [value.realtorListing, value.adminListing, value.unassignedListing]
  check(ids.every(id => typeof id === 'string' && idPattern.test(id)) && new Set(ids).size === 3
    && Number.isSafeInteger(value.buyerCode) && value.buyerCode > 0
    && /^\d{4}-\d{2}-\d{2}$/.test(value.day), 'EXACT_STAY_EXPLORE_FIXTURE_REQUIRED')
  for (const [key, label] of [['buyer', 'buyer'], ['realtor', 'realtor'], ['otherBuyer', 'other-buyer']]) {
    const account = value.accounts?.[key]
    check(account?.email === 'browser-' + label + '@example.com'
      && /^[a-zA-Z0-9_-]{32}$/.test(account.password), 'SYNTHETIC_ACCOUNTS_REQUIRED')
  }
  return value
}

function fromApi(response, path, method = 'GET') {
  const url = new URL(response.url())
  return url.origin === api && url.pathname === path && response.request().method() === method
}

function privateResponse(response) {
  const headers = response.headers()
  const vary = (headers.vary ?? '').toLowerCase()
  check((headers['cache-control'] ?? '').includes('no-store')
    && vary.includes('cookie') && vary.includes('authorization'), 'PRIVATE_RESPONSE_REQUIRED')
}

function request(context, path, method = 'GET', data) {
  return context.request.fetch(api + path, { method, data, headers: { Origin: dashboard }, timeout: 12_000, maxRedirects: 0 })
}

async function scopedRead(context, path, expected) {
  const response = await request(context, path)
  try {
    check(response.status() === expected, 'SCOPED_READ_STATUS_REQUIRED')
    privateResponse(response)
    return await response.json()
  } finally {
    await response.dispose()
  }
}

async function session(browser, account, language = 'en', mobile = true) {
  const context = await browser.newContext({
    viewport: mobile ? { width: 390, height: 844 } : { width: 1366, height: 900 },
    hasTouch: mobile, isMobile: mobile, serviceWorkers: 'block',
  })
  context.setDefaultTimeout(12_000)
  await context.route('**/*', route => {
    const url = new URL(route.request().url())
    return url.origin === api || url.origin === dashboard ? route.continue() : route.abort('blockedbyclient')
  })
  await context.addInitScript(value => {
    if (!navigator.onLine) Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => true })
    localStorage.setItem('ildr_lang', value)
  }, language)
  context.on('request', observed => {
    const url = new URL(observed.url())
    if (url.origin === api && url.pathname === '/viewings' && observed.method() === 'POST') {
      viewingWrites.push(observed.postDataJSON())
    }
  })
  const response = await request(context, '/auth/login', 'POST', account)
  check(response.status() === 200 && (await context.cookies(api)).some(cookie => cookie.name === 'ildr_token' && cookie.httpOnly),
    'REAL_SYNTHETIC_SESSION_REQUIRED')
  await response.dispose()
  return context
}

async function visit(context, path, role) {
  const page = await context.newPage()
  await page.bringToFront()
  const [identity] = await Promise.all([
    page.waitForResponse(response => fromApi(response, '/auth/me')),
    page.goto(dashboard + path, { waitUntil: 'domcontentloaded' }),
  ])
  check(identity.ok() && (await identity.json()).role === role, 'EXACT_WORKSPACE_ROLE_REQUIRED')
  await page.waitForFunction(() => document.visibilityState === 'visible' && navigator.onLine)
  return page
}

const control = (page, role, name) => page.getByRole(role, { name, exact: true })
const button = (page, name) => control(page, 'button', name)
const heading = (page, name) => control(page, 'heading', name)

async function createShortlist(page, title, listing, id) {
  await button(page, 'Create shortlist').click()
  await page.getByLabel('Shortlist title', { exact: true }).fill(title)
  await page.getByLabel('Shared notes', { exact: true }).fill(privateNotes)
  await page.getByLabel('Shared preferences', { exact: true }).fill(privatePreferences)
  await button(page, 'Choose properties').click()
  await button(page, 'Add ' + listing).click()
  check(await button(page, 'Add ' + withdrawnListing).count() === 0, 'WITHDRAWN_LISTING_NOT_PUBLIC')
  const [created] = await Promise.all([
    page.waitForResponse(response => fromApi(response, '/shortlists/owned', 'POST')),
    button(page, 'Save shortlist').click(),
  ])
  check(created.status() === 201, 'BROWSER_CREATE_REQUIRED')
  privateResponse(created)
  const record = await created.json()
  check(idPattern.test(record.id) && record.version === 1 && record.items?.length === 1
    && record.items[0].id === id && record.shared_notes === privateNotes
    && record.shared_preferences === privatePreferences, 'EXACT_SCOPED_RECORD_REQUIRED')
  await heading(page, title).waitFor()
  return record.id
}

const plannerPath = (scope, id) => '/viewings?workspace=buyer&shortlist=' + id + '&shortlist_scope=' + scope

async function planner(page, scope, id, expected, language = 'en') {
  const spanish = language === 'es'
  const link = control(page, 'link', spanish ? 'Planificar visitas' : 'Plan viewings')
  const path = plannerPath(scope, id)
  check(await link.getAttribute('href') === path, 'LINK_MUST_CARRY_ONLY_SCOPE_AND_IDENTIFIER')
  const [response] = await Promise.all([
    page.waitForResponse(reply => fromApi(reply, '/shortlists/' + scope + '/' + id)),
    link.click(),
  ])
  check(page.url() === dashboard + path && response.ok(), 'PLANNER_MUST_RECHECK_SHORTLIST')
  privateResponse(response)
  await heading(page, spanish ? 'Propiedades de tu lista' : 'Properties from your shortlist').waitFor()
  const add = button(page, (spanish ? 'Agregar ' : 'Add ') + expected)
  await add.waitFor()
  const disallowed = expected === firstListing ? secondListing : firstListing
  check(await button(page, (spanish ? 'Agregar ' : 'Add ') + disallowed).count() === 0,
    'PLANNER_CHOICES_MUST_STAY_IN_SCOPE')
  for (const hidden of [privateNotes, privatePreferences, ownTitle, sharedTitle, withdrawnListing]) {
    check(await page.getByText(hidden, { exact: true }).count() === 0, 'PRIVATE_FIELDS_ENTERED_PLANNER')
  }
  const compare = control(page, 'link', spanish ? 'Comparar zonas' : 'Compare neighborhoods')
  check(await compare.getAttribute('href') === publicSite + '/local-guides/compare/?lang=' + language
    && await compare.getAttribute('target') === '_blank'
    && (await compare.getAttribute('rel')).split(' ').includes('noreferrer'), 'PUBLIC_COMPARISON_LINK_ONLY')
  check(await page.evaluate(() => innerWidth === 390 && document.documentElement.scrollWidth <= innerWidth + 1),
    'MOBILE_PLANNER_OVERFLOW')
  return add
}

async function backTo(page, path) {
  await Promise.all([
    page.waitForURL(dashboard + path),
    page.goBack({ waitUntil: 'domcontentloaded' }),
  ])
}

async function guardedPlanner(page, language = 'en') {
  await page.getByText(language === 'es' ? 'Esta lista no está disponible.' : 'This shortlist is unavailable.',
    { exact: true }).waitFor()
  check(await button(page, (language === 'es' ? 'Enviar solicitudes de visita' : 'Send viewing requests')).isDisabled(),
    'DENIED_SOURCE_CANNOT_SUBMIT_WITHOUT_SELECTION')
  for (const hidden of [privateNotes, privatePreferences, ownTitle, sharedTitle, firstListing, secondListing]) {
    check(await page.getByText(hidden, { exact: true }).count() === 0, 'DENIED_SOURCE_VISIBLE')
  }
}

async function main() {
  const fixture = await readFixture()
  const browser = await chromium.launch({
    headless: true, timeout: 20_000, proxy: { server: 'http://127.0.0.1:9', bypass: '127.0.0.1' },
    args: ['--disable-background-networking', '--disable-component-update', '--disable-sync', '--no-first-run'],
  })
  try {
    stage = 'create_buyer_shortlist'
    const buyer = await session(browser, fixture.accounts.buyer)
    const ownPage = await visit(buyer, '/shortlists', 'buyer')
    const own = await createShortlist(ownPage, ownTitle, firstListing, fixture.realtorListing)
    stage = 'owned_planner'
    await planner(ownPage, 'owned', own, firstListing)
    await backTo(ownPage, '/shortlists')
    await heading(ownPage, ownTitle).waitFor()
    check(viewingWrites.length === 0, 'VIEWING_WRITE_BEFORE_SUBMIT')
    checks.push('buyer_creates_owned_shortlist_and_returns_to_owned_scope')

    stage = 'create_realtor_shortlist'
    const realtor = await session(browser, fixture.accounts.realtor, 'en', false)
    const realtorPage = await visit(realtor, '/shortlists', 'realtor')
    const shared = await createShortlist(realtorPage, sharedTitle, secondListing, fixture.adminListing)
    const ownPath = '/shortlists/owned/' + shared
    stage = 'realtor_grants_assigned_buyer'
    await realtorPage.getByLabel('Recipient account code', { exact: true }).fill('#' + String(fixture.buyerCode).padStart(7, '0'))
    await realtorPage.getByRole('combobox', { name: /^Access duration/ }).selectOption('7')
    await button(realtorPage, 'Review share').click()
    const [share] = await Promise.all([
      realtorPage.waitForResponse(response => fromApi(response, ownPath + '/share', 'POST')),
      button(realtorPage, 'Share shortlist').click(),
    ])
    check(share.ok(), 'REALTOR_WRITE_REQUIRED')
    privateResponse(share)
    const grant = (await share.json()).share
    check(idPattern.test(grant?.id) && grant.recipient_user_code === fixture.buyerCode
      && grant.state === 'active' && Date.parse(grant.expires_at) > Date.now(), 'EXACT_ASSIGNED_BUYER_GRANT_REQUIRED')

    stage = 'received_mobile_planner'
    const received = await session(browser, fixture.accounts.buyer, 'es')
    const receivedPage = await visit(received, '/shortlists', 'buyer')
    await button(receivedPage, 'Compartidas conmigo').click()
    await heading(receivedPage, sharedTitle).waitFor()
    await button(receivedPage, 'Abrir lista').click()
    const receivedPath = '/shortlists?grant=' + grant.id
    await receivedPage.waitForURL(dashboard + receivedPath)
    await heading(receivedPage, sharedTitle).waitFor()
    await planner(receivedPage, 'received', grant.id, secondListing, 'es')
    await backTo(receivedPage, receivedPath)
    await heading(receivedPage, sharedTitle).waitFor()
    await planner(receivedPage, 'received', grant.id, secondListing, 'es')
    checks.push('received_mobile_plan_preserves_grant_scope_on_back')
    checks.push('planner_exposes_only_public_choices_and_language_link')

    stage = 'unrelated_buyer_rejected'
    const outsider = await session(browser, fixture.accounts.otherBuyer)
    await scopedRead(outsider, '/shortlists/owned/' + own, 404)
    await scopedRead(outsider, '/shortlists/received/' + grant.id, 404)
    const outsidePage = await visit(outsider, plannerPath('received', grant.id), 'buyer')
    await guardedPlanner(outsidePage)
    checks.push('unrelated_same_role_cannot_use_owned_or_received_scope')

    stage = 'realtor_revokes'
    await realtorPage.bringToFront()
    await button(realtorPage, 'Revoke access').click()
    const [revoke] = await Promise.all([
      realtorPage.waitForResponse(response => fromApi(response, ownPath + '/shares/' + grant.id + '/revoke', 'POST')),
      button(realtorPage, 'Confirm revoke').click(),
    ])
    check(revoke.ok() && (await revoke.json()).share.state === 'revoked', 'REVOKE_REQUIRED')
    privateResponse(revoke)
    stage = 'revoked_mobile_planner'
    await scopedRead(received, '/shortlists/received/' + grant.id, 404)
    await receivedPage.bringToFront()
    await receivedPage.reload({ waitUntil: 'domcontentloaded' })
    await guardedPlanner(receivedPage, 'es')
    checks.push('owner_revoke_removes_choices_from_received_planner')

    stage = 'explicit_submit'
    await ownPage.bringToFront()
    if (!await control(ownPage, 'link', 'Plan viewings').count()) await button(ownPage, 'Open shortlist').click()
    const add = await planner(ownPage, 'owned', own, firstListing)
    await add.click()
    const stop = control(ownPage, 'group', firstListing)
    await stop.locator('input[type="datetime-local"]').fill(fixture.day + 'T10:00')
    await ownPage.getByLabel('Itinerary title', { exact: true }).fill(viewingTitle)
    check((await scopedRead(buyer, '/viewings/mine', 200)).items.length === 0 && viewingWrites.length === 0,
      'NO_VIEWING_BEFORE_ACTION_REQUIRED')
    const [submitted] = await Promise.all([
      ownPage.waitForResponse(response => fromApi(response, '/viewings', 'POST')),
      button(ownPage, 'Send viewing requests').click(),
    ])
    check(submitted.status() === 201 && viewingWrites.length === 1, 'EXACT_EXPLICIT_VIEWING_WRITE_REQUIRED')
    privateResponse(submitted)
    const payload = viewingWrites[0]
    check(Object.keys(payload).sort().join(',') === 'request_id,stops,title' && payload.title === viewingTitle
      && idPattern.test(payload.request_id) && payload.stops.length === 1
      && Object.keys(payload.stops[0]).sort().join(',') === 'listing_id,mode,note,starts_at'
      && payload.stops[0].listing_id === fixture.realtorListing && payload.stops[0].note === '',
    'SHORTLIST_PRIVATE_CONTEXT_MUST_NOT_LEAVE_PLANNER')
    const row = await submitted.json()
    check(row.items?.length === 1 && row.items[0].listing.id === fixture.realtorListing
      && row.items[0].status === 'requested', 'EXPECTED_UNCONFIRMED_REQUEST_REQUIRED')
    check((await scopedRead(buyer, '/viewings/mine', 200)).items[0].id === row.id, 'EXPECTED_PERSISTED_REQUEST_REQUIRED')
    checks.push('only_explicit_submit_creates_one_unconfirmed_viewing')
    console.log('STAY_EXPLORE_BROWSER', JSON.stringify({ status: 'PASS', checks }))
  } finally {
    await browser.close()
  }
}

try {
  await main()
} catch (error) {
  const reason = error instanceof CheckFailure ? error.message
    : error?.name === 'TimeoutError' ? 'TIMED_OUT' : 'BROWSER_OPERATION_FAILED'
  console.error('STAY_EXPLORE_BROWSER_FAILURE', stage, reason)
  process.exitCode = 1
}
