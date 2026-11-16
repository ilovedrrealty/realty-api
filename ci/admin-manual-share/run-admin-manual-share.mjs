import { lstat, readFile } from 'node:fs/promises'
import { chromium } from 'playwright-core'
import { verifyShareContact } from './verify-share-contact.mjs'

const dashboard = 'http://127.0.0.1:4175'
const api = 'http://127.0.0.1:8000'
const landing = 'http://127.0.0.1:8917'
const management = 'https://management.kaobahotel.com/'
const roles = ['admin', 'realtor']
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
  check(fixture && Object.keys(fixture).length === 2 && Object.keys(fixture.accounts ?? {}).length === roles.length
    && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/.test(fixture.shareLeadId), 'EXACT_FIXTURE_FIELDS_REQUIRED')
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

async function verifyRealtor(context, page, fixture) {
  await openSignedIn(page, '/leads?workspace=admin', fixture.accounts.realtor, 'realtor')
  const denied = await context.request.get(`${api}/admin/leads`, { maxRedirects: 0, timeout })
  check(denied.status() === 403, 'REAL_REALTOR_ADMIN_LEADS_DENIAL_REQUIRED')
  await denied.dispose()
  const assigned = await context.request.get(`${api}/realtor/leads`, { maxRedirects: 0, timeout })
  check(assigned.status() === 200 && (await assigned.json()).some(lead => lead.id === fixture.shareLeadId),
    'ASSIGNED_LEAD_REQUIRED_FOR_REALTOR_NEGATIVE')
  await assigned.dispose()
  await page.getByText('Browser Share Contact', { exact: true }).filter({ visible: true }).first().click()
  await page.getByText('Contact', { exact: true }).waitFor({ state: 'visible' })
  check(await page.getByRole('button', { name: 'Share contact', exact: true }).count() === 0,
    'REALTOR_LEAD_SHARE_EXPOSED')
  check(await page.locator(`aside nav a[href="${management}"]`).count() === 0, 'REALTOR_MANAGEMENT_LINK_EXPOSED')
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

    stage = 'realtor_assigned_lead_has_no_share'
    const [realtorContext, realtor] = await pageFor('realtor', desktop)
    await verifyRealtor(realtorContext, realtor, fixture)
    await realtorContext.close()
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
    console.log('ADMIN_MANUAL_SHARE_BROWSER_RESULT ' + JSON.stringify(results))
  }
}

await run()
