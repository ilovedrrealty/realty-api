import { lstat, readFile } from 'node:fs/promises'
import { chromium } from 'playwright-core'

const dashboard = 'http://127.0.0.1:4175'
const api = 'http://127.0.0.1:8000'
const fixturePath = '/browser-fixtures/fixtures.json'
const matchPath = '/property-network/service-provider-matches'
const legacyPath = '/property-network/service-providers'
const requestsPath = '/property-network/service-requests'
const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/
const category = 'pool_service'
const propertyClass = 'house_villa'
const area = 'cabarete'
const listingTitle = 'Browser rehearsal villa'
const northAlias = 'Synthetic Reviewed North'
const englishAlias = 'Synthetic Reviewed English'
const privateAliases = ['Synthetic Held Import', 'Synthetic Hidden Provider', 'Synthetic Non Opted Provider',
  'Synthetic Other Area Provider', 'Synthetic Wrong Class Provider', 'synthetic-unshared@example.com']
const locale = {
  en: {
    network: 'Projects & member community', services: 'Private services', create: 'New private service request',
    sourceKind: 'Property context', source: 'Choose an authorized property', propertyClass: 'Property class',
    category: 'Requested service', mode: 'Provider search', area: 'Matching area', language: 'Preferred language (optional)',
    title: 'Shared request title', alias: 'Name or alias to share for this request', brief: 'Shared work brief',
    preview: 'Review exact shared request', ack: 'I authorize sharing these fields only with the selected members.',
    share: 'Share this private request',
  },
  es: {
    network: 'Proyectos y comunidad de miembros', services: 'Servicios privados', create: 'Nueva solicitud privada de servicio',
    sourceKind: 'Contexto de la propiedad', source: 'Elige una propiedad autorizada', propertyClass: 'Clase de propiedad',
    category: 'Servicio solicitado', mode: 'Búsqueda de profesionales', area: 'Zona para buscar', language: 'Idioma preferido (opcional)',
    title: 'Título compartido de la solicitud', alias: 'Nombre o alias para compartir en esta solicitud',
    brief: 'Descripción compartida del trabajo', preview: 'Revisar la solicitud exacta que se compartirá',
    ack: 'Autorizo compartir estos campos solo con los miembros seleccionados.', share: 'Compartir esta solicitud privada',
  },
}
let stage = 'fixture'
const checks = []

class FailedCheck extends Error {}
function check(condition, reason) { if (!condition) throw new FailedCheck(reason) }
function failureReason(error) {
  if (error instanceof FailedCheck) return error.message
  if (error?.name === 'TimeoutError') return 'LOCATOR_OR_EVENT_NOT_FOUND'
  if (String(error?.message).includes('strict mode violation')) return 'LOCATOR_AMBIGUOUS'
  return 'BROWSER_OPERATION_FAILED'
}

async function getFixture() {
  check(process.platform === 'linux' && process.env.GITHUB_ACTIONS === 'true'
    && process.env.GITHUB_REPOSITORY === 'ilovedrrealty/realty-api'
    && process.env.ILDR_BROWSER_FIXTURES === fixturePath, 'OWNED_SYNTHETIC_FIXTURE_REQUIRED')
  const file = await lstat(fixturePath)
  check(file.isFile() && (file.mode & 0o777) === 0o600 && file.size < 4096, 'PRIVATE_FIXTURE_REQUIRED')
  const data = JSON.parse(await readFile(fixturePath, 'utf8'))
  check(uuid.test(data.serviceSourceId) && uuid.test(data.foreignListingId)
    && data.serviceSourceId !== data.foreignListingId, 'OWNED_SOURCE_REQUIRED')
  const codes = Object.values(data.serviceCodes)
  check(codes.length === 6 && new Set(codes).size === 6
    && codes.every(code => Number.isSafeInteger(code) && code > 0), 'SYNTHETIC_CODES_REQUIRED')
  for (const [account, email] of [
    [data.accounts.owner, 'browser-owner@example.com'], [data.accounts.realtor, 'browser-realtor@example.com'],
    [data.accounts.buyer, 'browser-buyer@example.com'], [data.serviceProviders.north, 'browser-reviewed-north@example.com'],
    [data.serviceProviders.english, 'browser-reviewed-english@example.com'],
  ]) {
    check(account?.email === email && /^[A-Za-z0-9_-]{32}$/.test(account.password), 'SYNTHETIC_CREDENTIAL_REQUIRED')
  }
  return data
}

async function request(context, path, { method = 'GET', data, status = 200, noStore = true } = {}) {
  const response = await context.request.fetch(api + path, {
    method, data, headers: { Origin: dashboard }, maxRedirects: 0, timeout: 12_000,
  })
  try {
    check(response.status() === status, 'NATIVE_HTTP_' + response.status())
    if (noStore) check(response.headers()['cache-control'] === 'no-store', 'NO_STORE_REQUIRED')
    return await response.json()
  } finally {
    await response.dispose()
  }
}

async function session(browser, account, language = 'en') {
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, serviceWorkers: 'block',
    locale: language === 'es' ? 'es-DO' : 'en-US',
  })
  context.setDefaultTimeout(12_000)
  context.setDefaultNavigationTimeout(12_000)
  await context.route('**/*', route => [dashboard, api].includes(new URL(route.request().url()).origin)
    ? route.continue() : route.abort('blockedbyclient'))
  await context.addInitScript(value => {
    if (location.origin === 'http://127.0.0.1:4175') localStorage.setItem('ildr_lang', value)
    if (!navigator.onLine) Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => true })
  }, language)
  if (account) {
    await request(context, '/auth/login', { method: 'POST', data: account, noStore: false })
    check((await context.cookies(api)).some(cookie => cookie.name === 'ildr_token' && cookie.httpOnly), 'REAL_SYNTHETIC_LOGIN_REQUIRED')
  }
  return context
}

function observe(page, method, path, filters = {}) {
  return page.waitForResponse(response => {
    const url = new URL(response.url())
    return url.origin === api && url.pathname === path && response.request().method() === method
      && Object.entries(filters).every(([key, value]) => url.searchParams.get(key) === value)
  }, { timeout: 12_000 }).catch(() => null)
}

async function matchedResponse(pending, status = 200, noStore = true) {
  const response = await pending
  check(response, 'BROWSER_API_RESPONSE_NOT_OBSERVED')
  check(response.status() === status, 'BROWSER_HTTP_' + response.status())
  if (noStore) check(response.headers()['cache-control'] === 'no-store', 'NO_STORE_REQUIRED')
  return response
}

const button = (scope, name) => scope.getByRole('button', { name, exact: true })
const label = (scope, name) => scope.getByLabel(name, { exact: true })
const text = (scope, name) => scope.getByText(name, { exact: true })
const pathOf = (id, view) => requestsPath + '/' + id + '?view=' + view

function selected(page, alias) {
  return page.getByRole('group', { name: alias, exact: true }).getByRole('checkbox')
}

function matchFilters(data, language) {
  return { listing_id: data.serviceSourceId, category_id: category, property_class: propertyClass, area_id: area,
    language: language || null, page: '1' }
}

async function mobileLayout(page) {
  const layout = await page.locator('main').evaluate(node => ({
    touch: navigator.maxTouchPoints, right: node.getBoundingClientRect().right, width: innerWidth,
    rootWidth: document.documentElement.scrollWidth,
  }))
  check(layout.touch > 0 && layout.width === 390 && layout.right <= 391 && layout.rootWidth <= 391, 'MOBILE_LAYOUT_REQUIRED')
}

async function services(context, labels) {
  const page = await context.newPage()
  await page.bringToFront()
  const identity = observe(page, 'GET', '/auth/me')
  check((await page.goto(dashboard + '/', { waitUntil: 'domcontentloaded' }))?.ok(), 'REAL_DASHBOARD_REQUIRED')
  await matchedResponse(identity, 200, false)
  await page.locator('main button:has(svg.lucide-menu)').click()
  await button(page.locator('aside'), labels.network).click()
  check(new URL(page.url()).pathname === '/property-network', 'NORMAL_NETWORK_ROUTE_REQUIRED')
  const managed = observe(page, 'GET', requestsPath, { view: 'managed' })
  await button(page, labels.services).click()
  await matchedResponse(managed)
  return page
}

async function verifyCandidates(response, codes) {
  const output = await response.json()
  check(output.page === 1 && output.page_size === 30 && output.total === codes.length
    && Array.isArray(output.items) && output.items.length === codes.length, 'EXACT_CANDIDATE_PAGE_REQUIRED')
  check(output.items.every(item => Object.keys(item).sort().join(',') === 'category_ids,member_alias,user_code'
    && item.category_ids.includes(category) && codes.includes(item.user_code)), 'UNAUTHORIZED_PROVIDER_PROJECTION')
}

async function prepareMatch(context, data, language, browserLanguage, alias) {
  const labels = locale[browserLanguage]
  stage = browserLanguage === 'en' ? 'owner_entry' : 'realtor_entry'
  const page = await services(context, labels)
  await button(page, labels.create).click()
  check(await label(page, labels.mode).inputValue() === 'reviewed', 'DEFAULT_REVIEWED_REQUIRED')
  await label(page, labels.sourceKind).selectOption('listing')
  const source = label(page, labels.source)
  await source.locator(`option[value="${data.serviceSourceId}"]`).waitFor({ state: 'attached' })
  check(await source.locator(`option[value="${data.foreignListingId}"]`).count() === 0, 'FOREIGN_SOURCE_LISTED')
  await source.selectOption(data.serviceSourceId)
  await label(page, labels.propertyClass).selectOption(propertyClass)
  await label(page, labels.category).selectOption(category)
  await label(page, labels.language).selectOption(language)
  stage = browserLanguage === 'en' ? 'owner_discovery' : 'realtor_discovery'
  const matched = observe(page, 'GET', matchPath, matchFilters(data, language))
  await label(page, labels.area).selectOption(area)
  const result = await matchedResponse(matched)
  const actual = new URL(result.url()).searchParams
  check(!actual.has('property_id') && [...actual.keys()].sort().join(',')
    === 'area_id,category_id,language,listing_id,page,property_class', 'EXACT_MATCH_QUERY_REQUIRED')
  await verifyCandidates(result, [data.serviceCodes[language === 'es' ? 'north' : 'english']])
  await selected(page, alias).waitFor()
  check(await selected(page, alias).isChecked() === false, 'CANDIDATE_AUTOMATICALLY_SELECTED')
  for (const value of privateAliases) check(await text(page, value).count() === 0, 'PRIVATE_PROVIDER_RENDERED')
  check(await selected(page, language === 'es' ? englishAlias : northAlias).count() === 0, 'WRONG_LANGUAGE_RENDERED')
  await mobileLayout(page)
  return { page, labels }
}

async function ownerFilterEdits({ page, labels }, data) {
  stage = 'owner_filter_reset'
  await selected(page, northAlias).check()
  const anyLanguage = observe(page, 'GET', matchPath, matchFilters(data, null))
  await label(page, labels.language).selectOption('')
  await verifyCandidates(await matchedResponse(anyLanguage), [data.serviceCodes.north, data.serviceCodes.english])
  await selected(page, northAlias).waitFor()
  check(!(await selected(page, northAlias).isChecked()) && !(await selected(page, englishAlias).isChecked()),
    'FILTER_KEPT_STALE_SELECTION')
  const opted = observe(page, 'GET', legacyPath, { category_id: category, property_class: propertyClass, page: '1' })
  await label(page, labels.mode).selectOption('opt_in')
  await matchedResponse(opted)
  check(await label(page, labels.area).count() === 0, 'OPT_IN_MODE_USED_REVIEWED_FIELDS')
  const reviewedAgain = observe(page, 'GET', matchPath, matchFilters(data, null))
  await label(page, labels.mode).selectOption('reviewed')
  await verifyCandidates(await matchedResponse(reviewedAgain), [data.serviceCodes.north, data.serviceCodes.english])
  const restore = observe(page, 'GET', matchPath, matchFilters(data, 'es'))
  await label(page, labels.language).selectOption('es')
  await verifyCandidates(await matchedResponse(restore), [data.serviceCodes.north])
  await selected(page, northAlias).waitFor()
  check(!await selected(page, northAlias).isChecked(), 'MODE_KEPT_STALE_SELECTION')
}

async function createQuote({ page, labels }, data, role) {
  stage = role === 'owner' ? 'owner_create' : 'realtor_create'
  await page.bringToFront()
  const owner = role === 'owner'
  const alias = owner ? northAlias : englishAlias
  const code = data.serviceCodes[owner ? 'north' : 'english']
  const language = owner ? 'es' : 'en'
  const title = `Synthetic ${role} reviewed request`
  const brief = `Synthetic ${role} pool maintenance brief`
  await selected(page, alias).check()
  await label(page, labels.title).fill(title)
  await label(page, labels.alias).fill(`Synthetic ${role}`)
  await label(page, labels.brief).fill(brief)
  await button(page, labels.preview).click()
  const preview = page.getByRole('region', { name: labels.preview, exact: true })
  await preview.getByRole('heading', { name: title, exact: true }).waitFor()
  await text(preview, brief).waitFor()
  await text(preview, 'Cabarete').waitFor()
  check(await preview.getByRole('listitem').count() === 1 && (await preview.getByRole('listitem').innerText()).includes(alias),
    'ONLY_EXPLICIT_RECIPIENT_IN_PREVIEW_REQUIRED')
  check(!await button(page, labels.share).isEnabled(), 'SHARING_WITHOUT_ACK_AVAILABLE')
  await label(preview, labels.ack).check()
  const operation = observe(page, 'POST', requestsPath)
  await button(page, labels.share).click()
  const created = await matchedResponse(operation, 201)
  const sent = created.request().postDataJSON()
  check(sent.listing_id === data.serviceSourceId && sent.property_id === null && sent.title === title
    && sent.shared_brief_acknowledged === true && sent.category_id === category && sent.property_class === propertyClass
    && sent.reviewed_match.area_id === area && sent.reviewed_match.language === language
    && sent.recipient_user_codes.length === 1 && sent.recipient_user_codes[0] === code, 'EXACT_QUOTE_SCOPE_REQUIRED')
  const row = await created.json()
  check(uuid.test(row.id) && row.view === 'managed' && row.recipients.length === 1
    && row.recipients[0].user_code === code && row.recipients[0].status === 'pending'
    && row.source.listing_id === data.serviceSourceId, 'EXACT_PENDING_INVITATION_REQUIRED')
  await page.getByRole('heading', { name: title, exact: true }).waitFor()
  await mobileLayout(page)
  return { id: row.id, title, brief }
}

async function providerView(context, quote, other) {
  const page = await services(context, locale.en)
  const received = observe(page, 'GET', requestsPath, { view: 'received' })
  await button(page, 'Requests for me').click()
  const pageData = await (await matchedResponse(received)).json()
  check(pageData.items.length === 1 && pageData.items[0].id === quote.id, 'RECEIVED_INBOX_SCOPE_REQUIRED')
  const card = page.getByRole('article').filter({ has: page.getByRole('heading', { name: quote.title, exact: true }) })
  await card.waitFor()
  check(await text(page, other.title).count() === 0, 'OTHER_REQUEST_VISIBLE')
  const detail = observe(page, 'GET', requestsPath + '/' + quote.id, { view: 'received' })
  await button(card, 'Open request').click()
  const brief = await (await matchedResponse(detail)).json()
  check(brief.id === quote.id && brief.brief === quote.brief && brief.view === 'received' && brief.capabilities.can_accept
    && !('source' in brief) && !('recipients' in brief) && !('listing_id' in brief), 'EXACT_PRIVATE_RECEIVED_PROJECTION_REQUIRED')
  await text(page, quote.brief).waitFor()
  await button(page, 'Accept request').waitFor()
  check(await text(page, listingTitle).count() === 0, 'PRIVATE_LISTING_RENDERED')
  await mobileLayout(page)
  await request(context, pathOf(other.id, 'received'), { status: 404 })
}

async function main() {
  const data = await getFixture()
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
  try {
    stage = 'auth'
    const owner = await session(browser, data.accounts.owner)
    const realtor = await session(browser, data.accounts.realtor, 'es')
    const north = await session(browser, data.serviceProviders.north)
    const english = await session(browser, data.serviceProviders.english)
    const outside = await session(browser, data.accounts.buyer)
    stage = 'scope'
    const query = new URLSearchParams({ ...matchFilters(data, 'es'), listing_id: data.foreignListingId })
    await request(owner, matchPath + '?' + query, { status: 404 })
    query.set('listing_id', data.serviceSourceId)
    await request(outside, matchPath + '?' + query, { status: 404 })
    checks.push('foreign_and_unassigned_sources_denied')
    const ownerStart = await prepareMatch(owner, data, 'es', 'en', northAlias)
    checks.push('reviewed_area_language_class_and_held_exclusion')
    await ownerFilterEdits(ownerStart, data)
    checks.push('criteria_and_mode_clear_prior_selection')
    const ownerQuote = await createQuote(ownerStart, data, 'owner')
    checks.push('owner_reviews_and_sends_exact_private_request')
    const realtorStart = await prepareMatch(realtor, data, 'en', 'es', englishAlias)
    const realtorQuote = await createQuote(realtorStart, data, 'realtor')
    checks.push('spanish_assigned_realtor_sends_exact_private_request')
    stage = 'received_north'
    await providerView(north, ownerQuote, realtorQuote)
    stage = 'received_english'
    await providerView(english, realtorQuote, ownerQuote)
    stage = 'unassigned_received_scope'
    await request(outside, pathOf(ownerQuote.id, 'received'), { status: 404 })
    await request(outside, pathOf(realtorQuote.id, 'received'), { status: 404 })
    checks.push('selected_providers_receive_only_their_own_scoped_brief')
    console.log('REVIEWED_SERVICE_MATCHES_BROWSER', JSON.stringify({ status: 'PASS', checks }))
  } finally {
    await browser.close()
  }
}

main().catch(error => {
  console.error('REVIEWED_SERVICE_MATCHES_BROWSER_FAILURE', stage, failureReason(error))
  process.exitCode = 1
})
