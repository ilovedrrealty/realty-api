import { lstat, readFile } from 'node:fs/promises'
import { chromium } from 'playwright-core'

const dashboard = 'http://127.0.0.1:4175'
const api = 'http://127.0.0.1:8000'
const fixturePath = '/browser-fixtures/fixtures.json'
const queuePath = '/property-network/service-operations'
const requestPath = id => `/property-network/service-requests/${id}`
const timeZone = 'America/Santo_Domingo'
const wait = 15_000
const roles = { owner: 'owner', winner: 'buyer', loser: 'buyer', outsider: 'buyer', admin: 'admin' }
const copy = {
  en: { title: 'Service operations', serviceNav: 'Private service workflow', views: 'Service operations views',
    managed: 'Managed', received: 'Received', back: 'Back to service operations', open: 'Open request',
    filtered: 'Showing requests for one property', filter: 'View requests for this property', clear: 'Clear property filter',
    confirmed: 'Visit confirmed', confirmedDate: 'Confirmed visit', awaiting: 'Awaiting replies',
    desiredDate: 'Requested start date', invitation: 'Invitation to respond',
    emptyManaged: 'No service requests need attention in this view.', emptyReceived: 'No received requests need attention in this view.' },
  es: { title: 'Operaciones de servicios', serviceNav: 'Flujo privado de servicios', views: 'Vistas de operaciones de servicios',
    managed: 'Gestionadas', received: 'Recibidas', back: 'Volver a operaciones de servicios', open: 'Abrir solicitud',
    filtered: 'Solicitudes de una sola propiedad', filter: 'Ver solicitudes de esta propiedad', clear: 'Quitar filtro de propiedad',
    confirmed: 'Visita confirmada', confirmedDate: 'Visita confirmada', awaiting: 'En espera de respuestas',
    desiredDate: 'Fecha de inicio solicitada', invitation: 'Invitación para responder',
    emptyManaged: 'No hay solicitudes de servicio que requieran atención en esta vista.',
    emptyReceived: 'No hay solicitudes recibidas que requieran atención en esta vista.' },
}

const result = { status: 'FAIL', scope: 'isolated_synthetic_private_service_operations', checks: [],
  queue_scopes_verified: 0, native_request_opens: 0, successful_logins: 0,
  unexpected_browser_mutations: 0, blocked_external_requests: 0 }
const contexts = []
const directScopes = new Set()
let stage = 'fixture'
let externalNavigation = false

class RehearsalFailure extends Error {}
function require(condition, code) {
  if (!condition) throw new RehearsalFailure(code)
}
function keys(value, names) {
  return value && typeof value === 'object' && !Array.isArray(value)
    && Object.keys(value).length === names.length && names.every(name => Object.hasOwn(value, name))
}
const uuid = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/
const day = /^\d{4}-\d{2}-\d{2}$/

async function readFixture() {
  require(process.platform === 'linux' && process.env.GITHUB_ACTIONS === 'true'
    && process.env.GITHUB_REPOSITORY === 'ilovedrrealty/realty-api'
    && process.env.ILDR_BROWSER_FIXTURES === fixturePath, 'EXACT_HOSTED_FIXTURE_REQUIRED')
  const file = await lstat(fixturePath)
  require(file.isFile() && (file.mode & 0o777) === 0o600 && file.size > 0 && file.size <= 12_000, 'PRIVATE_SYNTHETIC_FILE_REQUIRED')
  const value = JSON.parse(await readFile(fixturePath, 'utf8'))
  require(keys(value, ['accounts', 'serviceOperations']) && keys(value.accounts, Object.keys(roles)), 'SYNTHETIC_ROLES_REQUIRED')
  for (const [role, account] of Object.entries(value.accounts)) {
    require(keys(account, ['email', 'password']) && account.email === `browser-${role}@example.com`
      && typeof account.password === 'string' && account.password.length >= 24 && account.password.length <= 96,
    'EXACT_SYNTHETIC_LOGIN_REQUIRED')
  }
  const f = value.serviceOperations
  const identities = [f?.awardedRequestId, f?.openRequestId, f?.listingId, f?.privatePropertyId]
  require(keys(f, ['awardedRequestId', 'awardedTitle', 'openRequestId', 'openTitle', 'listingId', 'listingTitle',
    'privatePropertyId', 'privatePropertyTitle', 'confirmedVisitStart', 'desiredStartOn', 'asOf'])
    && identities.every(id => typeof id === 'string' && uuid.test(id)) && new Set(identities).size === identities.length
    && f.awardedTitle === 'Browser rehearsal awarded pool service' && f.listingTitle === 'Browser rehearsal villa'
    && f.openTitle === 'Synthetic invited courtyard service' && f.privatePropertyTitle === 'Synthetic private courtyard property',
  'EXACT_SYNTHETIC_OPERATIONS_IDENTITIES_REQUIRED')
  require(day.test(f.asOf) && day.test(f.desiredStartOn) && typeof f.confirmedVisitStart === 'string'
    && f.confirmedVisitStart === `${f.confirmedVisitStart.slice(0, 10)}T10:00:00-04:00`
    && day.test(f.confirmedVisitStart.slice(0, 10)) && Number.isFinite(Date.parse(f.confirmedVisitStart))
    && f.asOf < f.confirmedVisitStart.slice(0, 10) && f.confirmedVisitStart.slice(0, 10) < f.desiredStartOn,
  'EXACT_DOMINICAN_OPERATIONS_DATES_REQUIRED')
  return value
}

const button = (scope, name) => scope.getByRole('button', { name, exact: true })
const heading = (scope, name, level) => scope.getByRole('heading', { name, level, exact: true })
const panel = state => state.page.getByRole('region', { name: state.text.title, exact: true })
const views = state => panel(state).getByRole('group', { name: state.text.views, exact: true })
const card = (state, title) => panel(state).getByRole('article').filter({ has: heading(state.page, title, 3) })

function endpoint(response, path) {
  const url = new URL(response.url())
  return url.origin === api && url.pathname === path && response.request().method() === 'GET'
}
function privateHeaders(response) {
  const headers = response.headers()
  return headers['cache-control'] === 'no-store' && headers.pragma === 'no-cache' && headers['x-content-type-options'] === 'nosniff'
}

function queueQuery(scope) {
  const params = new URLSearchParams({ view: scope.view, page: '1' })
  if (scope.source) {
    params.set('source_kind', scope.source.kind)
    params.set('source_id', scope.source.id)
  }
  return params
}

function matchesQueue(response, scope) {
  if (!endpoint(response, queuePath)) return false
  const actual = new URL(response.url()).searchParams
  const expected = queueQuery(scope)
  return actual.size === expected.size && [...expected].every(([key, value]) => actual.get(key) === value)
}

async function session(browser, fixture, role, lang = 'en') {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, serviceWorkers: 'block',
    timezoneId: lang === 'es' ? 'Asia/Tokyo' : 'America/Los_Angeles', locale: lang === 'es' ? 'es-DO' : 'en-US' })
  contexts.push(context)
  context.setDefaultTimeout(wait)
  context.setDefaultNavigationTimeout(wait)
  await context.addInitScript(({ dashboard, lang }) => {
    if (location.origin !== dashboard) return
    localStorage.setItem('ildr_lang', lang)
    if (!navigator.onLine) Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => true })
  }, { dashboard, lang })
  await context.route('**/*', async route => {
    const request = route.request()
    if (![dashboard, api].includes(new URL(request.url()).origin)) {
      result.blocked_external_requests += 1
      if (request.isNavigationRequest()) externalNavigation = true
      return route.abort('blockedbyclient')
    }
    if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) {
      result.unexpected_browser_mutations += 1
      return route.abort('blockedbyclient')
    }
    return route.continue()
  })
  const account = fixture.accounts[role]
  if (account) {
    const login = await context.request.post(`${api}/auth/login`, { data: account,
      headers: { Origin: dashboard }, maxRedirects: 0, timeout: wait })
    try {
      const cookie = (await context.cookies(api)).find(cookie => cookie.name === 'ildr_token')
      require(login.status() === 200 && cookie?.httpOnly && cookie.domain === '127.0.0.1', 'REAL_SYNTHETIC_AUTH_REQUIRED')
      result.successful_logins += 1
    } finally { await login.dispose() }
  }
  return { context, page: account ? await context.newPage() : null, role, account, text: copy[lang], lang }
}

async function openServices(state) {
  const { page, role, account, text } = state
  await page.bringToFront()
  const [auth] = await Promise.all([
    page.waitForResponse(response => endpoint(response, '/auth/me')),
    page.goto(`${dashboard}/property-network?tab=services`, { waitUntil: 'domcontentloaded' }),
  ])
  const user = await auth.json()
  require(auth.status() === 200 && user.role === roles[role] && user.email === account.email, 'SIGNED_IN_ROLE_MISMATCH')
  await page.waitForFunction(() => document.visibilityState === 'visible' && navigator.onLine)
  require(await page.evaluate(lang => !document.cookie.includes('ildr_token=') && localStorage.getItem('ildr_lang') === lang, state.lang),
    'ISOLATED_BROWSER_CONTEXT_INVALID')
  const nav = page.getByRole('navigation', { name: text.serviceNav, exact: true })
  await nav.waitFor()
  return nav
}

function expectedCards(f, view, identities) {
  return identities.map(identity => {
    const award = identity === 'award'
    const source = view === 'received' ? null : award
      ? { kind: 'listing', id: f.listingId, title: f.listingTitle }
      : { kind: 'private', id: f.privatePropertyId, title: f.privatePropertyTitle }
    const actions = award ? ['propose_visit', 'cancel_visit']
      : view === 'managed' ? ['compare_quotes', 'cancel_request'] : ['accept_invitation', 'decline_invitation']
    return { request_id: award ? f.awardedRequestId : f.openRequestId, title: award ? f.awardedTitle : f.openTitle,
      stage: award ? 'visit_confirmed' : view === 'managed' ? 'awaiting_replies' : 'invitation',
      desired_start_on: award ? null : f.desiredStartOn, quote_valid_until: null,
      starts_at: award ? f.confirmedVisitStart : null, actions, source }
  })
}

function assertQueue(value, f, view, identities) {
  require(keys(value, ['view', 'as_of', 'time_zone', 'items', 'total', 'page', 'page_size'])
    && value.view === view && value.as_of === f.asOf && value.time_zone === timeZone && value.page === 1 && value.page_size === 30
    && Array.isArray(value.items) && value.total === identities.length && value.items.length === identities.length,
  'EXACT_SERVICE_OPERATIONS_PAGE_REQUIRED')
  for (const expected of expectedCards(f, view, identities)) {
    const row = value.items.find(item => item?.request_id === expected.request_id)
    require(keys(row, Object.keys(expected)) && row.title === expected.title && row.stage === expected.stage
      && row.desired_start_on === expected.desired_start_on && row.quote_valid_until === null
      && (expected.starts_at === null ? row.starts_at === null : Date.parse(row.starts_at) === Date.parse(expected.starts_at))
      && Array.isArray(row.actions) && row.actions.length === expected.actions.length
      && row.actions.every((action, index) => action === expected.actions[index]), 'EXACT_PRIVATE_OPERATIONS_CARD_REQUIRED')
    require(expected.source === null ? row.source === null : keys(row.source, ['kind', 'id', 'title'])
      && Object.entries(expected.source).every(([key, expectedValue]) => row.source[key] === expectedValue), 'WRONG_OPERATIONS_SOURCE_PROJECTION')
  }
}

async function directQueue(state, f, scope, identities, status = 200) {
  const response = await state.context.request.get(`${api}${queuePath}?${queueQuery(scope)}`, { timeout: wait, maxRedirects: 0 })
  try {
    require(response.status() === status && privateHeaders(response), 'PRIVATE_QUEUE_STATUS_OR_HEADERS')
    const value = await response.json()
    if (status === 200) assertQueue(value, f, scope.view, identities)
    else require(keys(value, ['detail']) && typeof value.detail === 'string'
      && ![f.openTitle, f.awardedTitle, f.privatePropertyTitle, f.listingTitle].some(title => value.detail.includes(title)),
    'QUEUE_DENIAL_EXPOSED_DATA')
    const key = `${state.role}:${scope.view}:${scope.source?.kind ?? ''}:${status}`
    require(!directScopes.has(key), 'DUPLICATE_DIRECT_SCOPE')
    directScopes.add(key)
    result.queue_scopes_verified = directScopes.size
  } finally { await response.dispose() }
}

async function uiQueue(state, f, scope, identities, trigger) {
  const [response] = await Promise.all([state.page.waitForResponse(candidate => matchesQueue(candidate, scope)), trigger()])
  require(response.status() === 200 && privateHeaders(response), 'PRIVATE_UI_QUEUE_STATUS_OR_HEADERS')
  assertQueue(await response.json(), f, scope.view, identities)
  await panel(state).waitFor()
  if (identities.length) {
    for (const row of expectedCards(f, scope.view, identities)) await card(state, row.title).waitFor()
  } else {
    await panel(state).getByText(scope.view === 'managed' ? state.text.emptyManaged : state.text.emptyReceived, { exact: true }).waitFor()
  }
  require(await panel(state).getByRole('article').count() === identities.length, 'UNEXPECTED_OPERATIONS_CARDS')
  require(await button(views(state), state.text[scope.view]).getAttribute('aria-pressed') === 'true', 'WRONG_ACTIVE_OPERATIONS_VIEW')
  if (scope.source) await panel(state).getByText(state.text.filtered, { exact: true }).waitFor()
}

async function assertVisit(state, f) {
  const visit = card(state, f.awardedTitle)
  await visit.locator('p').filter({ hasText: new RegExp(`^${state.text.confirmed}$`) }).waitFor()
  await visit.locator('dt').filter({ hasText: new RegExp(`^${state.text.confirmedDate}$`) }).waitFor()
  const time = visit.locator('time')
  const shown = await time.innerText()
  const stamp = await time.getAttribute('datetime')
  const localized = await state.page.evaluate(({ date, lang, timeZone }) => new Intl.DateTimeFormat(lang === 'es' ? 'es-DO' : 'en-US',
    { dateStyle: 'medium', timeStyle: 'short', timeZone }).format(new Date(date)), { date: f.confirmedVisitStart, lang: state.lang, timeZone })
  require(Date.parse(stamp) === Date.parse(f.confirmedVisitStart) && shown === localized, 'CONFIRMED_VISIT_LOST_DOMINICAN_DATE_OR_TIME')
}

async function fitMobile(state) {
  await panel(state).scrollIntoViewIfNeeded()
  require(await panel(state).evaluate(element => {
    const box = element.getBoundingClientRect()
    return innerWidth === 390 && box.left >= -1 && box.right <= innerWidth + 1 && document.documentElement.scrollWidth <= innerWidth + 1
  }), 'MOBILE_HORIZONTAL_OVERFLOW')
}

async function openNativeAndBack(state, f, scope, identity) {
  const expected = expectedCards(f, scope.view, [identity])[0]
  const [response] = await Promise.all([
    state.page.waitForResponse(response => endpoint(response, requestPath(expected.request_id))
      && new URL(response.url()).searchParams.get('view') === scope.view),
    button(card(state, expected.title), state.text.open).click(),
  ])
  require(response.status() === 200 && privateHeaders(response), 'NATIVE_DETAIL_STATUS_OR_HEADERS')
  const detail = await response.json()
  require(detail.id === expected.request_id && detail.view === scope.view && detail.title === expected.title
    && (scope.view === 'received' ? !Object.hasOwn(detail, 'source') : detail.source?.title === expected.source.title),
  'NATIVE_REQUEST_IDENTITY_OR_VIEW_MISMATCH')
  await heading(state.page, expected.title, 2).waitFor()
  require(await panel(state).count() === 0, 'OPERATIONS_QUEUE_MOUNTED_DURING_DETAIL')
  result.native_request_opens += 1
  await uiQueue(state, f, scope, [identity], () => button(state.page, state.text.back).click())
}

async function assertNoSource(state, f) {
  const region = panel(state)
  for (const label of [f.listingTitle, f.privatePropertyTitle, state.text.filter, state.text.clear]) {
    require(await region.getByText(label, { exact: true }).count() === 0, 'RECEIVED_SOURCE_OR_FILTER_EXPOSED')
  }
}

async function step(name, test) {
  stage = name
  await test()
  result.checks.push(name)
}

async function main() {
  const fixture = await readFixture(), f = fixture.serviceOperations
  const managed = { view: 'managed' }, received = { view: 'received' }
  const privateScope = { ...managed, source: { kind: 'private', id: f.privatePropertyId } }
  const listingScope = { ...managed, source: { kind: 'listing', id: f.listingId } }
  const browser = await chromium.launch({ headless: true, timeout: 20_000,
    proxy: { server: 'http://127.0.0.1:9', bypass: '127.0.0.1' },
    args: ['--disable-background-networking', '--disable-component-update', '--disable-sync', '--no-first-run'] })
  try {
    await step('spanish_mobile_managed_filters_and_native_back', async () => {
      const owner = await session(browser, fixture, 'owner', 'es')
      const nav = await openServices(owner)
      await uiQueue(owner, f, managed, ['award', 'open'], () => button(nav, owner.text.title).click())
      await directQueue(owner, f, managed, ['award', 'open'])
      await assertVisit(owner, f)
      const open = card(owner, f.openTitle)
      await open.getByText(owner.text.awaiting, { exact: true }).waitFor()
      await open.getByText(owner.text.desiredDate, { exact: true }).waitFor()
      require(await open.locator('time').getAttribute('datetime') === f.desiredStartOn, 'REQUESTED_DATE_NOT_DISPLAYED_EXACTLY')
      await fitMobile(owner)
      await uiQueue(owner, f, privateScope, ['open'], () => button(open, owner.text.filter).click())
      await directQueue(owner, f, privateScope, ['open'])
      await openNativeAndBack(owner, f, privateScope, 'open')
      await uiQueue(owner, f, managed, ['award', 'open'], () => button(panel(owner), owner.text.clear).click())
      await uiQueue(owner, f, listingScope, ['award'], () => button(card(owner, f.awardedTitle), owner.text.filter).click())
      await directQueue(owner, f, listingScope, ['award'])
      await openNativeAndBack(owner, f, listingScope, 'award')
      await fitMobile(owner)
    })

    await step('english_mobile_winner_received_confirmed_visit', async () => {
      const winner = await session(browser, fixture, 'winner')
      const nav = await openServices(winner)
      await uiQueue(winner, f, managed, [], () => button(nav, winner.text.title).click())
      await uiQueue(winner, f, received, ['award'], () => button(views(winner), winner.text.received).click())
      await directQueue(winner, f, received, ['award'])
      await assertVisit(winner, f)
      await assertNoSource(winner, f)
      await fitMobile(winner)
      await openNativeAndBack(winner, f, received, 'award')
      await assertNoSource(winner, f)
    })

    await step('received_invitation_and_uninvited_empty', async () => {
      for (const [role, identities] of [['loser', ['open']], ['outsider', []]]) {
        const state = await session(browser, fixture, role)
        const nav = await openServices(state)
        await uiQueue(state, f, managed, [], () => button(nav, state.text.title).click())
        await uiQueue(state, f, received, identities, () => button(views(state), state.text.received).click())
        await directQueue(state, f, received, identities)
        await assertNoSource(state, f)
        if (role === 'loser') await card(state, f.openTitle).getByText(state.text.invitation, { exact: true }).waitFor()
      }
    })

    await step('generic_admin_hidden_and_guest_denied', async () => {
      const admin = await session(browser, fixture, 'admin')
      let probes = 0
      admin.page.on('request', request => { if (new URL(request.url()).pathname === queuePath) probes += 1 })
      const nav = await openServices(admin)
      require(await button(nav, admin.text.title).count() === 0 && await panel(admin).count() === 0, 'ADMIN_OPERATIONS_EXPOSED')
      await directQueue(admin, f, managed, [], 404)
      require(probes === 0, 'ADMIN_UI_PROBED_OPERATIONS')
      const guest = await session(browser, fixture, 'guest')
      await directQueue(guest, f, managed, [], 401)
    })

    await step('loopback_only_no_browser_mutations', async () => {
      require(result.queue_scopes_verified === 8 && result.native_request_opens === 3 && result.successful_logins === 5
        && result.unexpected_browser_mutations === 0 && result.blocked_external_requests <= 100 && !externalNavigation,
      'EXACT_READ_ONLY_LOOPBACK_SCOPE_REQUIRED')
    })
    result.status = 'PASS'
  } finally {
    const closed = await Promise.allSettled(contexts.map(context => context.close()))
    await browser.close()
    require(!browser.isConnected() && closed.every(entry => entry.status === 'fulfilled'), 'BROWSER_CLEANUP_FAILED')
  }
}

try {
  await main()
} catch (error) {
  result.status = 'FAIL'
  result.failed_step = stage
  result.error_code = error instanceof RehearsalFailure ? error.message : error?.name === 'TimeoutError' ? 'BROWSER_TIMEOUT' : 'BROWSER_FAILURE'
  const frames = [...String(error?.stack ?? '').matchAll(/run-service-operations-browser\.mjs:(\d+):\d+/g)]
  const source = error instanceof RehearsalFailure && frames.length > 1 ? frames[1] : frames[0]
  result.source_line = source ? Number(source[1]) : null
  process.exitCode = 1
}
console.log('SERVICE_OPERATIONS_BROWSER_RESULT ' + JSON.stringify(result))
