import { lstat, readFile, readdir } from 'node:fs/promises'
import { chromium } from 'playwright-core'

const api = 'http://127.0.0.1:8918'
const landing = 'http://127.0.0.1:8917'
const fixturePath = '/browser-fixtures/fixtures.json'
const publicPath = '/site/market-reports'
const route = '/local-guides/market-report/'
const Sosua = 'Sosúa'
const emptyArea = 'Azua'
const checks = []
let stage = 'fixture'

class CheckFailure extends Error {
  constructor(code, status) {
    super(code)
    this.status = status
  }
}
function check(condition, code, status) { if (!condition) throw new CheckFailure(code, status) }
function failureCode(error) {
  if (error instanceof CheckFailure) return error.message
  if (String(error?.message).includes('strict mode violation')) return 'LOCATOR_AMBIGUOUS'
  if (String(error?.message).includes('did not find some options')) return 'SELECT_OPTION_NOT_FOUND'
  return error?.name === 'TimeoutError' ? 'LOCATOR_OR_EVENT_NOT_FOUND' : 'BROWSER_OPERATION_FAILED'
}

async function verifyFixture() {
  check(process.platform === 'linux' && process.env.GITHUB_ACTIONS === 'true'
    && process.env.GITHUB_REPOSITORY === 'ilovedrrealty/realty-api'
    && process.env.ILDR_BROWSER_FIXTURES === fixturePath, 'EXACT_HOSTED_FIXTURE_REQUIRED')
  check((await readdir('/sys/class/net')).join(',') === 'lo', 'LOOPBACK_NAMESPACE_REQUIRED')
  const stat = await lstat(fixturePath)
  check(stat.isFile() && (stat.mode & 0o777) === 0o600 && stat.size < 256, 'PRIVATE_FIXTURE_REQUIRED')
  const data = JSON.parse(await readFile(fixturePath, 'utf8'))
  check(Object.keys(data).sort().join(',') === 'empty_scope,local_scope,stage'
    && data.stage === 'market_report' && data.local_scope === Sosua && data.empty_scope === emptyArea, 'EXACT_SYNTHETIC_SCOPE_REQUIRED')
}

async function session(browser, mobile = false) {
  const context = await browser.newContext({
    viewport: mobile ? { width: 390, height: 844 } : { width: 1280, height: 900 },
    locale: mobile ? 'es-DO' : 'en-US', isMobile: mobile, hasTouch: mobile, serviceWorkers: 'block',
  })
  context.setDefaultTimeout(12000)
  context.setDefaultNavigationTimeout(12000)
  await context.route('**/*', request => {
    const browserRequest = request.request()
    const url = new URL(browserRequest.url())
    return [api, landing].includes(url.origin) && ['GET', 'HEAD', 'OPTIONS'].includes(browserRequest.method())
      ? request.continue() : request.abort('blockedbyclient')
  })
  return context
}

function waitReport(page, location) {
  return page.waitForResponse(response => {
    const url = new URL(response.url())
    return url.origin === api && url.pathname === publicPath && response.request().method() === 'GET'
      && (location ? url.searchParams.size === 1 && url.searchParams.get('location') === location : url.searchParams.size === 0)
  }).catch(() => null)
}

async function readResult(pending, location) {
  const response = await pending
  check(response, 'MARKET_BROWSER_RESPONSE_REQUIRED')
  check(response.status() === 200, 'MARKET_BROWSER_STATUS_MISMATCH', response.status())
  check(response.headers()['cache-control'] === 'no-store', 'MARKET_RESPONSE_MUST_BE_CURRENT')
  const headers = await response.request().allHeaders()
  check(!headers.cookie && !headers.authorization, 'ANONYMOUS_REPORT_REQUIRED')
  const data = await response.json()
  const local = new Intl.DateTimeFormat('sv-SE', { timeZone: 'America/Santo_Domingo', dateStyle: 'short' })
  check(Object.keys(data).sort().join(',') === 'as_of,groups,local_date,location,total_listings,unclassified_currency_count'
    && data.location === location && Math.abs(Date.parse(data.as_of) - Date.now()) <= 300_000
    && local.format(new Date(data.as_of)) === data.local_date, 'EXACT_CURRENT_SCOPE_REQUIRED')
  check(!JSON.stringify(data).includes('D02_SYNTHETIC_'), 'PRIVATE_LISTING_DETAILS_RETURNED')
  return data
}

function expectCohort(data, propertyType, currency, expected) {
  const cohort = data.groups.find(group => group.property_type === propertyType && group.currency === currency)
  check(cohort && Object.keys(cohort).sort().join(',') === 'area_basis,currency,listing_count,median_price_per_sqm,price_count,price_max,price_median,price_min,property_type,size_count'
    && Object.entries(expected).every(([key, value]) => cohort[key] === value), 'EXACT_NATIVE_COHORT_REQUIRED')
}

const link = (scope, name) => scope.getByRole('link', { name, exact: true })
const button = (scope, name) => scope.getByRole('button', { name, exact: true })
const heading = (scope, name) => scope.getByRole('heading', { name, exact: true })
const text = (scope, name) => scope.getByText(name, { exact: true })
const currencySection = (scope, name) => scope.getByRole('region', { name, exact: true })

function card(page, currency, name) {
  return currencySection(page, currency).getByRole('article').filter({ has: heading(page, name) })
}

async function metric(scope, label, expected) {
  const term = scope.locator('dt').filter({ hasText: new RegExp('^' + label + '$') })
  await term.waitFor()
  const value = await term.evaluate(node => node.nextElementSibling.textContent.trim().replace(/\s+/g, ' '))
  check(value === expected, 'VISIBLE_METRIC_MISMATCH')
}

async function layout(page, mobile) {
  const shape = await page.evaluate(() => ({
    width: innerWidth, content: document.documentElement.scrollWidth, touch: navigator.maxTouchPoints,
  }))
  check(shape.width === (mobile ? 390 : 1280) && shape.content <= shape.width + 1
    && (!mobile || shape.touch > 0), 'RESPONSIVE_LAYOUT_REQUIRED')
}

async function openGuide(page, language, name) {
  check((await page.goto(landing + '/local-guides/?lang=' + language, { waitUntil: 'domcontentloaded' }))?.ok(),
    'PUBLIC_GUIDES_EXPORT_REQUIRED')
  const normal = link(page, name)
  await normal.waitFor()
  check(await normal.getAttribute('href') === route + '?lang=' + language, 'NORMAL_GUIDE_ENTRY_REQUIRED')
  const result = waitReport(page, null)
  await normal.click()
  return readResult(result, null)
}

async function chooseArea(page, labels, area, keyboard = false) {
  const choice = page.getByRole('combobox', { name: labels.location, exact: true })
  await choice.selectOption(area)
  const result = waitReport(page, area)
  const apply = button(page, labels.apply)
  if (keyboard) {
    await apply.focus()
    check(await apply.evaluate(node => document.activeElement === node), 'FILTER_KEYBOARD_FOCUS_REQUIRED')
    await apply.press('Enter')
  } else await apply.click()
  return readResult(result, area)
}

async function main() {
  await verifyFixture()
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
  try {
    stage = 'desktop_resources'
    const desktop = await session(browser)
    const page = await desktop.newPage()
    await page.bringToFront()
    check((await page.goto(landing + '/blog/', { waitUntil: 'domcontentloaded' }))?.ok(), 'PUBLIC_RESOURCES_EXPORT_REQUIRED')
    const resources = link(page, 'Local market report')
    await resources.waitFor()
    check(await resources.getAttribute('href') === route + '?lang=en', 'NORMAL_RESOURCES_ENTRY_REQUIRED')
    stage = 'desktop_sitewide'
    const sitewide = await openGuide(page, 'en', 'Local market report')
    check(sitewide.total_listings === 7 && sitewide.unclassified_currency_count === 1 && sitewide.groups.length === 3,
      'EXACT_SITEWIDE_COVERAGE_REQUIRED')
    expectCohort(sitewide, 'villa', 'USD', { listing_count: 4, price_count: 4, size_count: 1, price_min: '210000.00',
      price_median: '355000.005', price_max: '900000.00', median_price_per_sqm: '2260.42', area_basis: 'living' })
    await heading(page, 'All locations in our sale inventory').waitFor()
    await text(page, 'Active sale listings · 7').waitFor()
    await layout(page, false)
    checks.push('normal_resources_and_local_guides_show_sitewide_sale_inventory')

    stage = 'desktop_scope'
    const scoped = await chooseArea(page, { location: 'Report location', apply: 'View report' }, Sosua)
    check(scoped.total_listings === 5 && scoped.unclassified_currency_count === 1 && scoped.groups.length === 3,
      'EXACT_SCOPED_COVERAGE_REQUIRED')
    expectCohort(scoped, 'villa', 'USD', { listing_count: 2, price_count: 2, size_count: 1, price_min: '210000.00',
      price_median: '210000.005', price_max: '210000.01', median_price_per_sqm: '2260.42', area_basis: 'living' })
    expectCohort(scoped, 'villa', 'DOP', { listing_count: 1, price_count: 1, size_count: 0, price_median: '995000.500',
      median_price_per_sqm: null, area_basis: 'living' })
    expectCohort(scoped, 'land', 'DOP', { listing_count: 1, price_count: 0, size_count: 0, price_min: null,
      price_median: null, price_max: null, median_price_per_sqm: null, area_basis: 'lot' })
    await heading(page, Sosua).waitFor()
    const oldLocaleLink = link(page, 'Español')
    check(await oldLocaleLink.getAttribute('href') === route + '?lang=es&location=Sos%C3%BAa', 'LOCALE_SCOPE_NOT_RETAINED')
    const englishUSD = card(page, 'USD', 'Villa')
    await metric(englishUSD, 'Median asking price', 'USD 210,000.005')
    await metric(englishUSD, 'Listings with a usable asking price', '2')
    await metric(englishUSD, 'Listings used for price per m²', '1')
    await metric(card(page, 'DOP', 'Villa'), 'Median asking price', 'DOP 995,000.50')
    await text(card(page, 'DOP', 'Land'), 'No asking prices are available for this group.').waitFor()
    check(await currencySection(page, 'EUR').count() === 0, 'UNSUPPORTED_CURRENCY_GROUP_RENDERED')
    checks.push('real_postgres_scope_preserves_native_prices_samples_and_hidden_boundary')

    stage = 'spanish_entry'
    const mobile = await session(browser, true)
    const mobilePage = await mobile.newPage()
    await mobilePage.bringToFront()
    await openGuide(mobilePage, 'es', 'Informe del mercado local')
    await heading(mobilePage, 'Informe del mercado local').waitFor()
    stage = 'spanish_scope'
    const mobileLabels = { location: 'Ubicación del informe', apply: 'Ver informe' }
    await chooseArea(mobilePage, mobileLabels, Sosua, true)
    await heading(mobilePage, Sosua).waitFor()
    await text(mobilePage, 'Anuncios activos de venta · 5').waitFor()
    await text(mobilePage, '1 anuncio sin una moneda admitida figura solo en el total.').waitFor()
    await metric(card(mobilePage, 'USD', 'Villa'), 'Anuncios usados para el precio por m²', '1')
    await text(mobilePage, 'Mediana del precio de oferta por m² · Área del terreno').waitFor()
    await layout(mobilePage, true)
    checks.push('spanish_mobile_keyboard_filter_and_area_labels')

    stage = 'sale_search'
    const search = link(mobilePage, 'Buscar propiedades en venta en Sosúa')
    check(await search.getAttribute('href') === '/search/?purpose=sale&location=Sos%C3%BAa', 'CANONICAL_SALE_SEARCH_REQUIRED')
    const searchApi = mobilePage.waitForResponse(response => {
      const url = new URL(response.url())
      return response.request().method() === 'GET' && url.origin === api && url.pathname === '/listings'
        && url.searchParams.get('purpose') === 'sale' && url.searchParams.getAll('locations').join(',') === Sosua
    }).catch(() => null)
    await search.click()
    await mobilePage.waitForURL(url => url.pathname === '/search/' && url.searchParams.get('location') === Sosua)
    const searchResponse = await searchApi
    check(searchResponse && searchResponse.status() === 200, 'ACTUAL_SCOPED_SALE_SEARCH_REQUIRED', searchResponse?.status())
    checks.push('sale_search_receives_exact_catalog_location')

    stage = 'empty_scope'
    const zeroRead = waitReport(mobilePage, emptyArea)
    check((await mobilePage.goto(landing + route + '?lang=es&location=' + emptyArea, { waitUntil: 'domcontentloaded' }))?.ok(),
      'PUBLIC_MARKET_EXPORT_REQUIRED')
    const zero = await readResult(zeroRead, emptyArea)
    check(zero.total_listings === 0 && zero.unclassified_currency_count === 0 && zero.groups.length === 0, 'ZERO_SCOPE_REQUIRED')
    await text(mobilePage, 'No hay anuncios activos de venta en este informe.').waitFor()
    check(await currencySection(mobilePage, 'USD').count() === 0 && await currencySection(mobilePage, 'DOP').count() === 0,
      'EMPTY_SCOPE_RENDERED_CURRENCY_GROUP')
    stage = 'invalid_scope'
    let requests = 0
    mobilePage.on('request', request => { if (new URL(request.url()).pathname === publicPath) requests++ })
    await mobilePage.goto(landing + route + '?lang=es&location=Azua&location=Cabarete', { waitUntil: 'domcontentloaded' })
    await text(mobilePage, 'Elige una ubicación del informe en la lista.').waitFor()
    check(requests === 0 && await link(mobilePage, 'Buscar todas las propiedades en venta').count() === 0,
      'INVALID_SCOPE_SILENTLY_WIDENED')
    checks.push('empty_and_invalid_scopes_never_invent_prices_or_widen_scope')
    check((await desktop.cookies(api)).length === 0 && (await mobile.cookies(api)).length === 0, 'UNEXPECTED_BROWSER_SESSION')
    checks.push('actual_loopback_anonymous_read_only')
    console.log('MARKET_REPORT_BROWSER', JSON.stringify({ status: 'PASS', checks }))
  } finally {
    await browser.close()
  }
}

main().catch(error => {
  const status = error instanceof CheckFailure && Number.isInteger(error.status) ? ' status=' + error.status : ''
  console.error('MARKET_REPORT_BROWSER_FAILURE', stage, failureCode(error) + status)
  process.exitCode = 1
})
