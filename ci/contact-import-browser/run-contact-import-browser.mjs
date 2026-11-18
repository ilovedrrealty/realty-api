import { lstat, readFile, readdir } from 'node:fs/promises'
import { chromium } from 'playwright-core'

const api = 'http://127.0.0.1:8918', dashboard = 'http://127.0.0.1:4175'
const prefix = '/admin/trade-contacts', fixturePath = '/browser-fixtures/fixtures.json'
const checks = ['normal_admin_navigation_and_private_choices', 'legacy_json_review_cancel_without_changes',
  'csv_column_mapping_and_field_errors', 'review_back_cancel_and_explicit_csv_confirmation',
  'repeated_csv_import_retains_identical_source_rows', 'real_xlsx_visible_worksheet_and_text_values',
  'composed_area_source_channel_filters', 'actual_role_boundaries_and_bilingual_mobile_geometry']
const contexts = [], geometries = []
let browser, stage = 'fixture', timer, pageErrors = 0, externalWrites = 0, trackingRequests = 0, successfulSaves = 0
class Failure extends Error {}
function check(value, code) { if (!value) throw new Failure(code) }
function advance(value) {
  stage = value; clearTimeout(timer)
  console.log('CONTACT_IMPORT_BROWSER_STAGE ' + value)
  timer = setTimeout(() => { console.log('CONTACT_IMPORT_BROWSER_FAILURE ' + stage + ' STAGE_DEADLINE'); process.exit(1) }, 60000)
}
async function bounded(promise) {
  let timeout
  try { return await Promise.race([promise, new Promise((_, reject) => { timeout = setTimeout(() => reject(new Failure('BODY_DEADLINE')), 15000) })]) }
  finally { clearTimeout(timeout) }
}
function noStore(response) { check((response.headers()['cache-control'] || '').split(',').some(value => value.trim() === 'no-store'), 'PRIVATE_NO_STORE_REQUIRED') }
const json = response => bounded(response.json())
const button = (surface, name) => surface.getByRole('button', { name, exact: true })
const combo = (surface, label) => surface.getByRole('combobox', { name: new RegExp('^' + label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')) })
const match = (response, path, method) => new URL(response.url()).origin === api && new URL(response.url()).pathname === path && response.request().method() === method
async function observe(page, path, action, status = 200, method = 'POST', accept = () => true) {
  const [response] = await Promise.all([page.waitForResponse(value => match(value, path, method) && accept(value)), action()])
  check(response.status() === status, 'ACTUAL_API_STATUS_REQUIRED'); noStore(response)
  return response
}
async function request(context, path, data, headers = {}) {
  return context.request.fetch(api + path, { method: data === undefined ? 'GET' : 'POST', data,
    headers: { Origin: dashboard, ...headers }, maxRedirects: 0, timeout: 15000 })
}
async function session(locale, account, role) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, serviceWorkers: 'block' })
  contexts.push(context); context.setDefaultTimeout(15000); context.setDefaultNavigationTimeout(20000)
  context.on('page', page => page.on('pageerror', () => { pageErrors += 1 }))
  context.on('response', response => {
    if (match(response, prefix + '/import', 'POST') && response.status() === 200 && response.request().postDataJSON()?.dry_run === false) successfulSaves += 1
  })
  await context.route('**/*', route => {
    const value = route.request(), url = new URL(value.url())
    if (/google-analytics|googletagmanager|doubleclick|facebook\.com\/tr/.test(url.href)) trackingRequests += 1
    if (![api, dashboard].includes(url.origin)) {
      if (!['GET', 'HEAD', 'OPTIONS'].includes(value.method())) externalWrites += 1
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
    const response = await request(context, '/auth/login', account)
    check(response.status() === 200 && (await context.cookies(api)).some(cookie => cookie.name === 'ildr_token' && cookie.httpOnly), 'REAL_COOKIE_LOGIN_REQUIRED')
    await response.dispose()
    const identity = await request(context, '/auth/me')
    check(identity.status() === 200 && (await json(identity)).role === role, 'ACTUAL_ROLE_REQUIRED'); await identity.dispose()
  }
  return context
}
async function geometry(page, label, dialog) {
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 })
    const dimensions = await page.evaluate(() => ({ width: innerWidth, document: document.documentElement.scrollWidth }))
    check(dimensions.width === width && dimensions.document <= width + 1, 'WHOLE_PAGE_OVERFLOW')
    if (dialog) {
      const value = await dialog.evaluate(node => ({ left: node.getBoundingClientRect().left, right: node.getBoundingClientRect().right,
        scroll: node.scrollWidth, client: node.clientWidth }))
      check(value.left >= -1 && value.right <= width + 1 && value.scroll <= value.client + 1, 'DIALOG_OVERFLOW')
    }
    geometries.push({ label, width })
    console.log('CONTACT_IMPORT_BROWSER_GEOMETRY ' + JSON.stringify({ label, width, document: dimensions.document }))
  }
  await page.setViewportSize({ width: 390, height: 844 })
}
async function count(context, term) {
  const response = await request(context, prefix + '?q=' + encodeURIComponent(term)); noStore(response)
  check(response.status() === 200, 'REAL_DIRECTORY_READ_REQUIRED')
  const value = await json(response); await response.dispose(); return value.total
}
async function openContacts(context, copy) {
  const page = await context.newPage()
  await page.goto(dashboard + '/?workspace=admin', { waitUntil: 'domcontentloaded' })
  await page.locator('button:has(svg.lucide-menu)').click()
  const response = await observe(page, prefix, () => button(page.locator('aside'), copy.nav['trade-contacts']).click(), 200, 'GET')
  check((await json(response)).total >= 82, 'PROTECTED_DIRECTORY_PRESENT')
  await button(page, copy.admin.import).waitFor()
  return page
}
async function openImport(page, copy) {
  await button(page, copy.import).click()
  const dialog = page.getByRole('dialog', { name: copy.import, exact: true }); await dialog.waitFor()
  return dialog
}
const mappingGroup = (dialog, copy) => dialog.getByRole('group', { name: copy.file_mapping.match_columns, exact: true })
async function shared(dialog, copy, facts, status) {
  const group = dialog.getByRole('group', { name: copy.file_mapping.shared_fields, exact: true })
  for (const [field, value] of Object.entries({ category: facts.category, province: facts.province, locality: facts.locality,
    source_authority: facts.sourceAuthority, source_url: facts.sourceUrl, verified_on: facts.sourceDate })) {
    await group.getByLabel(copy.fields[field], { exact: true }).fill(value)
  }
  await combo(group, copy.fields.verification_status).selectOption(status)
}
async function analyze(page, dialog, path, copy) {
  const response = await observe(page, prefix + '/preview-file', () => dialog.locator('input[type=file]').setInputFiles(path))
  const table = await json(response)
  await mappingGroup(dialog, copy).waitFor()
  return table
}
async function mapped(page, dialog, copy) {
  const response = await observe(page, prefix + '/preview-mapping', () => button(dialog, copy.preview).click())
  return { response, report: await json(response) }
}
async function reviewed(dialog, copy) {
  await dialog.getByRole('heading', { name: copy.preview, exact: true }).waitFor()
  await button(dialog, copy.confirm_import).click({ trial: true })
}
async function save(page, dialog, copy, expected) {
  const response = await observe(page, prefix + '/import', () => button(dialog, copy.confirm_import).click())
  const body = response.request().postDataJSON(), result = await json(response)
  check(body.dry_run === false && body.preserve_source_details === true && body.entries.length === 1, 'EXPLICIT_PRESERVING_CONFIRMATION_REQUIRED')
  check(result.inserted === expected && result.updated === 0 && result.unchanged === 1 - expected, 'EXACT_IMPORT_RESULT_REQUIRED')
  await dialog.waitFor({ state: 'hidden' }); return body.entries
}
async function filtered(page, context, copy, facts, status, channel, expected) {
  const form = page.locator('form').filter({ has: page.getByRole('button', { name: copy.search, exact: true }) })
  for (const [field, value] of Object.entries({ q: facts.searchTerm, category: facts.category, province: facts.province, locality: facts.locality })) {
    await form.getByLabel(copy.filters[field], { exact: true }).fill(value)
  }
  await combo(form, copy.filters.verification_status).selectOption(status)
  await combo(form, copy.filters.contact_channel).selectOption(channel)
  const response = await observe(page, prefix, () => button(form, copy.search).click(), 200, 'GET', response => {
    const params = new URL(response.url()).searchParams
    return params.get('q') === facts.searchTerm && params.get('locality') === facts.locality
      && (params.get('verification_status') || '') === status && (params.get('contact_channel') || '') === channel
  })
  const value = await json(response), params = new URL(response.url()).searchParams
  check(value.total === expected && params.get('q') === facts.searchTerm && params.get('locality') === facts.locality,
    'COMPOSED_FILTER_COUNTS_REQUIRED')
  await page.getByText((expected === 1 ? copy.results_one : copy.results_other).replace('{{count}}', String(expected)), { exact: true }).waitFor()
  if (expected === 0) await page.getByText(copy.empty, { exact: true }).waitFor()
  const control = await request(context, prefix + '?' + params.toString()); noStore(control)
  check((await json(control)).total === expected, 'FILTER_UI_API_PARITY_REQUIRED'); await control.dispose()
}

try {
  check(process.platform === 'linux' && process.env.GITHUB_ACTIONS === 'true' && process.env.GITHUB_REPOSITORY === 'ilovedrrealty/realty-api'
    && (await readdir('/sys/class/net')).join(',') === 'lo', 'HOSTED_LOOPBACK_REQUIRED')
  const info = await lstat(fixturePath)
  check(info.isFile() && (info.mode & 0o777) === 0o600 && info.size <= 32768, 'PRIVATE_FIXTURE_REQUIRED')
  const fixture = JSON.parse(await readFile(fixturePath, 'utf8'))
  check(fixture.scope === 'contact-import-filter-only' && Object.keys(fixture.accounts).sort().join(',') === 'admin,buyer,owner,realtor', 'OWNED_FIXTURE_REQUIRED')
  check(Object.values(fixture.accounts).every(account => /^[a-z0-9._+-]+@example\.com$/.test(account.email) && /^[A-Za-z0-9_-]{32}$/.test(account.password)), 'SYNTHETIC_ACCOUNTS_REQUIRED')
  check(Object.values(fixture.files).every(path => path.startsWith('/browser-fixtures/') && !path.split('/').includes('..')), 'OWNED_INPUT_FILES_REQUIRED')
  browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] })
  const copies = Object.fromEntries(await Promise.all(['en', 'es'].map(async language => [language, JSON.parse(await readFile(`/browser-run/${language}.json`, 'utf8'))])))
  const en = await session('en', fixture.accounts.admin, 'admin'), copy = copies.en.admin, facts = fixture.facts
  advance('admin_normal_navigation')
  const page = await openContacts(en, copies.en)
  await geometry(page, 'en_page')
  const options = await request(en, prefix + '/options'); noStore(options)
  check(options.status() === 200 && (await json(options)).categories.includes('Protected owner lender'), 'PRIVATE_CHOICES_REQUIRED'); await options.dispose()
  advance('legacy_json_preview_cancel')
  let dialog = await openImport(page, copy)
  await dialog.locator('input[type=file]').setInputFiles(fixture.files.json)
  const legacy = await observe(page, prefix + '/import', () => button(dialog, copy.preview).click())
  const legacyBody = legacy.request().postDataJSON()
  check(legacyBody.dry_run === true && !('preserve_source_details' in legacyBody) && (await json(legacy)).unchanged === 1, 'LEGACY_JSON_CONTRACT_REQUIRED')
  await reviewed(dialog, copy); await button(dialog, copy.close).click(); await dialog.waitFor({ state: 'hidden' })
  check(successfulSaves === 0 && await count(en, facts.searchTerm) === 0, 'JSON_CANCEL_ZERO_WRITES_REQUIRED')
  advance('csv_mapping_validation_and_cancel')
  dialog = await openImport(page, copy)
  const csvTable = await analyze(page, dialog, fixture.files.csv, copy)
  const columns = mappingGroup(dialog, copy)
  await combo(columns, copy.fields.name).selectOption('0')
  await shared(dialog, copy, facts, 'public_source')
  await combo(columns, copy.fields.email).selectOption('1')
  const invalid = await mapped(page, dialog, copy)
  check(invalid.report.issue_count === 1 && invalid.report.issues[0].field === 'email' && invalid.report.entries.length === 0,
    'REAL_ROW_VALIDATION_REQUIRED')
  await dialog.getByRole('alert').waitFor(); await geometry(page, 'en_dialog', dialog)
  check(await count(en, facts.searchTerm) === 0, 'INVALID_MAPPING_ZERO_WRITES_REQUIRED')
  await combo(columns, copy.fields.email).selectOption('')
  const candidate = await mapped(page, dialog, copy)
  const candidateBody = candidate.response.request().postDataJSON()
  check(candidate.report.result.inserted === 1 && candidate.report.preserve_source_details === true, 'READ_ONLY_INSERT_PREVIEW_REQUIRED')
  await reviewed(dialog, copy); await button(dialog, copy.back).click(); await mappingGroup(dialog, copy).waitFor()
  check(await combo(columns, copy.fields.name).inputValue() === '0', 'BACK_RETAINS_MAPPING_REQUIRED')
  await button(dialog, copy.close).click(); await dialog.waitFor({ state: 'hidden' })
  check(successfulSaves === 0 && await count(en, facts.searchTerm) === 0, 'REVIEW_CANCEL_ZERO_WRITES_REQUIRED')
  advance('csv_explicit_save_and_replay')
  dialog = await openImport(page, copy); await analyze(page, dialog, fixture.files.csv, copy)
  await combo(mappingGroup(dialog, copy), copy.fields.name).selectOption('0')
  await shared(dialog, copy, facts, 'public_source'); await mapped(page, dialog, copy); await reviewed(dialog, copy)
  const csvEntries = await save(page, dialog, copy, 1)
  dialog = await openImport(page, copy); await analyze(page, dialog, fixture.files.csv, copy)
  await combo(mappingGroup(dialog, copy), copy.fields.name).selectOption('0')
  await shared(dialog, copy, facts, 'public_source')
  const repeat = await mapped(page, dialog, copy); check(repeat.report.result.unchanged === 1, 'REPLAY_PREVIEW_UNCHANGED_REQUIRED')
  await reviewed(dialog, copy); await save(page, dialog, copy, 0); await page.close()
  advance('spanish_real_xlsx_selection_and_save')
  const es = await session('es', fixture.accounts.admin, 'admin'), spanish = copies.es.admin
  const spanishPage = await openContacts(es, copies.es); await geometry(spanishPage, 'es_page')
  dialog = await openImport(spanishPage, spanish)
  const initialSheet = await analyze(spanishPage, dialog, fixture.files.xlsx, spanish)
  check(initialSheet.sheet === 'Instructions' && !JSON.stringify(initialSheet).includes(facts.hiddenSentinel)
    && !initialSheet.sheets.includes('Hidden contacts'), 'VISIBLE_WORKSHEETS_ONLY_REQUIRED')
  const sheetResponse = await observe(spanishPage, prefix + '/preview-file', () => combo(dialog, spanish.file_mapping.worksheet).selectOption(facts.contactSheet))
  const actualSheet = await json(sheetResponse)
  check(actualSheet.sheet === facts.contactSheet && actualSheet.rows[0].values[1] === '+12025550124'
    && actualSheet.rows[0].values[4] === '00123', 'XLSX_TEXT_AND_FORMATTED_ZEROES_REQUIRED')
  await mappingGroup(dialog, spanish).waitFor(); await shared(dialog, spanish, facts, 'owner_source')
  await geometry(spanishPage, 'es_dialog', dialog)
  const xlsx = await mapped(spanishPage, dialog, spanish)
  check(xlsx.report.entries[0].postal_code === '00123' && xlsx.report.result.inserted === 1, 'ACTUAL_XLSX_PREVIEW_REQUIRED')
  await reviewed(dialog, spanish); await save(spanishPage, dialog, spanish, 1)
  advance('composed_filters')
  for (const [status, channel, expected] of [['', '', 2], ['public_source', '', 1], ['owner_source', '', 1], ['', 'phone', 2], ['', 'email', 1], ['', 'website', 1], ['', 'no_contact', 0]]) {
    await filtered(spanishPage, es, spanish, facts, status, channel, expected)
  }
  const cleared = await observe(spanishPage, prefix, () => button(spanishPage, spanish.clear_filters).click(), 200, 'GET',
    response => !new URL(response.url()).searchParams.has('q'))
  check((await json(cleared)).total === 84 && await count(es, '') === 84, 'CLEAR_FILTERS_RESTORES_DIRECTORY_REQUIRED')
  await spanishPage.getByText(spanish.results_other.replace('{{count}}', '84'), { exact: true }).waitFor(); await spanishPage.close()
  advance('real_nonadmin_and_guest_boundaries')
  const csvBytes = await readFile(fixture.files.csv)
  for (const role of ['owner', 'realtor', 'buyer', 'guest']) {
    const context = await session('en', fixture.accounts[role], role)
    const expected = role === 'guest' ? [401, 403] : [403]
    for (const path of [prefix, prefix + '/options']) {
      const denied = await request(context, path); noStore(denied); check(expected.includes(denied.status()), 'ACTUAL_PRIVATE_READ_DENIAL_REQUIRED'); await denied.dispose()
    }
    const upload = await context.request.post(api + prefix + '/preview-file', { headers: { Origin: dashboard },
      multipart: { file: { name: 'contacts.csv', mimeType: 'text/csv', buffer: csvBytes } }, timeout: 15000 })
    noStore(upload); check(expected.includes(upload.status()), 'ACTUAL_PRIVATE_FILE_DENIAL_REQUIRED'); await upload.dispose()
    for (const [path, data] of [[prefix + '/preview-mapping', candidateBody],
      [prefix + '/import', { dry_run: false, entries: csvEntries, preserve_source_details: true }]]) {
      const denied = await request(context, path, data); noStore(denied); check(expected.includes(denied.status()), 'ACTUAL_PRIVATE_WRITE_DENIAL_REQUIRED'); await denied.dispose()
    }
  }
  check(pageErrors === 0 && externalWrites === 0 && trackingRequests === 0 && successfulSaves === 3 && geometries.length === 8,
    'BROWSER_PRIVACY_OR_COUNTS_REQUIRED')
  for (const context of contexts) await context.close()
  await browser.close(); clearTimeout(timer)
  console.log('CONTACT_IMPORT_BROWSER_RESULT ' + JSON.stringify({ status: 'PASS', checks, geometry_checks: 8,
    locales: ['en', 'es'], successful_final_imports: successfulSaves, page_errors: 0, external_writes: 0, tracking_requests: 0 }))
} catch (error) {
  clearTimeout(timer)
  const code = error instanceof Failure ? error.message : error?.name === 'TimeoutError' ? 'LOCATOR_OR_EVENT_DEADLINE' : 'BROWSER_OPERATION_FAILED'
  console.log('CONTACT_IMPORT_BROWSER_FAILURE ' + stage + ' ' + code)
  await browser?.close().catch(() => {}); process.exitCode = 1
}
