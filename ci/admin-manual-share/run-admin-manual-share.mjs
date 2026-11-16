import { lstat, readFile } from 'node:fs/promises'
import { chromium } from 'playwright-core'
import { verifyShareContact } from './verify-share-contact.mjs'

const dashboard = 'http://127.0.0.1:4175'
const api = 'http://127.0.0.1:8000'
const landing = 'http://127.0.0.1:8917'
const management = 'https://management.kaobahotel.com/'
const roles = ['admin', 'owner', 'realtor', 'buyer']
const timeout = 10_000
const desktop = { width: 1366, height: 900 }
const mobile = { width: 390, height: 844 }
const results = { status: 'FAIL', checks: [], blocked_external_requests: 0 }

class CheckFailed extends Error {}

function check(condition, code) {
  if (!condition) throw new CheckFailed(code)
}

async function readFixtures() {
  const path = process.env.ILDR_BROWSER_FIXTURES
  check(path === '/browser-fixtures/fixtures.json', 'EXACT_SYNTHETIC_FIXTURE_REQUIRED')
  const stat = await lstat(path)
  check(stat.isFile() && (stat.mode & 0o777) === 0o600 && stat.size > 0 && stat.size <= 4096, 'PRIVATE_FIXTURE_REQUIRED')
  const fixture = JSON.parse(await readFile(path, 'utf8'))
  const ids = ['shareLeadId', 'foreignListingId', 'foreignLeadId', 'legacyInquiryId'].map(key => fixture?.[key])
  check(fixture && Object.keys(fixture).length === 5 && Object.keys(fixture.accounts ?? {}).length === roles.length
    && ids.every(id => typeof id === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/.test(id))
    && new Set(ids).size === ids.length, 'EXACT_FIXTURE_FIELDS_REQUIRED')
  for (const role of roles) {
    const account = fixture.accounts[role]
    check(account && Object.keys(account).length === 2 && account.email === 'browser-' + role + '@example.com'
      && /^[a-zA-Z0-9_-]{32}$/.test(account.password), 'EXACT_SYNTHETIC_ACCOUNT_REQUIRED')
  }
  return fixture
}

function endpoint(response, path, method = 'GET') {
  const url = new URL(response.url())
  return url.origin === api && url.pathname === path && response.request().method() === method
}

async function localContext(browser, viewport) {
  const context = await browser.newContext({ viewport, serviceWorkers: 'block' })
  context.setDefaultTimeout(timeout)
  context.setDefaultNavigationTimeout(timeout)
  context.on('request', request => {
    const url = new URL(request.url())
    if (request.isNavigationRequest() && ![dashboard, api, landing].includes(url.origin)) {
      results.blocked_external_navigation = true
    }
  })
  await context.route('**/*', async route => {
    const request = route.request()
    const url = new URL(request.url())
    if (request.isNavigationRequest() && url.pathname === '/login'
      && (url.origin === dashboard || url.origin === landing)) {
      return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8',
        body: '<!doctype html><html lang="en"><title>Local sign-in destination</title><h1>Local sign-in destination</h1></html>' })
    }
    if (url.origin === dashboard || url.origin === api) return route.continue()
    results.blocked_external_requests += 1
    if (request.isNavigationRequest()) results.blocked_external_navigation = true
    return route.abort('blockedbyclient')
  })
  return context
}

async function signIn(context, account) {
  const response = await context.request.post(`${api}/auth/login`, {
    data: account,
    headers: { Origin: dashboard },
    maxRedirects: 0,
    timeout,
  })
  check(response.status() === 200, 'FIXTURE_LOGIN_FAILED')
  const cookie = (await context.cookies(api)).find(cookie => cookie.name === 'ildr_token')
  check(cookie?.httpOnly && cookie.domain === '127.0.0.1' && cookie.path === '/', 'REAL_LOGIN_COOKIE_REQUIRED')
  await response.dispose()
}

async function verifyIdentity(response, account, name) {
  check(response.status() === 200, 'BROWSER_IDENTITY_RESPONSE_FAILED')
  const user = await response.json()
  check(user.email === account.email && user.role === name, 'BROWSER_ROLE_MISMATCH')
}

async function openSignedIn(page, path, account, name) {
  const [identity] = await Promise.all([
    page.waitForResponse(response => endpoint(response, '/auth/me')),
    page.goto(`${dashboard}${path}`, { waitUntil: 'domcontentloaded' }),
  ])
  await verifyIdentity(identity, account, name)
  await page.locator('main').waitFor({ state: 'visible' })
  check(await page.evaluate(() => !document.cookie.includes('ildr_token=')), 'LOGIN_COOKIE_VISIBLE_TO_JS')
}

function managementLink(page, language) {
  return page.locator('aside nav').getByRole('link', { name: language.management, exact: true })
}

async function assertManagementLink(link) {
  await link.waitFor({ state: 'visible' })
  const relations = (await link.getAttribute('rel') ?? '').split(/\s+/)
  check(await link.getAttribute('href') === management && await link.getAttribute('target') === '_blank'
    && relations.includes('noopener') && relations.includes('noreferrer'), 'MANAGEMENT_LINK_ATTRIBUTES')
}

async function openDrawer(page) {
  await page.locator('main button:has(svg.lucide-menu)').click()
  await page.waitForFunction(() => {
    const bounds = document.querySelector('aside')?.getBoundingClientRect()
    return Boolean(bounds && bounds.left >= -1 && bounds.right <= window.innerWidth + 1)
  })
}

async function closeDrawer(page) {
  await page.locator('aside button:has(svg.lucide-x)').click()
  await page.waitForFunction(() => (document.querySelector('aside')?.getBoundingClientRect().right ?? 1) <= 1)
}

async function verifyRealtor(context, page, fixture, markStep) {
  markStep('realtor_identity_and_page')
  await openSignedIn(page, '/leads?workspace=admin', fixture.accounts.realtor, 'realtor')
  markStep('realtor_admin_route_denial')
  const denied = await context.request.get(`${api}/admin/leads`, { maxRedirects: 0, timeout })
  check(denied.status() === 403, 'REAL_REALTOR_ADMIN_LEADS_DENIAL_REQUIRED')
  await denied.dispose()
  markStep('realtor_assigned_route_read')
  const assigned = await context.request.get(`${api}/realtor/leads`, { maxRedirects: 0, timeout })
  check(assigned.status() === 200, 'REALTOR_ASSIGNED_LEADS_READ_FAILED')
  const leads = await assigned.json()
  check(Array.isArray(leads) && leads.some(lead => lead?.id === fixture.shareLeadId && lead.name === 'Browser Share Contact'),
    'ASSIGNED_LEAD_REQUIRED_FOR_REALTOR_NEGATIVE')
  await assigned.dispose()
  markStep('realtor_assigned_lead_row')
  await page.locator('main').getByText('Browser Share Contact', { exact: true }).filter({ visible: true }).click()
  markStep('realtor_assigned_lead_detail')
  await page.getByRole('button', { name: 'Copy email', exact: true }).waitFor({ state: 'visible' })
  markStep('realtor_share_control_absence')
  check(await page.getByRole('button', { name: 'Share contact', exact: true }).count() === 0,
    'REALTOR_LEAD_SHARE_EXPOSED')
  markStep('realtor_management_link_absence')
  check(await page.locator(`aside nav a[href="${management}"]`).count() === 0, 'REALTOR_MANAGEMENT_LINK_EXPOSED')
}

async function verifyOwnerScope(context, page, fixture, lead) {
  await openSignedIn(page, '/leads?workspace=owner', fixture.accounts.owner, 'owner')
  const response = await context.request.get(`${api}/owner/leads`, { maxRedirects: 0, timeout })
  check(response.status() === 200 && response.headers()['cache-control'] === 'no-store', 'OWNER_CURRENT_READ_REQUIRED')
  const pageData = await response.json()
  await response.dispose()
  const own = pageData.items?.[0]
  check(pageData.total === 1 && pageData.items?.length === 1 && own.id === lead.id && own.name === lead.name
    && own.email === lead.email && own.phone === lead.phone && own.property_id !== fixture.foreignListingId,
  'OWNER_CURRENT_SCOPE_MISMATCH')
  const filtered = await context.request.get(`${api}/owner/leads?property_id=${fixture.foreignListingId}`, { maxRedirects: 0, timeout })
  check(filtered.status() === 200 && filtered.headers()['cache-control'] === 'no-store', 'OWNER_FOREIGN_SCOPE_READ_REQUIRED')
  const foreignPage = await filtered.json()
  await filtered.dispose()
  check(foreignPage.total === 0 && foreignPage.items?.length === 0, 'FOREIGN_OWNER_INQUIRY_EXPOSED')
  const visible = page.getByRole('heading', { name: lead.name, exact: true })
  await visible.waitFor({ state: 'visible' })
  check(await page.getByText('Synthetic other owner contact', { exact: true }).count() === 0, 'FOREIGN_OWNER_CONTACT_RENDERED')
  check(await page.locator(`aside nav a[href="${management}"]`).count() === 0, 'OWNER_MANAGEMENT_LINK_EXPOSED')
}

async function verifyLegacyRead(page, fixture, role) {
  const route = role === 'owner' ? '/inquiries/for-owner' : '/inquiries/mine'
  const toggle = page.getByRole('button', { name: 'Previous inquiries · separate history', exact: true })
  const [response] = await Promise.all([page.waitForResponse(item => endpoint(item, route)), toggle.click()])
  check(response.status() === 200, 'LEGACY_INQUIRY_READ_REQUIRED')
  const rows = await response.json()
  check(Array.isArray(rows) && rows.length === 1 && rows[0]?.id === fixture.legacyInquiryId
    && rows[0].id !== fixture.shareLeadId, 'LEGACY_INQUIRY_SCOPE_MISMATCH')
  const note = page.getByText('Synthetic legacy inquiry history note', { exact: true })
  const row = page.getByRole('listitem').filter({ has: note })
  await row.waitFor({ state: 'visible' })
  check(await row.getByRole('button', { name: 'Share contact', exact: true }).count() === 0, 'LEGACY_INQUIRY_SHARE_EXPOSED')
}

async function verifyBuyerScope(context, page, fixture) {
  await openSignedIn(page, '/inquiries?workspace=admin', fixture.accounts.buyer, 'buyer')
  const response = await context.request.get(`${api}/leads/mine`, { maxRedirects: 0, timeout })
  check(response.status() === 200 && response.headers()['cache-control'] === 'no-store', 'BUYER_OWN_INQUIRY_READ_REQUIRED')
  const rows = await response.json()
  await response.dispose()
  check(Array.isArray(rows) && rows.length === 1 && rows[0]?.id === fixture.shareLeadId
    && rows[0]?.id !== fixture.foreignLeadId, 'BUYER_CURRENT_SCOPE_MISMATCH')
  await page.getByText('Synthetic private lead note sentinel', { exact: true }).waitFor({ state: 'visible' })
  const denied = await context.request.get(`${api}/owner/leads`, { maxRedirects: 0, timeout })
  check(denied.status() === 403 && denied.headers()['cache-control'] === 'no-store', 'BUYER_OWNER_ROUTE_NOT_DENIED')
  await denied.dispose()
  await verifyLegacyRead(page, fixture, 'buyer')
  check(await page.getByRole('button', { name: 'Share contact', exact: true }).count() === 0, 'BUYER_SHARE_EXPOSED')
}

async function run() {
  const contexts = []
  let stage = 'fixture_and_launch'
  let browser
  try {
    const fixture = await readFixtures()
    const lead = { id: fixture.shareLeadId, name: 'Browser Share Contact', email: 'browser-share+ci@example.com',
      phone: '(809) 555-0119', message: 'Synthetic private lead note sentinel' }
    browser = await chromium.launch({
      headless: true, timeout: 20_000,
      proxy: { server: 'http://127.0.0.1:9', bypass: '127.0.0.1' },
      args: ['--disable-background-networking', '--disable-component-update', '--disable-sync', '--no-first-run'],
    })
    async function pageFor(role, viewport) {
      const context = await localContext(browser, viewport)
      contexts.push(context)
      await signIn(context, fixture.accounts[role])
      const page = await context.newPage()
      await page.bringToFront()
      return [context, page]
    }

    stage = 'admin_english_desktop'
    const [englishContext, english] = await pageFor('admin', desktop)
    await openSignedIn(english, '/leads?workspace=admin', fixture.accounts.admin, 'admin')
    await assertManagementLink(managementLink(english, { management: 'Kaoba management portal' }))
    results.english_desktop = await verifyShareContact(english, lead)
    await englishContext.close()
    results.checks.push(stage)

    stage = 'admin_spanish_mobile'
    const [mobileContext, spanish] = await pageFor('admin', mobile)
    await openSignedIn(spanish, '/leads?workspace=admin', fixture.accounts.admin, 'admin')
    await openDrawer(spanish)
    await spanish.locator('aside').getByRole('button', { name: /^Language\b/ }).click()
    await assertManagementLink(managementLink(spanish, { management: 'Portal de administración de Kaoba' }))
    await closeDrawer(spanish)
    results.spanish_mobile = await verifyShareContact(spanish, lead, { language: 'es', mobile: true })
    await mobileContext.close()
    results.checks.push(stage)

    stage = 'owner_current_and_foreign_scope'
    const [ownerContext, owner] = await pageFor('owner', desktop)
    await verifyOwnerScope(ownerContext, owner, fixture, lead)
    results.checks.push(stage)
    stage = 'owner_current_contact_sharing'
    results.owner_desktop = await verifyShareContact(owner, lead, { role: 'owner' })
    results.checks.push(stage)
    stage = 'owner_legacy_has_no_share'
    await verifyLegacyRead(owner, fixture, 'owner')
    check(await owner.getByRole('button', { name: 'Share contact', exact: true }).count() === 1,
      'OWNER_CURRENT_AND_LEGACY_SHARE_COUNTS_CHANGED')
    await ownerContext.close()
    results.checks.push(stage)

    stage = 'realtor_login'
    const [realtorContext, realtor] = await pageFor('realtor', desktop)
    await verifyRealtor(realtorContext, realtor, fixture, step => { stage = step })
    stage = 'realtor_context_cleanup'
    await realtorContext.close()
    results.checks.push('realtor_assigned_lead_has_no_share')

    stage = 'buyer_own_inquiry_has_no_share'
    const [buyerContext, buyer] = await pageFor('buyer', desktop)
    await verifyBuyerScope(buyerContext, buyer, fixture)
    await buyerContext.close()
    results.checks.push(stage)

    stage = 'browser_stays_local'
    check(!results.blocked_external_navigation, 'EXTERNAL_BROWSER_NAVIGATION_REFUSED')
    results.checks.push(stage)
    results.status = 'PASS'
  } catch (error) {
    results.failed_step = stage
    const code = String(error?.message ?? '')
    results.error_code = /^[A-Z][A-Z0-9_]{1,90}$/.test(code) ? code
      : error?.name === 'TimeoutError' ? 'BROWSER_REHEARSAL_TIMEOUT' : 'BROWSER_REHEARSAL_STEP_FAILED'
    process.exitCode = 1
  } finally {
    const closed = await Promise.allSettled(contexts.map(context => context.close()))
    try {
      await browser?.close()
      if (closed.some(result => result.status !== 'fulfilled')) throw new Error('CONTEXT_CLEANUP_FAILED')
    } catch {
      results.status = 'FAIL'
      results.failed_step = 'browser_cleanup'
      results.error_code = 'BROWSER_CLEANUP_FAILED'
      process.exitCode = 1
    }
    console.log('NETWORK_LIVE_SHARE_BROWSER_RESULT ' + JSON.stringify(results))
  }
}

await run()
