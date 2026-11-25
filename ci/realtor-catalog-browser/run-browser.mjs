import { lstat, readFile } from 'node:fs/promises'
import { writeSync } from 'node:fs'
import { chromium } from 'playwright-core'

const API = 'http://127.0.0.1:8918', DASH = 'http://127.0.0.1:4175', LANDING = 'http://127.0.0.1:8917'
const PORTAL = 'https://management.kaobahotel.com/'
const contexts = [], journeys = [], geometryChecks = [], presencePages = new WeakSet(), catalogRequests = new WeakMap()
let browser, accounts, facts, stage = 'fixture', timer, processFailed = false
let pageErrors = 0, externalWrites = 0, unexpectedMutations = 0, trackingRequests = 0, blockedExternal = 0
let presenceCount = 0, apiBoundaryChecks = 0, portalLinkChecks = 0, portalAbsenceChecks = 0
let canonicalDetailChecks = 0, dirtyGuardChecks = 0, transportAbortChecks = 0
class Failure extends Error {}
const check = (value, reason) => { if (!value) throw new Failure(reason) }
const button = (scope, name) => scope.getByRole('button', { name, exact: true })
const translated = (value, values) => value.replace(/\{\{(\w+)\}\}/g, (_, key) => String(values[key]))
const ids = values => values.map(row => row.id).sort().join(',')
function advance(value) {
  check(/^[A-Za-z][A-Za-z0-9_]{0,95}$/.test(value), 'FINITE_STAGE_REQUIRED')
  stage = value; clearTimeout(timer)
  timer = setTimeout(() => { console.log('CATALOG_BROWSER_FAILURE ' + stage + ' STAGE_DEADLINE'); process.exit(1) }, 60000)
}
function processFailure(cause) {
  if (processFailed) return
  processFailed = true
  const types = ['Error', 'TypeError', 'ReferenceError', 'SyntaxError', 'RangeError', 'TimeoutError', 'TargetClosedError']
  let type = 'other_error'
  try { if (types.includes(cause?.name)) type = cause.name } catch { /* No raw error data. */ }
  try { writeSync(1, 'CATALOG_BROWSER_FAILURE ' + stage + ' PROCESS_' + type + '\n') } catch { /* Nonzero native exit remains authoritative. */ }
  process.exit(1)
}
process.on('uncaughtException', processFailure)
process.on('unhandledRejection', processFailure)

async function read(context, path, expected = 200) {
  check(path.startsWith('/') && !path.startsWith('//') && !path.includes('#'), 'OWNED_API_PATH_REQUIRED')
  const reply = await context.request.get(API + path, { timeout: 15000, maxRedirects: 0 })
  try { check(reply.status() === expected, 'ACTUAL_API_STATUS_REQUIRED'); return await reply.json() }
  finally { await reply.dispose() }
}
async function contextFor(locale, actor) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, serviceWorkers: 'block' })
  contexts.push(context); catalogRequests.set(context, 0)
  context.setDefaultTimeout(15000); context.setDefaultNavigationTimeout(20000)
  context.on('page', page => page.on('pageerror', () => { pageErrors++ }))
  context.on('request', request => {
    const url = new URL(request.url())
    if (url.origin === API && !['GET', 'HEAD', 'OPTIONS'].includes(request.method())
      && !(request.method() === 'POST' && url.pathname === '/auth/login')) unexpectedMutations++
    if (url.origin === API && url.pathname === '/listings' && url.searchParams.get('purpose') === 'sale')
      catalogRequests.set(context, catalogRequests.get(context) + 1)
    if (/google-analytics|googletagmanager|facebook\.com\/tr|doubleclick/.test(url.href)) trackingRequests++
  })
  await context.route('**/*', route => {
    const request = route.request(), url = new URL(request.url())
    if (![API, DASH, LANDING].includes(url.origin)) {
      blockedExternal++
      if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) externalWrites++
      return route.abort('blockedbyclient')
    }
    return route.continue()
  })
  await context.addInitScript(({ locale, owned }) => {
    if (!owned.includes(location.origin)) return
    localStorage.setItem('ildr_lang', locale)
    localStorage.setItem('ildr_measurement_consent_v1', JSON.stringify({ choice: 'denied', savedAt: Date.now() }))
    const before = navigator.onLine
    if (!before) Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => true })
    Object.defineProperty(window, '__catalogOnline', { value: { before, modeled: !before } })
  }, { locale, owned: [DASH, LANDING] })
  const reply = await context.request.post(API + '/auth/login', { data: accounts[actor], headers: { Origin: DASH }, timeout: 15000, maxRedirects: 0 })
  try { check(reply.status() === 200 && (await context.cookies(API)).some(cookie => cookie.name === 'ildr_token' && cookie.httpOnly), 'REAL_COOKIE_LOGIN_REQUIRED') }
  finally { await reply.dispose() }
  const identity = await read(context, '/auth/me')
  check(identity.role === facts.roles[actor] && identity.id === facts.ids[actor], 'ACTUAL_ACCOUNT_ROLE_AND_ID_REQUIRED')
  return context
}
async function foreground(page) {
  await page.bringToFront()
  await page.waitForFunction(() => document.visibilityState === 'visible' && navigator.onLine)
  if (!presencePages.has(page)) {
    const value = await page.evaluate(() => ({ ...window.__catalogOnline, after: navigator.onLine, visible: document.visibilityState === 'visible' }))
    check(Object.keys(value).length === 4 && Object.values(value).every(item => typeof item === 'boolean')
      && value.modeled === !value.before && value.after && value.visible, 'ACTUAL_LOOPBACK_PRESENCE_REQUIRED')
    console.log('CATALOG_LOOPBACK_STATE ' + JSON.stringify(value)); presencePages.add(page); presenceCount++
  }
}
async function navigate(page, path) {
  check(path.startsWith('/') && !path.startsWith('//'), 'FIXED_DASHBOARD_PATH_REQUIRED')
  await page.goto(DASH + path, { waitUntil: 'domcontentloaded' }); await foreground(page)
}
async function geometry(page, label) {
  const scope = page.locator('main')
  for (const width of [320, 390]) {
    advance(label + '_geometry_' + width)
    await page.setViewportSize({ width, height: 844 }); await foreground(page)
    const value = await page.evaluate(() => ({ width: innerWidth, document: document.documentElement.scrollWidth }))
    check(value.width === width && value.document <= width + 1, 'VIEWPORT_OVERFLOW')
    for (const control of await scope.locator('input, textarea, select, button, a, summary').all()) {
      if (!await control.isVisible()) continue
      const box = await control.boundingBox()
      check(box && box.x >= -1 && box.x + box.width <= width + 1, 'CONTROL_OVERFLOW')
    }
    geometryChecks.push({ label, ...value }); console.log('CATALOG_BROWSER_GEOMETRY ' + JSON.stringify({ label, ...value }))
  }
}
async function openSidebar(page) {
  const sidebar = page.locator('aside')
  if ((await sidebar.boundingBox()).x < -1) {
    await page.locator('main > div').first().locator('button').first().click()
    await page.waitForFunction(() => document.querySelector('aside').getBoundingClientRect().x >= -1)
  }
  return sidebar
}
async function closeSidebar(page) {
  await page.locator('aside').locator('button').first().click()
  await page.waitForFunction(() => document.querySelector('aside').getBoundingClientRect().right <= 1)
}
async function portal(scope, copy) {
  const link = scope.getByRole('link', { name: copy.nav.management_portal, exact: true })
  check(await link.count() === 1 && await link.getAttribute('href') === PORTAL, 'FIXED_PORTAL_URL_REQUIRED')
  const url = new URL(await link.getAttribute('href'))
  check(url.search === '' && url.hash === '' && url.username === '' && url.password === '', 'NO_IDENTITY_HANDOFF_REQUIRED')
  check((await link.getAttribute('rel')).split(' ').includes('noopener')
    && (await link.locator('..').innerText()).includes(copy.nav.management_navigation_note), 'SEPARATE_HOTEL_SIGNIN_NOTE_REQUIRED')
  portalLinkChecks++; return link
}
async function noPortal(scope, copy) {
  check(await scope.getByRole('link', { name: copy.nav.management_portal, exact: true }).count() === 0, 'OTHER_IDENTITIES_HAVE_NO_PORTAL_SHORTCUT')
  portalAbsenceChecks++
}
function saleResponse(response) {
  const url = new URL(response.url())
  return url.origin === API && url.pathname === '/listings' && response.request().method() === 'GET'
    && url.searchParams.get('purpose') === 'sale' && url.searchParams.get('page_size') === '24'
}
async function assertCatalog(page, copy, data, expected, total = expected.length) {
  check(data.total === total && ids(data.items) === ids(expected) && data.items.length <= 24
    && data.items.every(item => item.status === 'active' && ['sale', 'both'].includes(item.transaction)), 'EXACT_REAL_SALE_INVENTORY_REQUIRED')
  const main = page.locator('main'), cards = main.locator('article')
  await main.getByText(translated(copy.sale.results, { count: total }), { exact: true }).waitFor()
  await page.waitForFunction(count => document.querySelectorAll('main article').length === count, expected.length)
  await page.waitForFunction(titles => [...document.querySelectorAll('main article h2')].map(item => item.textContent).join('|') === titles,
    data.items.map(item => item.title).join('|'))
  check((await cards.locator('h2').allTextContents()).join('|') === data.items.map(item => item.title).join('|'), 'ACTUAL_CARDS_MATCH_SERVER_ORDER_REQUIRED')
  check(!(await main.innerText()).includes(facts.privateMarker) && !(await main.innerText()).includes('never-render@example.invalid'), 'PRIVATE_LISTING_FIELDS_NOT_RENDERED')
  check(await cards.locator('button, form, input').count() === 0, 'PUBLIC_CARDS_HAVE_NO_MANAGEMENT_CONTROLS')
}
async function changeCatalog(page, copy, action, expected, total = expected.length, params = {}) {
  const [response] = await Promise.all([page.waitForResponse(saleResponse), action()])
  check(response.status() === 200 && /(?:^|,)\s*no-store(?:\s*,|$)/i.test(response.headers()['cache-control'] || ''), 'REAL_UNCACHED_CATALOG_RESPONSE_REQUIRED')
  const url = new URL(response.url())
  check(url.searchParams.get('price_currency') === 'USD' && url.searchParams.get('include_aggregates') === 'false', 'EXPLICIT_PUBLIC_PRICE_CONTRACT_REQUIRED')
  for (const [key, value] of Object.entries(params)) check(url.searchParams.get(key) === value, 'ACTUAL_FILTER_QUERY_REQUIRED')
  const data = await response.json(); await assertCatalog(page, copy, data, expected, total); return data
}
const complete = (locale, name) => journeys.push(locale + '_' + name)

async function boundaries(contextsByActor) {
  const query = '/listings?purpose=sale&page_size=24&price_currency=USD&usd_to_dop=60&include_aggregates=false'
  const newest = [...facts.catalog].reverse()
  for (const [page, expected] of [[1, newest.slice(0, 24)], [2, newest.slice(24)]]) {
    const result = await read(contextsByActor.outsider, query + '&page=' + page)
    check(result.total === 27 && ids(result.items) === ids(expected), 'ACTUAL_PAGINATED_API_REQUIRED'); apiBoundaryChecks++
  }
  for (const actor of ['outsider', 'arlyn']) {
    const result = await read(contextsByActor[actor], '/listings/mine')
    check(Array.isArray(result) && result.map(row => row.id).sort().join(',') === [...facts.mine[actor]].sort().join(','), 'MY_LISTINGS_REMAINS_PERSONALLY_SCOPED')
    apiBoundaryChecks++
  }
  await read(contextsByActor.member, '/listings/mine', 403); apiBoundaryChecks++
  await read(contextsByActor.outsider, '/listings/' + facts.excluded.draft, 404); apiBoundaryChecks++
  await read(contextsByActor.outsider, '/listings/' + facts.excluded.archived, 404); apiBoundaryChecks++
  const fx = await read(contextsByActor.outsider, query + '&region=Cabarete&type=villa&beds=4&min_price=140000&max_price=160000&sort=low')
  check(fx.total === 1 && fx.items[0].id === facts.catalog[2].id && fx.items[0].currency === 'DOP'
    && Number(fx.items[0].price) === 9000000, 'ACTUAL_PRICE_CONVERSION_FILTER_REQUIRED'); apiBoundaryChecks++
}

async function localeJourneys(locale, copy) {
  const byActor = {}, pages = {}
  for (const actor of ['outsider', 'arlyn', 'admin', 'member', 'owner']) {
    advance(locale + '_login_' + actor)
    byActor[actor] = await contextFor(locale, actor); pages[actor] = await byActor[actor].newPage()
  }
  await boundaries(byActor)
  const page = pages.outsider, main = page.locator('main'), newest = [...facts.catalog].reverse()
  advance(locale + '_home_entry')
  await navigate(page, '/')
  const shortcut = main.getByRole('link', { name: copy.nav['sale-properties'], exact: true })
  await shortcut.waitFor()
  check(await shortcut.getAttribute('href') === '/sale-properties?workspace=realtor', 'HOME_SALE_SHORTCUT_REQUIRED')
  await geometry(page, locale + '_home')
  await changeCatalog(page, copy, () => shortcut.click(), newest.slice(0, 24), 27); complete(locale, 'home_entry')
  await geometry(page, locale + '_catalog')
  advance(locale + '_catalog_inventory')
  await changeCatalog(page, copy, () => button(main, copy.sale.next).click(), newest.slice(24), 27, { page: '2' })
  check(new Set(facts.catalog.map(item => item.actor)).size === 2 && new Set(facts.catalog.map(item => item.ownerActor)).size === 2
    && facts.catalog.some(item => item.transaction === 'both'), 'MULTI_AGENT_MULTI_OWNER_BOTH_INVENTORY_REQUIRED')
  complete(locale, 'catalog_inventory')

  advance(locale + '_sidebar_entry')
  await navigate(page, '/settings')
  const sidebar = await openSidebar(page)
  await noPortal(sidebar, copy)
  await changeCatalog(page, copy, () => button(sidebar, copy.nav['sale-properties']).click(), newest.slice(0, 24), 27)
  complete(locale, 'sidebar_entry')
  const apply = () => button(main, copy.sale.search).click()
  advance(locale + '_catalog_filter_sort_low')
  await main.getByLabel(copy.sale.location, { exact: true }).fill('Cabarete')
  await main.getByLabel(copy.sale.usd_to_dop, { exact: true }).fill('60')
  await main.getByLabel(copy.sale.sort).selectOption('low')
  let result = await changeCatalog(page, copy, apply, facts.catalog.slice(0, 3), 3, { region: 'Cabarete', sort: 'low', usd_to_dop: '60' })
  check(result.items.map(item => item.id).join(',') === facts.catalog.slice(0, 3).map(item => item.id).join(','), 'REAL_LOW_PRICE_ORDER_REQUIRED')
  advance(locale + '_catalog_filter_sort_high')
  await main.getByLabel(copy.sale.sort).selectOption('high')
  result = await changeCatalog(page, copy, apply, facts.catalog.slice(0, 3), 3, { sort: 'high' })
  check(result.items.map(item => item.id).join(',') === facts.catalog.slice(0, 3).reverse().map(item => item.id).join(','), 'REAL_HIGH_PRICE_ORDER_REQUIRED')
  advance(locale + '_catalog_filter_type_beds_price')
  await main.getByLabel(copy.sale.type).selectOption('villa')
  await main.getByLabel(copy.sale.beds).selectOption('4')
  await main.getByLabel(copy.sale.min_price, { exact: true }).fill('140000')
  await main.getByLabel(copy.sale.max_price, { exact: true }).fill('160000')
  await main.getByLabel(copy.sale.sort).selectOption('low')
  await changeCatalog(page, copy, apply, [facts.catalog[2]], 1,
    { type: 'villa', beds: '4', min_price: '140000', max_price: '160000', sort: 'low' })
  complete(locale, 'catalog_filters')
  advance(locale + '_catalog_fx_outside')
  await main.getByLabel(copy.sale.usd_to_dop, { exact: true }).fill('50')
  await changeCatalog(page, copy, apply, [], 0, { usd_to_dop: '50' })
  advance(locale + '_catalog_fx_inside')
  await main.getByLabel(copy.sale.usd_to_dop, { exact: true }).fill('60')
  await changeCatalog(page, copy, apply, [facts.catalog[2]], 1, { usd_to_dop: '60' })
  const card = main.locator('article')
  const originalPrice = await card.locator('p').nth(1).innerText()
  check(originalPrice.replace(/\D/g, '') === '900000000' && /RD\$|DOP/.test(originalPrice), 'ORIGINAL_DOP_PRICE_RETAINED')
  const detail = card.getByRole('link', { name: copy.sale.view, exact: true })
  check(await detail.getAttribute('href') === LANDING + '/listing/' + facts.catalog[2].id + '/'
    && (await detail.getAttribute('rel')).includes('noopener'), 'CONFIGURED_PUBLIC_DETAIL_LINK_REQUIRED')
  canonicalDetailChecks++; complete(locale, 'catalog_fx')

  advance(locale + '_catalog_controlled_transport_error')
  await main.getByLabel(copy.sale.location, { exact: true }).fill('Synthetic no-match area')
  let intercepted = 0
  const rejectOne = route => {
    const request = route.request(), url = new URL(request.url())
    if (request.method() === 'GET' && url.origin === API && url.pathname === '/listings'
      && url.searchParams.get('purpose') === 'sale' && url.searchParams.get('region') === 'Synthetic no-match area' && intercepted === 0) {
      intercepted++; return route.abort('failed')
    }
    return route.fallback()
  }
  await page.route('**/*', rejectOne)
  try { await apply(); await main.getByText(copy.sale.error, { exact: true }).waitFor() }
  finally { await page.unroute('**/*', rejectOne) }
  check(intercepted === 1, 'ONE_OWNED_TRANSPORT_ABORT_REQUIRED'); transportAbortChecks++
  advance(locale + '_catalog_real_retry')
  await changeCatalog(page, copy, () => button(main, copy.sale.retry).click(), [], 0)
  await changeCatalog(page, copy, () => button(main, copy.sale.clear).click(), newest.slice(0, 24), 27)
  complete(locale, 'catalog_error_retry')

  advance(locale + '_account_entry')
  await navigate(page, '/settings'); await main.getByRole('link', { name: copy.nav['sale-properties'], exact: true }).waitFor()
  await noPortal(main, copy); complete(locale, 'other_realtor_no_portal')
  await geometry(page, locale + '_realtor_account')
  await changeCatalog(page, copy, () => main.getByRole('link', { name: copy.nav['sale-properties'], exact: true }).click(), newest.slice(0, 24), 27)
  complete(locale, 'account_entry')
  advance(locale + '_member_entry')
  await navigate(page, '/property-network?tab=my-profile')
  await main.getByRole('heading', { name: facts.profileNames.outsider, exact: true }).waitFor()
  await geometry(page, locale + '_member_profile')
  await changeCatalog(page, copy, () => main.getByRole('link', { name: copy.nav['sale-properties'], exact: true }).click(), newest.slice(0, 24), 27)
  complete(locale, 'member_entry')

  advance(locale + '_arlyn_account')
  const arlyn = pages.arlyn, arlynMain = arlyn.locator('main')
  await navigate(arlyn, '/settings')
  await arlynMain.getByRole('link', { name: copy.nav.management_portal, exact: true }).waitFor()
  await portal(arlynMain, copy); await geometry(arlyn, locale + '_arlyn_account')
  await portal(await openSidebar(arlyn), copy); await closeSidebar(arlyn)
  await changeCatalog(arlyn, copy, () => arlynMain.getByRole('link', { name: copy.nav['sale-properties'], exact: true }).click(), newest.slice(0, 24), 27)
  advance(locale + '_arlyn_member_dirty_guard')
  await navigate(arlyn, '/property-network?tab=my-profile')
  await arlynMain.getByRole('heading', { name: facts.profileNames.arlyn, exact: true }).waitFor()
  const bio = arlynMain.getByLabel(copy.network.bio)
  await bio.fill(facts.draftBio)
  const portalLink = await portal(await openSidebar(arlyn), copy)
  const beforeURL = arlyn.url(), beforePages = byActor.arlyn.pages().length
  const dialog = arlyn.waitForEvent('dialog'), click = portalLink.click()
  const prompt = await dialog
  check(prompt.type() === 'confirm' && prompt.message() === copy.network.leave_confirm, 'ACTUAL_UNSAVED_DRAFT_CONFIRM_REQUIRED')
  await prompt.dismiss(); await click
  check(arlyn.url() === beforeURL && await bio.inputValue() === facts.draftBio
    && byActor.arlyn.pages().length === beforePages, 'CANCELLED_PORTAL_NAVIGATION_PRESERVES_DRAFT')
  dirtyGuardChecks++
  await bio.fill(facts.bio); await closeSidebar(arlyn)
  complete(locale, 'arlyn_navigation_guard')

  advance(locale + '_admin_navigation')
  const admin = pages.admin
  await navigate(admin, '/?workspace=admin')
  await admin.getByRole('heading', { name: copy.admin.pages.home.title, exact: true }).waitFor()
  await geometry(admin, locale + '_admin')
  const adminSidebar = await openSidebar(admin)
  await portal(adminSidebar, copy)
  await admin.locator('#dashboard-workspace').selectOption('Realtor')
  await admin.locator('main').getByRole('link', { name: copy.nav['sale-properties'], exact: true }).waitFor()
  await noPortal(admin, copy)
  await changeCatalog(admin, copy, () => admin.locator('main').getByRole('link', { name: copy.nav['sale-properties'], exact: true }).click(), newest.slice(0, 24), 27)
  complete(locale, 'admin_navigation_and_catalog')

  advance(locale + '_buyer_owner_route_guards')
  for (const [actor, name] of [['member', 'buyer_denied'], ['owner', 'owner_denied']]) {
    const denied = pages[actor], countBefore = catalogRequests.get(byActor[actor])
    await navigate(denied, '/sale-properties?workspace=realtor')
    await denied.locator('main').getByText(copy.nav.section_unavailable, { exact: true }).waitFor()
    check(catalogRequests.get(byActor[actor]) === countBefore
      && await denied.locator('main article').count() === 0, 'FORGED_WORKSPACE_DOES_NOT_MOUNT_CATALOG')
    await noPortal(denied, copy); await geometry(denied, locale + '_' + name)
  }
  complete(locale, 'buyer_owner_route_guards')
}

try {
  check(process.platform === 'linux' && process.env.GITHUB_ACTIONS === 'true'
    && process.env.GITHUB_REPOSITORY === 'ilovedrrealty/realty-api', 'EXACT_HOSTED_BROWSER_REQUIRED')
  const path = process.env.ILDR_BROWSER_FIXTURES
  check(path === '/browser-fixtures/fixtures.json', 'EXACT_PRIVATE_FACTS_PATH_REQUIRED')
  const info = await lstat(path)
  check(info.isFile() && !info.isSymbolicLink() && (info.mode & 0o777) === 0o600 && info.size > 0 && info.size < 32768, 'OWNED_PRIVATE_FACTS_REQUIRED')
  const fixture = JSON.parse(await readFile(path, 'utf8'))
  check(fixture.scope === 'realtor-catalog-read-only' && fixture.accounts && fixture.facts, 'EXACT_CATALOG_FACTS_REQUIRED')
  ;({ accounts, facts } = fixture)
  check(facts.catalog.length === 27 && new Set(facts.catalog.map(item => item.id)).size === 27
    && facts.arlynId === '30e0a4b8-aab7-4af8-9b0b-ccbde1dd4e64' && facts.roles.arlyn === 'realtor', 'EXACT_SYNTHETIC_CATALOG_REQUIRED')
  browser = await chromium.launch({ headless: true, args: ['--disable-dev-shm-usage', '--no-sandbox'] })
  for (const locale of ['en', 'es']) {
    const copy = JSON.parse(await readFile(new URL('./' + locale + '.json', import.meta.url), 'utf8'))
    await localeJourneys(locale, copy)
  }
  advance('final')
  check(journeys.length === 24 && new Set(journeys).size === 24 && geometryChecks.length === 32
    && new Set(geometryChecks.map(row => row.label + '_' + row.width)).size === 32
    && contexts.length === 10 && presenceCount === 10 && apiBoundaryChecks === 16 && portalLinkChecks === 8
    && portalAbsenceChecks === 10 && canonicalDetailChecks === 2 && dirtyGuardChecks === 2 && transportAbortChecks === 2,
    'ALL_FINITE_JOURNEYS_REQUIRED')
  check(pageErrors === 0 && externalWrites === 0 && unexpectedMutations === 0 && trackingRequests === 0, 'NO_UNEXPECTED_ACTIVITY_REQUIRED')
  console.log('CATALOG_BROWSER_RESULT ' + JSON.stringify({ status: 'PASS', locales: ['en', 'es'], journeys: 24,
    geometry: 32, api_boundary_checks: 16, portal_link_checks: 8, portal_absence_checks: 10,
    canonical_detail_checks: 2, dirty_guard_checks: 2, transport_abort_checks: 2, page_errors: pageErrors,
    unexpected_mutations: unexpectedMutations, external_writes: externalWrites,
    tracking_requests: trackingRequests, blocked_external_requests: blockedExternal }))
} catch (error) {
  const reason = error instanceof Failure ? error.message : 'UNEXPECTED_' + (['Error', 'TypeError', 'TimeoutError'].includes(error?.name) ? error.name : 'Error')
  console.log('CATALOG_BROWSER_FAILURE ' + stage + ' ' + reason); process.exitCode = 1
} finally {
  clearTimeout(timer)
  for (const context of contexts.reverse()) await context.close().catch(() => {})
  if (browser) await browser.close().catch(() => {})
}
