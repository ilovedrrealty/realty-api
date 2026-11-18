import { lstat, readFile, readdir } from 'node:fs/promises'
import { chromium } from 'playwright-core'

const api = 'http://127.0.0.1:8918', dashboard = 'http://127.0.0.1:4175'
const contacts = '/admin/trade-contacts', professionals = '/professionals'
const checks = ['normal_admin_geography_and_full_scope_counts', 'exact_specialty_and_service_area_filters',
  'area_selection_unmapped_clear_and_pagination', 'existing_csv_preview_cancel_without_import',
  'member_visibility_overlapping_areas_and_profile', 'member_multiselect_and_unmapped_records',
  'stale_reads_and_auth_loss_clear_private_state', 'bilingual_mobile_desktop_and_actual_role_boundaries']
const contexts = [], geometries = []
let browser, timer, stage = 'fixture', pageErrors = 0, externalWrites = 0, trackingRequests = 0, finalImports = 0
class Failure extends Error {}
const check = (value, code) => { if (!value) throw new Failure(code) }
function advance(value) {
  stage = value; clearTimeout(timer); console.log('DIRECTORY_MAP_BROWSER_STAGE ' + value)
  timer = setTimeout(() => { console.log('DIRECTORY_MAP_BROWSER_FAILURE ' + stage + ' STAGE_DEADLINE'); process.exit(1) }, 60000)
}
async function bounded(promise) {
  let deadline
  try { return await Promise.race([promise, new Promise((_, reject) => { deadline = setTimeout(() => reject(new Failure('BODY_DEADLINE')), 15000) })]) }
  finally { clearTimeout(deadline) }
}
const json = response => bounded(response.json())
const button = (surface, name) => surface.getByRole('button', { name, exact: true })
const named = (copy, key, count) => copy.counts[key].replace('{{count}}', String(count))
const mapRegion = (page, copy) => page.getByRole('region', { name: copy.title, exact: true })
const match = (response, path) => new URL(response.url()).origin === api && new URL(response.url()).pathname === path && response.request().method() === 'GET'
function noStore(response) { check((response.headers()['cache-control'] || '').split(',').some(value => value.trim() === 'no-store'), 'PRIVATE_NO_STORE_REQUIRED') }
async function request(context, path, body) {
  return context.request.fetch(api + path, { method: body === undefined ? 'GET' : 'POST', data: body,
    headers: { Origin: dashboard }, maxRedirects: 0, timeout: 15000 })
}
async function session(locale, account, role) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, serviceWorkers: 'block' })
  contexts.push(context); context.setDefaultTimeout(15000); context.setDefaultNavigationTimeout(20000)
  context.on('page', page => page.on('pageerror', () => { pageErrors++ }))
  context.on('response', response => {
    if (new URL(response.url()).origin === api && new URL(response.url()).pathname === contacts + '/import'
      && response.request().method() === 'POST' && response.request().postDataJSON()?.dry_run === false) finalImports++
  })
  await context.route('**/*', route => {
    const req = route.request(), url = new URL(req.url())
    if (/google-analytics|googletagmanager|doubleclick|facebook\.com\/tr/.test(url.href)) trackingRequests++
    if (![api, dashboard].includes(url.origin)) {
      if (!['GET', 'HEAD', 'OPTIONS'].includes(req.method())) externalWrites++
      return route.abort('blockedbyclient')
    }
    return route.continue()
  })
  await context.addInitScript(({ locale, origin }) => {
    if (location.origin !== origin) return
    localStorage.setItem('ildr_lang', locale)
    localStorage.setItem('ildr_measurement_consent_v1', JSON.stringify({ choice: 'denied', savedAt: Date.now() }))
  }, { locale, origin: dashboard })
  if (account) {
    const login = await request(context, '/auth/login', account)
    check(login.status() === 200 && (await context.cookies(api)).some(cookie => cookie.name === 'ildr_token' && cookie.httpOnly), 'REAL_COOKIE_LOGIN_REQUIRED')
    await login.dispose()
    const identity = await request(context, '/auth/me')
    check(identity.status() === 200 && (await json(identity)).role === role, 'ACTUAL_ROLE_REQUIRED'); await identity.dispose()
  }
  return context
}
async function applied(page, scope, action, predicate = () => true) {
  const [list, map] = await Promise.all([
    page.waitForResponse(response => match(response, scope) && predicate(new URL(response.url()).searchParams, false)),
    page.waitForResponse(response => match(response, scope + '/map') && predicate(new URL(response.url()).searchParams, true)), action(),
  ])
  check(list.status() === 200 && map.status() === 200, 'ACTUAL_SCOPED_READ_REQUIRED'); noStore(list); noStore(map)
  const [pageValue, mapValue] = await Promise.all([json(list), json(map)])
  check(pageValue.total === mapValue.total && mapValue.complete === true
    && mapValue.mapped_total + mapValue.unmapped_total === mapValue.total, 'FULL_SCOPE_LIST_MAP_PARITY_REQUIRED')
  check(Object.keys(mapValue).sort().join(',') === 'areas,catalog,complete,mapped_total,total,unmapped_total', 'AGGREGATE_ONLY_REQUIRED')
  for (const area of mapValue.areas) check(area.precision === 'area_reference' && !('id' in area) && !('name' in area)
    && !('address' in area) && !('email' in area) && !('phone' in area), 'NO_CONTACTS_OR_EXACT_OFFICES_IN_MAP')
  return { list: pageValue, map: mapValue }
}
async function shown(page, copy, expected) {
  const region = mapRegion(page, copy); await region.waitFor()
  for (const key of ['all', 'mapped', 'unmapped']) await button(region, named(copy, key,
    key === 'all' ? expected.total : key === 'mapped' ? expected.mapped_total : expected.unmapped_total)).waitFor()
  check(await region.getByRole('img', { name: copy.geography, exact: true }).locator('path').count() === 177, 'ACTUAL_COUNTRY_GEOMETRY_REQUIRED')
  return region
}
async function openDirectory(context, copies, kind) {
  const page = await context.newPage(), admin = kind === 'admin', scope = admin ? contacts : professionals
  await page.goto(dashboard + '/?workspace=' + (admin ? 'admin' : 'buyer'), { waitUntil: 'domcontentloaded' })
  await page.locator('button:has(svg.lucide-menu)').click()
  const result = await applied(page, scope, () => button(page.locator('aside'), copies.nav[admin ? 'trade-contacts' : 'professional-directory']).click())
  await shown(page, copies.professionals.directory_map, result.map)
  return { page, ...result }
}
async function geometry(page, label) {
  for (const width of [390, 320, 1366]) {
    await page.setViewportSize({ width, height: 844 })
    const value = await page.evaluate(() => ({ width: innerWidth, document: document.documentElement.scrollWidth }))
    check(value.width === width && value.document <= width + 1, 'WHOLE_PAGE_OVERFLOW')
    geometries.push({ label, width }); console.log('DIRECTORY_MAP_BROWSER_GEOMETRY ' + JSON.stringify({ label, ...value }))
  }
  await page.setViewportSize({ width: 390, height: 844 })
}
async function holdMap(page, scope, predicate) {
  let ready, release, finished, used = false
  const readyPromise = new Promise(resolve => { ready = resolve })
  const releasePromise = new Promise(resolve => { release = resolve })
  const finishedPromise = new Promise(resolve => { finished = resolve })
  const handler = async route => {
    if (used || !predicate(new URL(route.request().url()).searchParams)) return route.continue()
    used = true
    const actual = await route.fetch()
    check(actual.status() === 200, 'HELD_RESPONSE_MUST_BE_REAL_SUCCESS')
    ready(); await releasePromise
    try { await route.fulfill({ response: actual }) } catch { /* The superseded real request may already be aborted. */ }
    finally { finished() }
  }
  await page.route(api + scope + '/map**', handler)
  return { ready: () => bounded(readyPromise), release: async () => { release(); await bounded(finishedPromise); await page.unroute(api + scope + '/map**', handler) } }
}

try {
  check(process.env.GITHUB_ACTIONS === 'true' && process.env.GITHUB_REPOSITORY === 'ilovedrrealty/realty-api'
    && process.env.ILDR_BROWSER_FIXTURES === '/browser-fixtures/fixtures.json', 'HOSTED_FIXTURE_REQUIRED')
  for (const name of ['DATABASE_URL', 'RESEND_API_KEY', 'SUPABASE_SERVICE_KEY', 'GHL_API_KEY']) check(!process.env[name], 'NO_LIVE_CREDENTIALS_REQUIRED')
  check((await readdir('/sys/class/net')).join(',') === 'lo', 'LOOPBACK_NETWORK_ONLY_REQUIRED')
  const info = await lstat(process.env.ILDR_BROWSER_FIXTURES)
  check(info.isFile() && !info.isSymbolicLink() && (info.mode & 0o777) === 0o600 && info.size < 40000, 'OWNED_PRIVATE_FIXTURE_REQUIRED')
  const fixture = JSON.parse(await readFile(process.env.ILDR_BROWSER_FIXTURES, 'utf8'))
  check(fixture.scope === 'directory-map-only', 'EXACT_SCOPE_REQUIRED')
  const copies = Object.fromEntries(await Promise.all(['en', 'es'].map(async locale => [locale, JSON.parse(await readFile('/browser-run/' + locale + '.json', 'utf8'))])))
  const facts = fixture.facts
  browser = await chromium.launch({ headless: true })
  advance('normal_admin_geography')
  const admin = await session('en', fixture.accounts.admin, 'admin'), en = copies.en, mapCopy = en.professionals.directory_map
  const initial = await openDirectory(admin, en, 'admin'), page = initial.page
  check(initial.list.items.length === 50 && initial.map.total === 88 && initial.map.mapped_total === 3 && initial.map.unmapped_total === 85, 'FULL_SCOPE_BEYOND_FIRST_PAGE_REQUIRED')
  check(initial.map.areas.map(area => area.area_key).sort().join(',') === [facts.sosuaKey, facts.cabareteKey, facts.provinceKey].sort().join(','), 'LOCALITY_AND_PROVINCE_FALLBACK_REFERENCES_REQUIRED')
  await geometry(page, 'en_admin')
  const region = mapRegion(page, mapCopy)
  await button(region, mapCopy.views.caribbean).click(); await button(region, mapCopy.views.dominican_republic).click()
  advance('specialty_service_area_and_stale_filters')
  const filterForm = page.locator('form').filter({ has: page.getByRole('button', { name: en.admin.search, exact: true }) })
  const field = key => filterForm.getByLabel(en.admin.filters[key], { exact: true })
  check(await field('subtype').getAttribute('maxlength') === '160' && await field('service_area').getAttribute('maxlength') === '320', 'FULL_API_FILTER_LENGTH_REQUIRED')
  const held = await holdMap(page, contacts, query => query.get('q') === facts.searchTerm && !query.get('subtype'))
  await field('q').fill(facts.searchTerm)
  await button(filterForm, en.admin.search).click(); await held.ready()
  await field('subtype').fill(facts.longSubtype); await field('service_area').fill(facts.serviceArea)
  const exact = await applied(page, contacts, () => button(filterForm, en.admin.search).click(), query => query.get('subtype') === facts.longSubtype && query.get('service_area') === facts.serviceArea)
  check(exact.map.total === 1 && exact.map.areas[0].area_key === facts.sosuaKey, 'EXACT_SPECIALTY_LITERAL_SERVICE_AREA_REQUIRED')
  await shown(page, mapCopy, exact.map); await held.release(); await shown(page, mapCopy, exact.map)
  advance('area_markers_unmapped_clear_and_pagination')
  const cleared = await applied(page, contacts, () => button(page, en.admin.clear_filters).click(), query => !query.has('q') && !query.has('map_area'))
  await shown(page, mapCopy, cleared.map)
  const next = await applied(page, contacts, () => button(page, en.admin.next).click(), (query, isMap) => isMap || query.get('offset') === '50')
  check(next.map.total === 88 && next.list.items.length === 38, 'PAGED_LIST_FULL_MAP_REQUIRED'); await shown(page, mapCopy, next.map)
  const marker = region.getByRole('button', { name: mapCopy.area_button.replace('{{area}}', 'Sosúa').replace('{{count}}', '1'), exact: true })
  await marker.focus()
  const selected = await applied(page, contacts, () => marker.press('Enter'), (query, isMap) => query.get('map_area') === facts.sosuaKey && (isMap || query.get('offset') === '0'))
  check(selected.map.total === 1 && selected.map.areas.length === 1 && selected.list.items[0].subtype === facts.longSubtype, 'KEYBOARD_AREA_SHARED_PREDICATE_REQUIRED')
  await shown(page, mapCopy, selected.map)
  const unmapped = await applied(page, contacts, () => button(region, named(mapCopy, 'unmapped', 0)).click(), query => query.get('location_status') === 'unmapped' && !query.has('map_area'))
  check(unmapped.map.total === 85 && unmapped.map.areas.length === 0 && unmapped.map.mapped_total === 0, 'UNMAPPED_NOT_DROPPED_REQUIRED')
  await shown(page, mapCopy, unmapped.map)
  await applied(page, contacts, () => button(region, mapCopy.clear_all).click(), query => !query.has('location_status') && !query.has('q'))
  await shown(page, mapCopy, initial.map)
  advance('csv_preview_cancel')
  await button(page, en.admin.import).click()
  const dialog = page.getByRole('dialog', { name: en.admin.import, exact: true }); await dialog.waitFor()
  const [analysis] = await Promise.all([page.waitForResponse(response => new URL(response.url()).pathname === contacts + '/preview-file' && response.request().method() === 'POST'), dialog.locator('input[type=file]').setInputFiles(fixture.files.csv)])
  check(analysis.status() === 200, 'ACTUAL_CSV_PREVIEW_REQUIRED'); noStore(analysis)
  await dialog.getByRole('group', { name: en.admin.file_mapping.match_columns, exact: true }).waitFor()
  const [preview] = await Promise.all([page.waitForResponse(response => new URL(response.url()).pathname === contacts + '/preview-mapping' && response.request().method() === 'POST'), button(dialog, en.admin.preview).click()])
  check(preview.status() === 200 && (await json(preview)).result.inserted === 1, 'READ_ONLY_REVIEW_REQUIRED'); noStore(preview)
  await button(dialog, en.admin.confirm_import).waitFor(); await button(dialog, en.admin.close).click(); await dialog.waitFor({ state: 'hidden' })
  check(finalImports === 0, 'CANCEL_MUST_NOT_IMPORT')
  advance('member_scope_overlap_profile_and_filters')
  const member = await session('en', fixture.accounts.buyer, 'buyer'), memberCopy = en.professionals
  const memberInitial = await openDirectory(member, en, 'member'), memberPage = memberInitial.page
  check(memberInitial.map.total === 3 && memberInitial.map.mapped_total === 2 && memberInitial.map.unmapped_total === 1
    && memberInitial.map.areas.reduce((sum, area) => sum + area.record_count, 0) === 3, 'DISTINCT_MEMBER_SCOPE_AND_OVERLAP_REQUIRED')
  check(memberInitial.list.items.every(item => facts.professionalNames.includes(item.business_name)) && !JSON.stringify(memberInitial.map).includes('Protected owner'), 'NO_HIDDEN_DRAFT_OR_OWNER_CONTACT_LEAK')
  await geometry(memberPage, 'en_member')
  const memberRegion = mapRegion(memberPage, mapCopy)
  const selectedMember = await applied(memberPage, professionals, () => button(memberRegion,
    mapCopy.area_button.replace('{{area}}', 'Sosúa').replace('{{count}}', '2')).click(), (query, isMap) => query.get('map_area') === facts.sosuaKey && (isMap || query.get('offset') === '0'))
  check(selectedMember.map.total === 2 && selectedMember.map.areas.length === 1, 'MEMBER_MARKER_FILTER_REQUIRED'); await shown(memberPage, mapCopy, selectedMember.map)
  const card = memberPage.locator('article').filter({ has: memberPage.getByRole('heading', { name: facts.professionalNames[0], exact: true }) })
  const [detail] = await Promise.all([memberPage.waitForResponse(response => match(response, professionals + '/' + facts.profileId)), button(card, `${memberCopy.directory_search.view}: ${facts.professionalNames[0]}`).click()])
  check(detail.status() === 200, 'ACTUAL_VISIBLE_PROFILE_REQUIRED'); noStore(detail)
  const detailDialog = memberPage.getByRole('dialog'); await detailDialog.waitFor(); await button(detailDialog, memberCopy.directory_search.close).click()
  await applied(memberPage, professionals, () => button(memberRegion, mapCopy.clear_all).click(), query => !query.has('map_area'))
  const memberForm = memberPage.locator('form')
  const optionsResponse = await request(member, professionals + '/options'), options = await json(optionsResponse); await optionsResponse.dispose()
  await memberForm.getByLabel(memberCopy.directory_search.search, { exact: true }).fill(facts.professionalSearch)
  const categories = memberForm.locator('details').filter({ has: memberPage.locator('summary').filter({ hasText: memberCopy.categories }) })
  await categories.locator('summary').click()
  for (const id of ['pool_service', 'plumbing']) await categories.getByRole('checkbox', { name: options.categories.find(item => item.id === id).label.en, exact: true }).check()
  const areas = memberForm.locator('details').filter({ has: memberPage.locator('summary').filter({ hasText: memberCopy.areas }) })
  await areas.locator('summary').click()
  for (const id of ['sosua', 'cabarete']) await areas.getByRole('checkbox', { name: options.areas.find(item => item.id === id).label.en, exact: true }).check()
  await memberForm.locator('select').selectOption('en')
  const filtered = await applied(memberPage, professionals, () => button(memberForm, memberCopy.directory_search.apply).click(), query => query.get('q') === facts.professionalSearch
    && query.getAll('category').length === 2 && query.getAll('area').length === 2 && query.get('language') === 'en')
  check(filtered.map.total === 3, 'MEMBER_REPEATED_FILTERS_REQUIRED'); await shown(memberPage, mapCopy, filtered.map)
  const memberUnmapped = await applied(memberPage, professionals, () => button(memberRegion, named(mapCopy, 'unmapped', 1)).click(), query => query.get('location_status') === 'unmapped' && !query.has('map_area'))
  check(memberUnmapped.map.total === 1 && memberUnmapped.map.areas.length === 0 && memberUnmapped.list.items[0].business_name === facts.professionalNames[2], 'NATIONWIDE_ONLY_REMAINS_VISIBLE')
  await shown(memberPage, mapCopy, memberUnmapped.map)
  advance('auth_clear_filters')
  await applied(memberPage, professionals, () => button(memberRegion, mapCopy.clear_all).click(), query => !query.get('q') && !query.has('location_status'))
  advance('auth_open_visible_detail')
  const firstCard = memberPage.locator('article').filter({ has: memberPage.getByRole('heading', { name: facts.professionalNames[0], exact: true }) })
  const [openedDetail] = await Promise.all([memberPage.waitForResponse(response => match(response, professionals + '/' + facts.profileId)),
    button(firstCard, `${memberCopy.directory_search.view}: ${facts.professionalNames[0]}`).click()])
  check(openedDetail.status() === 200, 'DETAIL_PRESENT_BEFORE_AUTH_LOSS_REQUIRED'); noStore(openedDetail)
  await memberPage.getByRole('dialog').waitFor()
  advance('auth_hold_real_map')
  await memberPage.bringToFront()
  const oldMember = await holdMap(memberPage, professionals, () => true)
  await memberPage.evaluate(() => window.dispatchEvent(new Event('focus'))); await oldMember.ready()
  advance('auth_clear_cookies_and_wait_denial')
  await member.clearCookies()
  const [actualDenial] = await Promise.all([memberPage.waitForResponse(response => match(response, professionals + '/map') && [401, 403].includes(response.status())),
    memberPage.evaluate(() => window.dispatchEvent(new Event('focus')))])
  noStore(actualDenial); advance('auth_real_denial_wait_ui_clear'); await memberPage.getByText(memberCopy.directory_search.auth_error, { exact: true }).waitFor()
  advance('auth_release_old_response_and_assert_cleared')
  await oldMember.release()
  check(await mapRegion(memberPage, mapCopy).count() === 0 && await memberPage.getByRole('dialog').count() === 0
    && await memberPage.locator('article').count() === 0, 'AUTH_LOSS_MUST_CLEAR_MAP_LIST_DETAIL')
  advance('bilingual_geometry_and_real_route_boundaries')
  for (const [role, label] of [['admin', 'es_admin'], ['buyer', 'es_member']]) {
    const context = await session('es', fixture.accounts[role], role)
    const opened = await openDirectory(context, copies.es, role === 'admin' ? 'admin' : 'member')
    await geometry(opened.page, label)
  }
  for (const role of ['owner', 'realtor', 'buyer', 'guest']) {
    const context = await session('en', fixture.accounts[role], role)
    for (const path of [contacts, contacts + '/map']) {
      const denied = await request(context, path); noStore(denied)
      check((role === 'guest' ? [401, 403] : [403]).includes(denied.status()), 'ACTUAL_ADMIN_SCOPE_DENIAL_REQUIRED'); await denied.dispose()
    }
    if (role === 'guest') {
      const denied = await request(context, professionals + '/map'); noStore(denied)
      check([401, 403].includes(denied.status()), 'ACTUAL_MEMBER_MAP_AUTH_REQUIRED'); await denied.dispose()
    }
  }
  check(geometries.length === 12 && pageErrors === 0 && externalWrites === 0 && trackingRequests === 0 && finalImports === 0, 'PRIVACY_COUNTS_REQUIRED')
  for (const context of contexts) await context.close()
  await browser.close(); clearTimeout(timer)
  console.log('DIRECTORY_MAP_BROWSER_RESULT ' + JSON.stringify({ status: 'PASS', checks, geometry_checks: 12, locales: ['en', 'es'],
    page_errors: 0, external_writes: 0, tracking_requests: 0, final_imports: 0 }))
} catch (error) {
  clearTimeout(timer)
  console.log('DIRECTORY_MAP_BROWSER_FAILURE ' + stage + ' ' + (error instanceof Failure ? error.message : error?.name === 'TimeoutError' ? 'LOCATOR_OR_EVENT_DEADLINE' : 'BROWSER_OPERATION_FAILED'))
  await browser?.close().catch(() => {}); process.exitCode = 1
}
