import { lstat, readFile, readdir } from 'node:fs/promises'
import { chromium } from 'playwright-core'

const dashboard = 'http://127.0.0.1:4175'
const api = 'http://127.0.0.1:8000'
const fixturePath = '/browser-fixtures/fixtures.json'
const workspacePath = '/research-workspaces'
const title = 'Synthetic purchase cost review'
const legacyTitle = 'Synthetic legacy review'
const taxTool = 'https://www.dgii.gov.do/app/WebApps/Calculadoras/transferenciaInmobiliaria/CalculoTransferencia.aspx'
const idPattern = /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/
const rows = [
  { category: 'legal', currency: 'USD', basis: 'estimate', amount_minor: 12005, label: 'Synthetic legal amount' },
  { category: 'notary_registry', currency: 'DOP', basis: 'quote', amount_minor: 900010, label: 'Synthetic notary quote' },
  { category: 'inspection', currency: 'USD', basis: 'paid', amount_minor: 0, label: 'Synthetic recorded zero' },
]
const english = {
  region: 'Purchase cost worksheet', row: 'Cost', add: 'Add cost', review: 'Review name (required)',
  open: /^Open a saved review/, category: /^Cost category/, currency: /^Currency/, basis: /^Amount basis/,
  amount: 'Amount', label: 'Description (optional)', print: 'Print summary', save: 'Save review', empty: 'No amounts',
  costLink: 'DGII transfer-tax calculator',
}
const spanish = {
  region: 'Hoja de costos de compra', row: 'Costo', open: /^Abrir una revisión guardada/, empty: 'Sin montos',
  category: /^Categoría del costo/, currency: /^Moneda/, basis: /^Tipo de monto/, amount: 'Monto',
  label: 'Descripción (opcional)', save: 'Guardar revisión', costLink: 'Calculadora de transferencia de la DGII',
}
let stage = 'fixture'
const checks = []

class CheckFailure extends Error {}
function check(condition, label) {
  if (!condition) throw new CheckFailure(label)
}

async function readFixture() {
  check(process.platform === 'linux' && process.env.GITHUB_ACTIONS === 'true'
    && process.env.GITHUB_REPOSITORY === 'ilovedrrealty/realty-api'
    && process.env.ILDR_BROWSER_FIXTURES === fixturePath, 'HOSTED_FIXTURE_REQUIRED')
  check((await readdir('/sys/class/net')).join(',') === 'lo', 'LOOPBACK_ONLY_REQUIRED')
  const stat = await lstat(fixturePath)
  check(stat.isFile() && (stat.mode & 0o777) === 0o600 && stat.size <= 2048, 'PRIVATE_FIXTURE_REQUIRED')
  const fixture = JSON.parse(await readFile(fixturePath, 'utf8'))
  check(idPattern.test(fixture.legacy_id), 'SYNTHETIC_LEGACY_ID_REQUIRED')
  for (const key of ['realtor', 'admin']) {
    const account = fixture.accounts?.[key]
    check(account?.email === 'browser-' + key + '@example.com'
      && /^[a-zA-Z0-9_-]{32}$/.test(account.password), 'SYNTHETIC_ACCOUNTS_REQUIRED')
  }
  return fixture
}

function responseFrom(response, path, method = 'GET') {
  const url = new URL(response.url())
  return url.origin === api && url.pathname === path && response.request().method() === method
}

function privateResponse(response) {
  check((response.headers()['cache-control'] ?? '').includes('no-store'), 'PRIVATE_RESPONSE_REQUIRED')
}

function direct(context, path, method = 'GET', data) {
  return context.request.fetch(api + path, {
    method, data, headers: { Origin: dashboard }, timeout: 12_000, maxRedirects: 0,
  })
}

async function session(browser, account, language = 'en') {
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true, serviceWorkers: 'block',
  })
  context.setDefaultTimeout(12_000)
  await context.route('**/*', route => {
    const origin = new URL(route.request().url()).origin
    return origin === api || origin === dashboard ? route.continue() : route.abort('blockedbyclient')
  })
  await context.addInitScript(value => {
    if (!navigator.onLine) Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => true })
    localStorage.setItem('ildr_lang', value)
    window.print = () => { document.documentElement.dataset.rehearsalPrint = 'requested' }
  }, language)
  if (account) {
    const login = await direct(context, '/auth/login', 'POST', account)
    check(login.status() === 200 && (await context.cookies(api)).some(cookie => cookie.name === 'ildr_token' && cookie.httpOnly),
      'REAL_SYNTHETIC_SESSION_REQUIRED')
    await login.dispose()
  }
  return context
}

const named = (area, role, name) => area.getByRole(role, { name, exact: !(name instanceof RegExp) })
const worksheet = (page, copy) => named(page, 'region', copy.region)
const button = (area, name) => named(area, 'button', name)
const reviewChooser = (page, copy) => named(page, 'combobox', copy.open)
const cost = (area, copy, number) => named(area, 'group', copy.row + ' ' + number)
const field = (area, name) => named(area, name instanceof RegExp ? 'combobox' : 'textbox', name)
const amountText = new RegExp('^USD\\s+120\\.05$')
const dopText = new RegExp('^DOP\\s+9[,]000\\.10$')

async function visit(context, path, role) {
  const page = await context.newPage()
  await page.bringToFront()
  const [identity] = await Promise.all([
    page.waitForResponse(response => responseFrom(response, '/auth/me')),
    page.goto(dashboard + path, { waitUntil: 'domcontentloaded' }),
  ])
  check(identity.ok() && (await identity.json()).role === role, 'EXACT_WORKSPACE_ROLE_REQUIRED')
  await page.waitForFunction(() => document.visibilityState === 'visible' && navigator.onLine)
  return page
}

async function openReview(page, copy, id) {
  const [loaded] = await Promise.all([
    page.waitForResponse(response => responseFrom(response, workspacePath + '/' + id)),
    reviewChooser(page, copy).selectOption(id),
  ])
  check(loaded.ok(), 'OWN_REVIEW_OPEN_REQUIRED')
  privateResponse(loaded)
  return loaded.json()
}

function matchesRows(receipt, expected) {
  return Array.isArray(receipt) && receipt.length === expected.length && expected.every((row, index) =>
    Object.keys(receipt[index]).length === Object.keys(row).length
    && Object.entries(row).every(([key, value]) => receipt[index][key] === value))
}

async function enterCost(region, index, row, amount) {
  await button(region, english.add).click()
  const group = cost(region, english, index)
  await field(group, english.category).selectOption(row.category)
  await field(group, english.currency).selectOption(row.currency)
  await field(group, english.basis).selectOption(row.basis)
  await field(group, english.amount).fill(amount)
  await field(group, english.label).fill(row.label)
}

async function mobileLayout(page, region) {
  await region.scrollIntoViewIfNeeded()
  const box = await region.boundingBox()
  const viewport = await page.evaluate(() => ({
    width: document.documentElement.clientWidth, scrollWidth: document.documentElement.scrollWidth,
  }))
  check(box && box.x >= -1 && box.x + box.width <= 391 && viewport.width === 390
    && viewport.scrollWidth <= 391, 'MOBILE_HORIZONTAL_OVERFLOW')
  check(await region.evaluate(element => Array.from(element.querySelectorAll('fieldset input, fieldset select'))
    .every(control => control.getBoundingClientRect().height >= 43)), 'MOBILE_FIELDS_MUST_BE_TOUCH_USABLE')
}

async function confirmTaxLink(region, copy) {
  const link = named(region, 'link', copy.costLink)
  check(await link.getAttribute('href') === taxTool && await link.getAttribute('target') === '_blank'
    && (await link.getAttribute('rel'))?.split(/\s+/).includes('noopener'), 'OFFICIAL_REFERENCE_LINK_REQUIRED')
}

async function verifyPrivate(context, id, status) {
  const reply = await direct(context, workspacePath + '/' + id)
  try {
    privateResponse(reply)
    check(reply.status() === status, 'UNAUTHORIZED_REVIEW_MUST_BE_HIDDEN')
    const body = await reply.text()
    check(!body.includes(title) && rows.every(row => !body.includes(row.label)), 'DENIAL_MUST_NOT_EXPOSE_WORKSHEET')
  } finally {
    await reply.dispose()
  }
}

async function run(browser, fixture) {
  const realtor = await session(browser, fixture.accounts.realtor)
  const newcomer = await session(browser, fixture.accounts.admin)
  const anonymous = await session(browser, null)
  const localized = await session(browser, fixture.accounts.realtor, 'es')

  stage = 'normal_navigation'
  const page = await visit(realtor, '/realtor-tools?workspace=realtor', 'realtor')
  const directory = named(page, 'region', 'Realtor tools')
  await button(directory, /^GIS, title & official sources/).click()
  const region = worksheet(page, english)
  await region.waitFor()
  check(new URL(page.url()).pathname === '/research', 'RESEARCH_TOOL_NAVIGATION_REQUIRED')

  stage = 'open_legacy_review'
  const legacy = await openReview(page, english, fixture.legacy_id)
  check(legacy.title === legacyTitle && legacy.data.purchase_costs?.length === 0, 'LEGACY_REVIEW_REQUIRES_EMPTY_WORKSHEET')
  await region.getByText(english.empty, { exact: true }).first().waitFor()
  check(await region.getByText(english.empty, { exact: true }).count() === 2
    && await cost(region, english, 1).count() === 0, 'LEGACY_REVIEW_MUST_SHOW_BLANK_CURRENCIES')
  checks.push('legacy_private_review_opens_with_empty_costs')
  await reviewChooser(page, english).selectOption('')
  await field(page, english.review).fill(title)

  stage = 'create_two_currencies'
  await enterCost(region, 1, rows[0], '120.05')
  await enterCost(region, 2, rows[1], '9000,10')
  await enterCost(region, 3, rows[2], '0.00')
  await button(region, english.add).click()
  await region.getByText(amountText).waitFor()
  await region.getByText(dopText).waitFor()
  await confirmTaxLink(region, english)
  await mobileLayout(page, region)
  const [saved] = await Promise.all([
    page.waitForResponse(response => responseFrom(response, workspacePath, 'POST')),
    button(page, english.save).click(),
  ])
  check(saved.status() === 201, 'REVIEW_CREATE_REQUIRED')
  privateResponse(saved)
  const created = await saved.json()
  check(idPattern.test(created.id) && created.title === title && created.version === 1
    && matchesRows(created.data.purchase_costs, rows), 'EXACT_STORED_COSTS_REQUIRED')
  await page.getByText('Saved to your account.', { exact: true }).waitFor()
  await cost(region, english, 3).waitFor()
  check(await cost(region, english, 4).count() === 0, 'BLANK_AMOUNT_MUST_NOT_PERSIST')
  checks.push('mobile_writes_usd_dop_and_explicit_zero_as_exact_cents')

  stage = 'print_summary'
  await button(page, english.print).click()
  check(await page.locator('html').getAttribute('data-rehearsal-print') === 'requested', 'NORMAL_PRINT_CONTROL_REQUIRED')
  await page.emulateMedia({ media: 'print' })
  await region.getByText(rows[2].label, { exact: true }).waitFor()
  await region.getByText(/^Inspection · Paid · USD\s+0\.00$/).waitFor()
  await region.getByText(amountText).waitFor()
  await region.getByText(dopText).waitFor()
  check(!await button(region, english.add).isVisible(), 'PRINT_MUST_HIDE_EDIT_CONTROLS')
  await page.emulateMedia({ media: 'screen' })
  checks.push('print_contains_rows_and_currency_totals_without_editor')

  stage = 'reload_spanish'
  const reopen = await visit(localized, '/research?workspace=realtor', 'realtor')
  const esRegion = worksheet(reopen, spanish)
  await esRegion.waitFor()
  const receipt = await openReview(reopen, spanish, created.id)
  check(matchesRows(receipt.data.purchase_costs, rows), 'FRESH_SESSION_MUST_GET_EXACT_ROWS')
  const first = cost(esRegion, spanish, 1)
  const quoted = cost(esRegion, spanish, 2)
  const zero = cost(esRegion, spanish, 3)
  check(await field(first, spanish.amount).inputValue() === '120.05'
    && await field(quoted, spanish.amount).inputValue() === '9000.10'
    && await field(zero, spanish.amount).inputValue() === '0.00'
    && await field(quoted, spanish.basis).inputValue() === 'quote'
    && await field(zero, spanish.label).inputValue() === rows[2].label, 'SPANISH_EDITOR_MUST_RESTORE_ROWS')
  await esRegion.getByText(amountText).waitFor()
  await esRegion.getByText(dopText).waitFor()
  await confirmTaxLink(esRegion, spanish)
  await mobileLayout(reopen, esRegion)
  checks.push('fresh_spanish_mobile_session_restores_exact_saved_work')

  stage = 'private_scope'
  const otherPage = await visit(newcomer, '/research?workspace=admin', 'admin')
  await worksheet(otherPage, english).waitFor()
  const chooser = reviewChooser(otherPage, english)
  await chooser.selectOption('')
  const foreignList = await direct(newcomer, workspacePath)
  privateResponse(foreignList)
  check(foreignList.ok() && (await foreignList.json()).length === 0
    && await chooser.locator('option').count() === 1, 'OTHER_ADMIN_MUST_NOT_SEE_PRIVATE_REVIEWS')
  await foreignList.dispose()
  await verifyPrivate(newcomer, created.id, 404)
  await verifyPrivate(anonymous, created.id, 401)
  checks.push('another_admin_and_anonymous_cannot_read_private_review')

  stage = 'clear_and_reload'
  for (let i = rows.length; i > 0; i--) await button(esRegion, 'Quitar costo ' + i).click()
  const [cleared] = await Promise.all([
    reopen.waitForResponse(response => responseFrom(response, workspacePath + '/' + created.id, 'PUT')),
    button(reopen, spanish.save).click(),
  ])
  check(cleared.ok(), 'EXPLICIT_CLEAR_MUST_SAVE')
  privateResponse(cleared)
  const updated = await cleared.json()
  check(updated.id === created.id && updated.version === created.version + 1
    && updated.data.purchase_costs?.length === 0, 'EXPLICIT_CLEAR_MUST_PERSIST_EMPTY_LIST')
  await reopen.getByText('Guardado en tu cuenta.', { exact: true }).waitFor()
  await reopen.reload({ waitUntil: 'domcontentloaded' })
  const reopened = await openReview(reopen, spanish, created.id)
  check(reopened.version === updated.version && reopened.data.purchase_costs?.length === 0,
    'RELOADED_CLEAR_REQUIRED')
  check(await worksheet(reopen, spanish).getByText(spanish.empty, { exact: true }).count() === 2,
    'CLEARED_WORKSHEET_MUST_RENDER_EMPTY_TOTALS')
  const legacyAgain = await openReview(reopen, spanish, fixture.legacy_id)
  check(legacyAgain.version === legacy.version && legacyAgain.data.notes === legacy.data.notes,
    'OTHER_PRIVATE_REVIEW_MUST_BE_UNCHANGED')
  checks.push('explicit_clear_saves_and_reloads_without_touching_legacy_review')
  for (const context of [realtor, newcomer, anonymous, localized]) await context.close()
}

let browser
try {
  const fixture = await readFixture()
  browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
  await run(browser, fixture)
  console.log('PURCHASE_COSTS_BROWSER ' + JSON.stringify({ status: 'PASS', checks }))
} catch (error) {
  const detail = error instanceof CheckFailure ? error.message : error?.name === 'TimeoutError' ? 'BROWSER_TIMEOUT' : 'BROWSER_UNEXPECTED_FAILURE'
  console.log('PURCHASE_COSTS_BROWSER_FAILURE ' + stage + ' ' + detail)
  process.exitCode = 1
} finally {
  await browser?.close()
}
