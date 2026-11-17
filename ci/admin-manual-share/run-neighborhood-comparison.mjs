import { lstat, readFile, readdir } from 'node:fs/promises'
import { chromium } from 'playwright-core'

const api = 'http://127.0.0.1:8000'
const dashboard = 'http://127.0.0.1:4175'
const landing = 'http://127.0.0.1:8917'
const fixturePath = '/browser-fixtures/fixtures.json'
const privateNote = 'D01_PRIVATE_EDITORIAL_NOTE'
const changedFact = 'D01_UNPUBLISHED_ALTERNATE_FACT'
const publishedOn = '2025-10-22'
const profiles = [
  { slug: 'fixture-neighborhood-bay', en: 'Fixture Bay guide', es: 'Guía de bahía sintética',
    location: 'Fixture Bay', topic: 'access', label: 'Synthetic Bay reference', url: 'https://example.org/d01-fixture/bay',
    english: 'Synthetic Bay access fact.', spanish: 'Dato sintético de acceso a la bahía.' },
  { slug: 'fixture-neighborhood-ridge', en: 'Fixture Ridge guide', es: 'Guía de loma sintética',
    location: 'Fixture Ridge', topic: 'utilities', label: 'Synthetic Ridge reference', url: 'https://example.org/d01-fixture/ridge',
    english: 'Synthetic Ridge utilities fact.', spanish: 'Dato sintético de servicios de la loma.' },
]
const checks = []
const uuid = /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/
let stage = 'fixture'

class CheckFailure extends Error {
  constructor(code, status) {
    super(code)
    this.status = status
  }
}

function check(condition, code, status) {
  if (!condition) throw new CheckFailure(code, status)
}

async function fixtureData() {
  check(process.platform === 'linux' && process.env.GITHUB_ACTIONS === 'true'
    && process.env.GITHUB_REPOSITORY === 'ilovedrrealty/realty-api'
    && process.env.ILDR_BROWSER_FIXTURES === fixturePath, 'EXACT_HOSTED_FIXTURE_REQUIRED')
  check((await readdir('/sys/class/net')).join(',') === 'lo', 'LOOPBACK_NAMESPACE_REQUIRED')
  const info = await lstat(fixturePath)
  check(info.isFile() && (info.mode & 0o777) === 0o600 && info.size < 4096, 'PRIVATE_FIXTURE_REQUIRED')
  const fixture = JSON.parse(await readFile(fixturePath, 'utf8'))
  for (const role of ['admin', 'realtor']) {
    const login = fixture.accounts?.[role]
    check(login?.email === `browser-${role}@example.com` && /^[a-zA-Z0-9_-]{32}$/.test(login.password),
      'EXACT_SYNTHETIC_ACCOUNTS_REQUIRED')
  }
  const ids = fixture.comparisonListingIds
  check(uuid.test(ids?.bay) && uuid.test(ids?.ridge) && ids.bay !== ids.ridge, 'EXACT_SYNTHETIC_LISTINGS_REQUIRED')
  return fixture
}

function request(context, path, method = 'GET', data) {
  return context.request.fetch(api + path, {
    method, data, headers: { Origin: dashboard }, maxRedirects: 0, timeout: 12000,
  })
}

async function responseJson(response, expected) {
  check(response.status() === expected, 'API_STATUS_MISMATCH', response.status())
  check((response.headers()['cache-control'] ?? '').includes('no-store'), 'NO_STORE_RESPONSE_REQUIRED')
  return response.json()
}

async function apiResult(context, path, expected = 200) {
  const response = await request(context, path)
  try { return await responseJson(response, expected) } finally { await response.dispose() }
}

async function session(browser, accounts = null, mobile = false) {
  const context = await browser.newContext({
    viewport: mobile ? { width: 390, height: 844 } : { width: 1280, height: 900 },
    locale: 'en-US', serviceWorkers: 'block',
  })
  context.setDefaultTimeout(12000)
  await context.route('**/*', route => {
    const target = new URL(route.request().url())
    return [dashboard, api, landing].includes(target.origin) ? route.continue() : route.abort('blockedbyclient')
  })
  await context.addInitScript(() => {
    if (!navigator.onLine) Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => true })
  })
  if (accounts) {
    const result = await request(context, '/auth/login', 'POST', accounts)
    check(result.status() === 200, 'SYNTHETIC_LOGIN_REQUIRED', result.status())
    await result.dispose()
    check((await context.cookies(api)).some(cookie => cookie.name === 'ildr_token' && cookie.httpOnly),
      'SYNTHETIC_AUTH_COOKIE_REQUIRED')
  }
  return context
}

function waitResponse(page, path, method = 'GET', query = null) {
  const pending = page.waitForResponse(response => {
    const url = new URL(response.url())
    return url.origin === api && url.pathname === path && response.request().method() === method
      && (!query || query(url.searchParams))
  })
  pending.catch(() => {}) // The click can fail before the caller awaits the response.
  return pending
}

const button = (scope, name) => scope.getByRole('button', { name, exact: true })
const group = (scope, name) => scope.getByRole('group', { name, exact: true })
const textField = (scope, name) => scope.getByRole('textbox', { name: new RegExp('^' + name) })
const labeled = (scope, label, input) => scope.getByText(label, { exact: true }).locator('..').locator(input)

function editorialForm(page) {
  return page.locator('form').filter({ has: button(page, 'Save draft') })
}

async function action(page, base, operation, control, confirmation) {
  await button(page, control).click()
  const dialog = page.getByRole('dialog', { name: control, exact: true })
  const pending = waitResponse(page, base + '/' + operation, 'POST')
  await button(dialog, confirmation).click()
  const response = await responseJson(await pending, 200)
  await dialog.waitFor({ state: 'hidden' })
  return response
}

async function createProfile(admin, publicContext, profile, ordinal) {
  stage = ordinal + '_form'
  await button(admin, 'New article or guide').click()
  const form = editorialForm(admin)
  await textField(form, 'Slug').fill(profile.slug)
  await form.getByRole('combobox', { name: /^Content type/ }).selectOption('neighborhood')
  for (const [locale, label] of [['en', 'English'], ['es', 'Spanish']]) {
    const translation = group(form, label)
    await textField(translation, 'Title').fill(profile[locale])
    await textField(translation, 'Summary').fill(locale === 'en' ? 'Synthetic guide summary.' : 'Resumen de la guía sintética.')
    await textField(translation, 'Body').fill(locale === 'en' ? 'Synthetic guide body.' : 'Texto de guía sintético.')
  }
  const firstSource = textField(form, 'Source label 1')
  if (!(await firstSource.isVisible())) await button(form, 'Add source').click()
  await firstSource.fill(profile.label)
  await textField(form, 'Source URL 1').fill(profile.url)
  await textField(form, 'Review note').fill(privateNote)
  const comparison = group(form, 'Neighborhood comparison facts')
  await button(comparison, 'Add comparison fact').click()
  await labeled(comparison, 'Listing search location (optional)', 'input').fill(profile.location)
  const fact = group(comparison, 'Comparison fact 1')
  await labeled(fact, 'Topic', 'select').selectOption(profile.topic)
  await labeled(fact, 'Date checked', 'input').fill(publishedOn)
  await labeled(fact, 'Fact in English', 'textarea').fill(profile.english)
  await labeled(fact, 'Fact in Spanish', 'textarea').fill(profile.spanish)
  await labeled(fact, 'Public source', 'select').selectOption(profile.url)

  stage = ordinal + '_draft'
  const pending = waitResponse(admin, '/admin/editorial', 'POST')
  await button(form, 'Save draft').click()
  const created = await responseJson(await pending, 201)
  check(uuid.test(created.id) && created.draft_revision === 1 && created.published_revision === null,
    'UNPUBLISHED_DRAFT_REQUIRED')
  const stored = created.content?.neighborhood
  check(stored?.listing_location === profile.location && stored.facts?.length === 1
    && stored.facts[0].topic === profile.topic && stored.facts[0].source_url === profile.url
    && stored.facts[0].en === profile.english && stored.facts[0].es === profile.spanish
    && stored.facts[0].checked_on === publishedOn, 'SOURCED_BILINGUAL_FACT_REQUIRED')
  const endpoint = '/site/neighborhoods/' + profile.slug
  const detail = '/admin/editorial/' + created.id
  await apiResult(publicContext, endpoint + '?locale=en', 404)

  stage = ordinal + '_review'
  const reviewed = await action(admin, detail, 'review', 'Review revision', 'Confirm review')
  check(reviewed.reviewed_revision === 1 && reviewed.published_revision === null, 'REVIEW_WITHOUT_PUBLICATION_REQUIRED')
  await apiResult(publicContext, endpoint + '?locale=es', 404)

  stage = ordinal + '_publish'
  const published = await action(admin, detail, 'publish', 'Publish revision', 'Confirm publish')
  check(published.published_revision === 1 && published.version > reviewed.version, 'EXACT_REVIEWED_PUBLICATION_REQUIRED')
  const projection = await apiResult(publicContext, endpoint + '?locale=es')
  check(projection.title === profile.es && projection.facts[0]?.text === profile.spanish
    && !JSON.stringify(projection).includes(privateNote), 'PUBLIC_LOCALIZED_PROJECTION_REQUIRED')
  return { detail, endpoint, published }
}

async function mobileComparison(page, locale) {
  for (const profile of profiles) await page.getByRole('checkbox', { name: profile[locale], exact: true }).check()
  const localized = locale === 'es'
  const pending = profiles.map(profile => waitResponse(page, '/site/neighborhoods/' + profile.slug, 'GET',
    params => params.getAll('locale').join(',') === locale))
  await button(page, localized ? 'Comparar zonas seleccionadas' : 'Compare selected neighborhoods').click()
  const results = page.getByRole('region', { name: localized ? 'Comparación de zonas' : 'Neighborhood comparison', exact: true })
  for (const response of await Promise.all(pending)) {
    const data = await responseJson(response, 200)
    check(!JSON.stringify(data).includes(privateNote), 'PRIVATE_FIELDS_MISSING_FROM_PUBLIC_PROJECTION')
  }
  const topics = localized ? ['Acceso', 'Servicios básicos'] : ['Access', 'Utilities']
  for (const [index, profile] of profiles.entries()) {
    const region = results.getByRole('region', { name: topics[index], exact: true })
    const fact = region.getByText(localized ? profile.spanish : profile.english, { exact: true })
    await fact.waitFor({ state: 'visible' })
    await region.getByText(localized ? 'No hay datos con fuente.' : 'No sourced fact available.', { exact: true })
      .waitFor({ state: 'visible' })
    const source = region.getByRole('link', { name: profile.label, exact: true })
    check(await source.getAttribute('href') === profile.url && await source.getAttribute('target') === '_blank'
      && (await source.getAttribute('rel'))?.split(' ').includes('noopener'), 'SAFE_SOURCE_LINK_REQUIRED')
    check(await region.locator('time').getAttribute('datetime') === publishedOn, 'SOURCE_CHECK_DATE_REQUIRED')
    const cells = region.getByRole('listitem')
    check(await cells.count() === 2, 'TWO_TOPIC_AREA_CELLS_REQUIRED')
    const [first, second] = await Promise.all([cells.nth(0).boundingBox(), cells.nth(1).boundingBox()])
    check(first && second && second.y >= first.y + first.height, 'MOBILE_STACKED_COMPARISON_REQUIRED')
  }
  check(await page.evaluate(() => innerWidth === 390 && document.documentElement.scrollWidth <= innerWidth),
    'MOBILE_COMPARISON_OVERFLOW')
  check(!(await page.locator('main').innerText()).includes(privateNote), 'PRIVATE_NOTE_NOT_PUBLIC')
  return results
}

function catalogQuery(locale) {
  return params => params.getAll('locale').join(',') === locale
    && params.getAll('page').join(',') === '1' && params.getAll('page_size').join(',') === '20'
}

async function main() {
  const fixture = await fixtureData()
  const browser = await chromium.launch({
    headless: true, timeout: 20000, proxy: { server: 'http://127.0.0.1:9', bypass: '127.0.0.1' },
    args: ['--disable-background-networking', '--disable-component-update', '--disable-sync', '--no-first-run'],
  })
  try {
    stage = 'auth'
    const admin = await session(browser, fixture.accounts.admin)
    const realtor = await session(browser, fixture.accounts.realtor)
    const publicContext = await session(browser, null, true)
    const adminPage = await admin.newPage()
    const page = await publicContext.newPage()
    const identity = waitResponse(adminPage, '/auth/me')
    await adminPage.goto(dashboard + '/editorial?workspace=admin', { waitUntil: 'domcontentloaded' })
    const actor = await identity
    check(actor.status() === 200 && (await actor.json()).role === 'admin', 'EXACT_ADMIN_IDENTITY_REQUIRED', actor.status())

    stage = 'scope'
    await apiResult(publicContext, '/admin/editorial', 401)
    await apiResult(realtor, '/admin/editorial', 403)
    check((await apiResult(publicContext, '/site/neighborhoods?locale=en&page=1&page_size=20')).total === 0,
      'EMPTY_NEIGHBORHOOD_FIXTURE_REQUIRED')
    await createProfile(adminPage, publicContext, profiles[0], 'first')
    await button(adminPage, 'Back to editorial content').click()
    const ridge = await createProfile(adminPage, publicContext, profiles[1], 'second')
    checks.push('admin_only_two_bilingual_staged_neighborhoods', 'reviewed_sources_and_explicit_publication')

    stage = 'public_index_english'
    const directory = await page.goto(landing + '/local-guides/?lang=en', { waitUntil: 'domcontentloaded' })
    check(directory?.status() === 200, 'STATIC_PUBLIC_DIRECTORY_REQUIRED', directory?.status())
    const comparison = page.getByRole('link', { name: 'Compare neighborhoods', exact: true })
    check(await comparison.getAttribute('href') === '/local-guides/compare/?lang=en', 'NORMAL_COMPARE_HREF_REQUIRED')
    const publicCatalog = waitResponse(page, '/site/neighborhoods', 'GET', catalogQuery('en'))
    await comparison.click()
    check((await responseJson(await publicCatalog, 200)).total === 2, 'EXACT_TWO_PUBLISHED_AREAS_REQUIRED')
    await page.getByRole('heading', { name: 'Compare neighborhoods', exact: true, level: 1 }).waitFor()

    stage = 'public_compare_english'
    const english = await mobileComparison(page, 'en')
    checks.push('public_next_english_two_area_comparison_390px')

    stage = 'listing_handoff'
    const propertyLink = english.getByRole('link', { name: 'Search listings in Fixture Bay', exact: true })
    check(await propertyLink.getAttribute('href') === '/search/?location=Fixture+Bay', 'EXACT_LISTING_SEARCH_HREF_REQUIRED')
    const matching = waitResponse(page, '/listings', 'GET', params => params.getAll('locations').join(',') === 'Fixture Bay'
      && params.get('page_size') === '24')
    await propertyLink.click()
    const listingResponse = await matching
    check(listingResponse.status() === 200, 'LISTING_SEARCH_STATUS_MISMATCH', listingResponse.status())
    const listings = await listingResponse.json()
    check(listings.total === 1 && listings.items?.length === 1 && listings.items[0].id === fixture.comparisonListingIds.bay,
      'ACTUAL_EXACT_LISTING_FILTER_REQUIRED')
    check(new URL(page.url()).searchParams.getAll('location').join(',') === 'Fixture Bay', 'LISTING_FILTER_URL_REQUIRED')

    stage = 'public_compare_spanish'
    const exported = await page.goto(landing + '/local-guides/compare/?lang=en', { waitUntil: 'domcontentloaded' })
    check(exported?.status() === 200, 'ACTUAL_EXPORTED_COMPARISON_REQUIRED', exported?.status())
    const spanishCatalog = waitResponse(page, '/site/neighborhoods', 'GET', catalogQuery('es'))
    await page.getByRole('link', { name: 'Español', exact: true }).click()
    check((await responseJson(await spanishCatalog, 200)).total === 2, 'LOCALIZED_CATALOG_SCOPE_REQUIRED')
    await page.getByRole('heading', { name: 'Comparar zonas', exact: true, level: 1 }).waitFor()
    await mobileComparison(page, 'es')
    checks.push('spanish_facts_dates_sources_and_unknowns', 'exact_location_listing_filter')

    stage = 'new_draft'
    await adminPage.bringToFront()
    const form = editorialForm(adminPage)
    await labeled(group(form, 'Comparison fact 1'), 'Fact in English', 'textarea').fill(changedFact)
    const saving = waitResponse(adminPage, ridge.detail + '/draft', 'PUT')
    await button(form, 'Save draft').click()
    const saved = await responseJson(await saving, 200)
    check(saved.draft_revision === 2 && saved.published_revision === ridge.published.published_revision,
      'NEW_DRAFT_KEEPS_OLD_REVISION_REQUIRED')
    const stillPublic = await apiResult(publicContext, ridge.endpoint + '?locale=en')
    check(stillPublic.facts[0]?.text === profiles[1].english && !JSON.stringify(stillPublic).includes(changedFact),
      'UNREVIEWED_DRAFT_MUST_STAY_PRIVATE')
    checks.push('private_draft_keeps_published_revision')

    stage = 'withdraw'
    const removed = await action(adminPage, ridge.detail, 'unpublish', 'Unpublish', 'Confirm unpublish')
    check(removed.draft_revision === 2 && removed.published_revision === null, 'EXPLICIT_WITHDRAWAL_REQUIRED')
    await apiResult(publicContext, ridge.endpoint + '?locale=es', 404)

    stage = 'public_withdrawn'
    const remaining = waitResponse(page, '/site/neighborhoods', 'GET', catalogQuery('es'))
    await page.goto(landing + '/local-guides/compare/?lang=es', { waitUntil: 'domcontentloaded' })
    const after = await responseJson(await remaining, 200)
    check(after.total === 1 && after.items[0]?.slug === profiles[0].slug, 'ONE_NEIGHBORHOOD_REMAINS_REQUIRED')
    await page.getByRole('checkbox', { name: profiles[0].es, exact: true }).waitFor()
    check(await page.getByRole('checkbox', { name: profiles[1].es, exact: true }).count() === 0,
      'WITHDRAWN_NEIGHBORHOOD_ABSENT_REQUIRED')
    checks.push('unpublish_removes_only_withdrawn_neighborhood', 'actual_loopback_only_without_outbound')
  } finally {
    await browser.close()
  }
  console.log('NEIGHBORHOOD_BROWSER ' + JSON.stringify({ status: 'PASS', checks }))
}

main().catch(error => {
  const code = error instanceof CheckFailure && /^[A-Z][A-Z0-9_]{0,52}$/.test(error.message)
    ? error.message : error.name === 'TimeoutError' ? 'TIMED_OUT' : 'BROWSER_STEP_FAILED'
  const status = Number.isInteger(error.status) && error.status >= 100 && error.status <= 599 ? ` status=${error.status}` : ''
  console.log(`NEIGHBORHOOD_BROWSER_FAILURE ${stage} ${code}${status}`)
  process.exitCode = 1
})
