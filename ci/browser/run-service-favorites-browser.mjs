import { lstat, readFile } from 'node:fs/promises'
import { chromium } from 'playwright-core'

const dashboard = 'http://127.0.0.1:4175'
const api = 'http://127.0.0.1:8000'
const fixturePath = '/browser-fixtures/fixtures.json'
const wait = 15_000
const roles = { owner: 'owner', winner: 'buyer', loser: 'buyer', outsider: 'buyer', admin: 'admin' }
const copy = {
  en: { nav: 'Private service workflow', newRequest: 'New private service request', source: 'Property context',
    choose: 'Choose an authorized property', propertyClass: 'Property class', category: 'Requested service',
    save: 'Save professional', saved: 'Saved', title: 'Saved professionals',
    remove: 'Remove saved professional', refresh: 'Refresh', unavailable: 'Professional unavailable',
    empty: "You haven't saved any professionals yet.", preferences: 'Offer services privately',
    optIn: 'Let members find me for these services', saveChanges: 'Save changes' },
  es: { nav: 'Flujo privado de servicios', newRequest: 'Nueva solicitud privada de servicio',
    source: 'Contexto de la propiedad', choose: 'Elige una propiedad autorizada', propertyClass: 'Clase de propiedad',
    category: 'Servicio solicitado', save: 'Guardar profesional', saved: 'Guardado', title: 'Profesionales guardados',
    remove: 'Quitar profesional guardado', refresh: 'Actualizar', unavailable: 'Profesional no disponible',
    empty: 'Todavía no has guardado profesionales.' },
}
const result = { status: 'FAIL', scope: 'isolated_synthetic_service_favorites', checks: [],
  favorite_saves: 0, favorite_removes: 0, preference_updates: 0,
  unexpected_browser_mutations: 0, blocked_external_requests: 0 }
const contexts = []
let stage = 'fixture'
let externalNavigation = false

class RehearsalFailure extends Error {}
function require(condition, code) {
  if (!condition) throw new RehearsalFailure(code)
}
function hasKeys(value, keys) {
  return value && typeof value === 'object' && !Array.isArray(value)
    && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key))
}
const uuid = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/

async function readFixture() {
  require(process.platform === 'linux' && process.env.GITHUB_ACTIONS === 'true'
    && process.env.GITHUB_REPOSITORY === 'ilovedrrealty/realty-api'
    && process.env.ILDR_BROWSER_FIXTURES === fixturePath, 'EXACT_HOSTED_FIXTURE_REQUIRED')
  const file = await lstat(fixturePath)
  require(file.isFile() && (file.mode & 0o777) === 0o600 && file.size > 0 && file.size <= 12_000, 'PRIVATE_SYNTHETIC_FILE_REQUIRED')
  const value = JSON.parse(await readFile(fixturePath, 'utf8'))
  require(hasKeys(value, ['accounts', 'serviceFavorites']) && hasKeys(value.accounts, Object.keys(roles)), 'SYNTHETIC_ROLES_REQUIRED')
  for (const [role, account] of Object.entries(value.accounts)) {
    require(hasKeys(account, ['email', 'password']) && account.email === `browser-${role}@example.com`
      && typeof account.password === 'string' && account.password.length >= 24 && account.password.length <= 96,
    'EXACT_SYNTHETIC_LOGIN_REQUIRED')
  }
  const data = value.serviceFavorites
  require(hasKeys(data, ['listingId','listingTitle','providerUserCode','providerAlias','otherProviderUserCode','otherProviderAlias','otherFavoriteId'])
    && uuid.test(data.listingId) && uuid.test(data.otherFavoriteId)
    && Number.isSafeInteger(data.providerUserCode) && data.providerUserCode > 0
    && Number.isSafeInteger(data.otherProviderUserCode) && data.otherProviderUserCode > 0
    && data.providerUserCode !== data.otherProviderUserCode && data.listingTitle === 'Browser rehearsal villa'
    && data.providerAlias === 'Synthetic winner service provider'
    && data.otherProviderAlias === 'Synthetic loser service provider', 'EXACT_SYNTHETIC_FAVORITES_FIXTURE_REQUIRED')
  return value
}

const favoritesPath = '/property-network/service-favorites'
const preferencesPath = '/property-network/me/service-preferences'
const discoverPath = '/property-network/service-providers'
let ownedFavoriteId = null

function endpoint(response, path, method = 'GET') {
  const url = new URL(response.url())
  return url.origin === api && url.pathname === path && response.request().method() === method
}
function privateHeaders(response) {
  const headers = response.headers()
  return headers['cache-control'] === 'no-store' && headers.pragma === 'no-cache' && headers['x-content-type-options'] === 'nosniff'
}

async function read(context, path, status = 200) {
  const response = await context.request.get(`${api}${path}`, { timeout: wait, maxRedirects: 0 })
  try {
    require(response.status() === status && privateHeaders(response), 'PRIVATE_READ_STATUS_OR_HEADERS')
    const value = await response.json()
    if (status >= 400) require(hasKeys(value, ['detail']), 'PRIVATE_DENIAL_EXPOSED_DATA')
    return value
  } finally { await response.dispose() }
}

async function session(browser, fixture, role, lang = 'en') {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, locale: lang === 'es' ? 'es-DO' : 'en-US', serviceWorkers: 'block' })
  contexts.push(context)
  context.setDefaultTimeout(wait)
  context.setDefaultNavigationTimeout(wait)
  await context.addInitScript(({ dashboard, lang }) => {
    if (location.origin !== dashboard) return
    localStorage.setItem('ildr_lang', lang)
    if (!navigator.onLine) Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => true })
  }, { dashboard, lang })
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url())
    if (![api, dashboard].includes(url.origin)) {
      result.blocked_external_requests += 1
      if (request.isNavigationRequest()) externalNavigation = true
      return route.abort('blockedbyclient')
    }
    if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) {
      if (url.origin === api && request.method() === 'POST' && role === 'owner' && url.pathname === favoritesPath) result.favorite_saves += 1
      else if (url.origin === api && request.method() === 'DELETE' && role === 'owner' && ownedFavoriteId
        && url.pathname === `${favoritesPath}/${ownedFavoriteId}`) result.favorite_removes += 1
      else if (url.origin === api && request.method() === 'PUT' && role === 'winner' && url.pathname === preferencesPath) result.preference_updates += 1
      else {
        result.unexpected_browser_mutations += 1
        return route.abort('blockedbyclient')
      }
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
    } finally { await login.dispose() }
  }
  const page = await context.newPage(), text = copy[lang]
  return { context, page, text, role }
}

function favoritePanel(state) {
  return state.page.getByRole('region', { name: state.text.title, exact: true })
}

async function foreground(page) {
  await page.bringToFront()
  await page.waitForFunction(() => document.visibilityState === 'visible' && navigator.onLine)
}

async function openServices(state, fixture) {
  const { page, text, role } = state
  await page.bringToFront()
  const [me] = await Promise.all([
    page.waitForResponse(response => endpoint(response, '/auth/me')),
    page.goto(`${dashboard}/property-network?tab=services`, { waitUntil: 'domcontentloaded' }),
  ])
  const user = await me.json()
  require(me.status() === 200 && user.role === roles[role] && user.email === fixture.accounts[role].email, 'SIGNED_IN_ROLE_MISMATCH')
  await foreground(page)
  const nav = page.getByRole('navigation', { name: text.nav, exact: true })
  await nav.waitFor()
  return nav
}


async function fits(locator) {
  await locator.scrollIntoViewIfNeeded()
  require(await locator.evaluate(element => {
    const rect = element.getBoundingClientRect()
    return innerWidth === 390 && rect.left >= -1 && rect.right <= 391 && document.documentElement.scrollWidth <= 391
  }), 'MOBILE_FAVORITES_OVERFLOW')
}

function expectFavorite(row, code, alias, id) {
  require(hasKeys(row, ['id', 'provider', 'saved_at']) && uuid.test(row.id)
    && (id === undefined || row.id === id) && Number.isFinite(Date.parse(row.saved_at)), 'EXACT_FAVORITE_ROW_REQUIRED')
  if (code === null) require(row.provider === null, 'INELIGIBLE_PROVIDER_DETAILS_EXPOSED')
  else require(hasKeys(row.provider, ['user_code', 'member_alias']) && row.provider.user_code === code
    && row.provider.member_alias === alias, 'EXACT_PROVIDER_PROJECTION_REQUIRED')
}

function expectPage(page, count) {
  require(hasKeys(page, ['items', 'total', 'page', 'page_size']) && page.page === 1 && page.page_size === 30
    && Array.isArray(page.items) && page.items.length === count && page.total === count, 'EXACT_PRIVATE_FAVORITES_PAGE_REQUIRED')
  return page.items[0]
}

async function openList(state, nav) {
  const responsePromise = state.page.waitForResponse(response => endpoint(response, favoritesPath))
  await nav.getByRole('button', { name: state.text.title, exact: true }).click()
  const response = await responsePromise
  require(response.status() === 200 && privateHeaders(response), 'PRIVATE_FAVORITES_UI_UNAVAILABLE')
  const panel = favoritePanel(state)
  await panel.waitFor()
  return panel
}

async function forbiddenDelete(state, id) {
  const response = await state.context.request.delete(`${api}${favoritesPath}/${id}`, {
    data: {}, headers: { Origin: dashboard }, maxRedirects: 0, timeout: wait,
  })
  try {
    require(response.status() === 404 && privateHeaders(response) && hasKeys(await response.json(), ['detail']),
      'CROSS_OWNER_REMOVE_NOT_DENIED')
  } finally { await response.dispose() }
}

async function step(name, run) {
  stage = name
  await run()
  result.checks.push(name)
}

async function main() {
  const fixture = await readFixture(), data = fixture.serviceFavorites
  const browser = await chromium.launch({ headless: true, timeout: 20_000,
    proxy: { server: 'http://127.0.0.1:9', bypass: '127.0.0.1' },
    args: ['--disable-background-networking', '--disable-component-update', '--disable-sync', '--no-first-run'] })
  let outsider, reader, panel
  try {
    await step('own_list_only_and_guest_admin_denial', async () => {
      const guest = await session(browser, fixture, 'guest')
      await read(guest.context, favoritesPath, 401)
      await guest.context.close()
      const admin = await session(browser, fixture, 'admin')
      let probes = 0
      admin.page.on('request', request => {
        if (new URL(request.url()).pathname === favoritesPath) probes += 1
      })
      const adminNav = await openServices(admin, fixture)
      require(await adminNav.getByRole('button', { name: admin.text.title, exact: true }).count() === 0
        && probes === 0, 'ADMIN_FAVORITES_PROBED_OR_SHOWN')
      await read(admin.context, favoritesPath, 404)
      await admin.context.close()

      outsider = await session(browser, fixture, 'outsider')
      const outsidePanel = await openList(outsider, await openServices(outsider, fixture))
      await outsidePanel.getByText(data.otherProviderAlias, { exact: true }).waitFor()
      expectFavorite(expectPage(await read(outsider.context, favoritesPath), 1),
        data.otherProviderUserCode, data.otherProviderAlias, data.otherFavoriteId)
      require(await outsidePanel.getByText(data.providerAlias, { exact: true }).count() === 0,
        'OTHER_ACTOR_LIST_SHOWS_UNOWNED_PROVIDER')
      await fits(outsidePanel)

      reader = await session(browser, fixture, 'owner', 'es')
      panel = await openList(reader, await openServices(reader, fixture))
      expectPage(await read(reader.context, favoritesPath), 0)
      await panel.getByText(reader.text.empty, { exact: true }).waitFor()
      require(await panel.getByText(data.otherProviderAlias, { exact: true }).count() === 0,
        'OWNER_SEES_OTHER_ACTOR_FAVORITE')
      await forbiddenDelete(reader, data.otherFavoriteId)
    })
    await step('explicit_save_from_spanish_mobile_discovery', async () => {
      const saver = await session(browser, fixture, 'owner', 'es')
      const { page, text } = saver
      await openServices(saver, fixture)
      await page.getByRole('button', { name: text.newRequest, exact: true }).click()
      await page.getByLabel(text.source, { exact: true }).selectOption('listing')
      await page.getByLabel(text.choose, { exact: true }).selectOption(data.listingId)
      await page.getByLabel(text.propertyClass, { exact: true }).selectOption('house_villa')
      const [discovery] = await Promise.all([
        page.waitForResponse(response => endpoint(response, discoverPath)),
        page.getByLabel(text.category, { exact: true }).selectOption('pool_service'),
      ])
      require(discovery.status() === 200 && privateHeaders(discovery), 'ACTUAL_PROVIDER_DISCOVERY_REQUIRED')
      const row = page.getByRole('group', { name: data.providerAlias, exact: true })
      await row.waitFor()
      await fits(row)
      const selection = row.getByRole('checkbox')
      require(!await selection.isChecked() && result.favorite_saves === 0, 'DISCOVERY_MUTATED_BEFORE_EXPLICIT_SAVE')
      const [saved] = await Promise.all([
        page.waitForResponse(response => endpoint(response, favoritesPath, 'POST')),
        row.getByRole('button', { name: text.save, exact: true }).click(),
      ])
      const body = saved.request().postDataJSON()
      require(saved.status() === 201 && privateHeaders(saved) && hasKeys(body, ['provider_user_code'])
        && body.provider_user_code === data.providerUserCode, 'ONE_EXACT_SAVE_REQUEST_REQUIRED')
      const created = await saved.json()
      expectFavorite(created, data.providerUserCode, data.providerAlias)
      ownedFavoriteId = created.id
      require(ownedFavoriteId !== data.otherFavoriteId && !await selection.isChecked(),
        'SAVING_FAVORITE_SELECTED_RECIPIENT_OR_REUSED_OTHER_ROW')
      await row.getByRole('button', { name: text.saved, exact: true }).waitFor()
      await saver.context.close()
    })
    await step('provider_opt_out_redacts_existing_favorite', async () => {
      await foreground(reader.page)
      await panel.getByRole('button', { name: reader.text.refresh, exact: true }).click()
      await panel.getByText(data.providerAlias, { exact: true }).waitFor()
      expectFavorite(expectPage(await read(reader.context, favoritesPath), 1),
        data.providerUserCode, data.providerAlias, ownedFavoriteId)
      expectFavorite(expectPage(await read(outsider.context, favoritesPath), 1),
        data.otherProviderUserCode, data.otherProviderAlias, data.otherFavoriteId)
      await forbiddenDelete(outsider, ownedFavoriteId)

      const provider = await session(browser, fixture, 'winner')
      const nav = await openServices(provider, fixture)
      await nav.getByRole('button', { name: provider.text.preferences, exact: true }).click()
      const optIn = provider.page.getByRole('checkbox', { name: provider.text.optIn, exact: true })
      require(await optIn.isChecked(), 'PROVIDER_MUST_START_OPTED_IN')
      await optIn.uncheck()
      const [changed] = await Promise.all([
        provider.page.waitForResponse(response => endpoint(response, preferencesPath, 'PUT')),
        provider.page.getByRole('button', { name: provider.text.saveChanges, exact: true }).click(),
      ])
      const body = changed.request().postDataJSON()
      require(changed.status() === 200 && privateHeaders(changed) && body.service_opt_in === false
        && body.member_alias === data.providerAlias && body.expected_version === 1 && uuid.test(body.submission_id)
        && (await changed.json()).service_opt_in === false, 'REAL_PROVIDER_OPT_OUT_REQUIRED')
      await provider.context.close()

      await foreground(reader.page)
      const hidden = panel.getByText(reader.text.unavailable, { exact: true }).waitFor()
      const refresh = panel.getByRole('button', { name: reader.text.refresh, exact: true })
      if (await refresh.isVisible()) await Promise.race([hidden, refresh.click()])
      await hidden
      const entry = panel.getByRole('listitem').filter({ hasText: reader.text.unavailable })
      require(await panel.getByText(data.providerAlias, { exact: true }).count() === 0
        && !(await entry.innerText()).includes(String(data.providerUserCode)), 'REDACTED_FAVORITE_LEAKED_PROVIDER_IDENTITY')
      expectFavorite(expectPage(await read(reader.context, favoritesPath), 1), null, null, ownedFavoriteId)
      await fits(entry)
    })
    await step('owner_removes_redacted_entry_and_other_list_is_preserved', async () => {
      const unavailable = panel.getByRole('listitem').filter({ hasText: reader.text.unavailable })
      const [removed] = await Promise.all([
        reader.page.waitForResponse(response => endpoint(response, `${favoritesPath}/${ownedFavoriteId}`, 'DELETE')),
        unavailable.getByRole('button', { name: reader.text.remove, exact: true }).click(),
      ])
      require(removed.status() === 204 && privateHeaders(removed), 'OWNER_REMOVE_DID_NOT_COMPLETE')
      await panel.getByText(reader.text.empty, { exact: true }).waitFor()
      expectPage(await read(reader.context, favoritesPath), 0)
      expectFavorite(expectPage(await read(outsider.context, favoritesPath), 1),
        data.otherProviderUserCode, data.otherProviderAlias, data.otherFavoriteId)
    })
    await step('loopback_only_and_no_service_request_or_message_writes', async () => {
      require(!externalNavigation && result.favorite_saves === 1 && result.favorite_removes === 1
        && result.preference_updates === 1 && result.unexpected_browser_mutations === 0, 'UNEXPECTED_BROWSER_NETWORK_EFFECT')
    })
    result.status = 'PASS'
  } finally {
    const closed = await Promise.allSettled(contexts.map(context => context.close()))
    await browser.close()
    require(!browser.isConnected() && closed.every(entry => entry.status === 'fulfilled'), 'BROWSER_CLEANUP_FAILED')
  }
}

try { await main() }
catch (error) {
  result.status = 'FAIL'
  result.failed_step = stage
  result.error_code = error instanceof RehearsalFailure ? error.message
    : error?.name === 'TimeoutError' ? 'BROWSER_TIMEOUT' : 'UNEXPECTED_BROWSER_FAILURE'
  const frames = [...String(error?.stack).matchAll(/run-service-favorites-browser\.mjs:(\d{1,5}):\d+/g)]
  const frame = error instanceof RehearsalFailure && frames.length > 1 ? frames[1] : frames[0]
  if (frame) result.source_line = Number(frame[1])
  process.exitCode = 1
}
process.stdout.write(`SERVICE_FAVORITES_BROWSER_RESULT ${JSON.stringify(result)}\n`)
