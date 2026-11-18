import { lstat, readFile } from 'node:fs/promises'
import { chromium } from 'playwright-core'

const dashboard = 'http://127.0.0.1:4175', api = 'http://127.0.0.1:8918'
const network = '/property-network', wait = 15_000
const roles = { owner: 'owner', provider: 'buyer', outsider: 'buyer' }
const uuid = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/
const contexts = []
const result = { status: 'FAIL', checks: [], geometry: [], writes: 0, unexpected_mutations: 0,
  blocked_external_requests: 0, external_navigation: false, page_errors: 0 }
let fixture, requestId, recipientId, stage = 'fixture'
class CheckError extends Error {}
function check(value, code) { if (!value) throw new CheckError(code) }
const exact = (value, keys) => value && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key))
const button = (scope, name) => scope.getByRole('button', { name, exact: true })
const text = (scope, value) => scope.getByText(value, { exact: true })
const panel = (page, name) => page.locator('section').filter({ has: page.getByRole('heading', { name, exact: true, level: 2 }) })
const requestPath = () => `${network}/service-requests/${requestId}`
const recipientPath = () => `${requestPath()}/recipients/${recipientId}`
const privateHeaders = response => response.headers()['cache-control'] === 'no-store'
  && response.headers().pragma === 'no-cache' && response.headers()['x-content-type-options'] === 'nosniff'
const endpoint = (response, path, method = 'GET') => {
  const url = new URL(response.url())
  return url.origin === api && url.pathname === path && response.request().method() === method
}
async function field(scope, prompt, selector) {
  const input = scope.locator('label').filter({ hasText: prompt }).locator(selector)
  await input.first().waitFor()
  check(await input.count() === 1 && await input.evaluate((element, expected) => {
    const label = element.closest('label')
    const value = [...(label?.childNodes ?? [])].filter(node => node.nodeType === Node.TEXT_NODE)
      .map(node => node.textContent).join('').replace(/\s+/g, ' ').trim()
    return label?.control === element && value === expected
  }, prompt), 'NATIVE_FIELD_SCOPE_REQUIRED')
  return input
}
async function readFixture() {
  check(process.platform === 'linux' && process.env.GITHUB_ACTIONS === 'true'
    && process.env.GITHUB_REPOSITORY === 'ilovedrrealty/realty-api'
    && process.env.ILDR_BROWSER_FIXTURES === '/browser-fixtures/fixtures.json', 'OWNED_HOSTED_FIXTURE_REQUIRED')
  const info = await lstat(process.env.ILDR_BROWSER_FIXTURES)
  check(info.isFile() && (info.mode & 0o777) === 0o600 && info.size < 8192, 'PRIVATE_FIXTURE_REQUIRED')
  const value = JSON.parse(await readFile(process.env.ILDR_BROWSER_FIXTURES, 'utf8'))
  check(exact(value, ['accounts', 'codes', 'facts']) && exact(value.accounts, Object.keys(roles))
    && exact(value.codes, Object.keys(roles)), 'FIXTURE_SHAPE_INVALID')
  for (const actor of Object.keys(roles)) {
    check(exact(value.accounts[actor], ['email', 'password'])
      && value.accounts[actor].email === `browser-intake-${actor}@example.com`
      && value.accounts[actor].password.length === 32 && Number.isSafeInteger(value.codes[actor])
      && value.codes[actor] > 0, 'SYNTHETIC_ACCOUNT_INVALID')
  }
  check(exact(value.facts, ['listingId', 'listingTitle', 'providerAlias', 'title', 'brief', 'requesterAlias', 'area',
    'category', 'propertyClass', 'quoteScope', 'quoteExclusions', 'validUntil'])
    && uuid.test(value.facts.listingId) && value.facts.category === 'pool_service'
    && value.facts.propertyClass === 'house_villa' && /^\d{4}-\d{2}-\d{2}$/.test(value.facts.validUntil), 'SYNTHETIC_FACTS_INVALID')
  return value
}
async function session(browser, actor, lang, width = 390) {
  const context = await browser.newContext({ viewport: { width, height: 844 }, serviceWorkers: 'block',
    locale: lang === 'es' ? 'es-DO' : 'en-US' })
  contexts.push(context); context.setDefaultTimeout(wait); context.setDefaultNavigationTimeout(wait)
  await context.addInitScript(({ origin, lang }) => {
    if (location.origin !== origin) return
    localStorage.setItem('ildr_lang', lang)
    if (!navigator.onLine) Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => true })
  }, { origin: dashboard, lang })
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), method = request.method()
    if (![dashboard, api].includes(url.origin)) {
      result.blocked_external_requests += 1
      if (request.isNavigationRequest()) result.external_navigation = true
      return route.abort('blockedbyclient')
    }
    if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
      const paths = actor === 'owner' ? [`${network}/service-requests`]
        : actor === 'provider' ? [`${recipientPath()}/accept`, `${recipientPath()}/proposals`] : []
      if (url.origin !== api || method !== 'POST' || url.search || !paths.includes(url.pathname)) {
        result.unexpected_mutations += 1
        return route.abort('blockedbyclient')
      }
      result.writes += 1
    }
    return route.continue()
  })
  if (actor) {
    const response = await context.request.post(api + '/auth/login', { data: fixture.accounts[actor],
      headers: { Origin: dashboard }, timeout: wait, maxRedirects: 0 })
    try {
      const cookie = (await context.cookies(api)).find(value => value.name === 'ildr_token')
      check(response.status() === 200 && cookie?.httpOnly && cookie.domain === '127.0.0.1', 'ACTUAL_COOKIE_LOGIN_REQUIRED')
    } finally { await response.dispose() }
  }
  const page = await context.newPage()
  page.on('pageerror', () => { result.page_errors += 1 })
  const t = JSON.parse(await readFile(`/browser-run/property-network-${lang}.json`, 'utf8'))
  return { context, page, actor, lang, width, t, s: t.service }
}
async function open(state, received = false) {
  await state.page.bringToFront()
  const [response] = await Promise.all([state.page.waitForResponse(value => endpoint(value, '/auth/me')),
    state.page.goto(`${dashboard}${network}?tab=services`, { waitUntil: 'domcontentloaded' })])
  const user = await response.json()
  check(response.status() === 200 && user.role === roles[state.actor]
    && user.email === fixture.accounts[state.actor].email, 'ACTUAL_ROLE_REQUIRED')
  await state.page.waitForFunction(() => document.visibilityState === 'visible' && navigator.onLine)
  const nav = state.page.getByRole('navigation', { name: state.s.navigation, exact: true })
  await nav.waitFor()
  if (received) await button(nav, state.s.received).click()
}
async function openRequest(state, received) {
  await open(state, received)
  const card = state.page.locator('article').filter({ has: state.page.getByRole('heading', { name: fixture.facts.title, exact: true }) })
  const [response] = await Promise.all([state.page.waitForResponse(value => endpoint(value, requestPath())
    && new URL(value.url()).searchParams.get('view') === (received ? 'received' : 'managed')),
    button(card, state.s.open_request).click()])
  check(response.status() === 200 && privateHeaders(response), 'NORMAL_REQUEST_ENTRY_REQUIRED')
  await state.page.getByRole('heading', { name: fixture.facts.title, exact: true, level: 2 }).waitFor()
  return response.json()
}
async function write(page, path, status, action) {
  const [response] = await Promise.all([page.waitForResponse(value => endpoint(value, path, 'POST')), action()])
  check(response.status() === status && privateHeaders(response), 'REAL_UI_WRITE_REQUIRED')
  return { request: response.request().postDataJSON(), data: await response.json() }
}
async function read(state, path, status = 200) {
  const response = await state.context.request.get(api + path, { maxRedirects: 0, timeout: wait })
  try {
    check(response.status() === status && privateHeaders(response), 'PRIVATE_READ_REQUIRED')
    const data = await response.json()
    if (status >= 400) check(exact(data, ['detail']), 'DENIAL_DATA_LEAK')
    return data
  } finally { await response.dispose() }
}
async function geometry(state, name) {
  const dimensions = await state.page.evaluate(() => ({ width: innerWidth, document: document.documentElement.scrollWidth,
    body: document.body.scrollWidth }))
  check(dimensions.width === state.width && dimensions.document <= state.width && dimensions.body <= state.width, 'MOBILE_PAGE_OVERFLOW')
  result.geometry.push({ name, lang: state.lang, ...dimensions })
}
async function step(name, action) { stage = name; await action(); result.checks.push(name) }

async function main() {
  fixture = await readFixture()
  const facts = fixture.facts
  const browser = await chromium.launch({ headless: true, args: ['--disable-background-networking', '--disable-component-update', '--disable-sync', '--no-first-run'] })
  try {
    const owner = await session(browser, 'owner', 'en')
    const provider = await session(browser, 'provider', 'es')
    await step('normal_owner_request_form_explicit_provider_choice_preview_and_consent', async () => {
      await open(owner)
      await button(owner.page, owner.s.new_request).click()
      const form = panel(owner.page, owner.s.new_request)
      await (await field(form, owner.s.source, 'select')).selectOption('listing')
      const source = await field(form, owner.s.choose_source, 'select')
      await source.locator(`option[value="${facts.listingId}"]`).waitFor({ state: 'attached' })
      await source.selectOption(facts.listingId)
      await (await field(form, owner.t.property_class, 'select')).selectOption(facts.propertyClass)
      await (await field(form, owner.s.category, 'select')).selectOption(facts.category)
      const picker = panel(owner.page, owner.s.find_members)
      const mode = await field(picker, owner.s.matches.mode, 'select')
      check(await mode.inputValue() === 'reviewed', 'REVIEWED_PROVIDER_DEFAULT_REQUIRED')
      await mode.selectOption('opt_in')
      const member = picker.getByRole('group', { name: facts.providerAlias, exact: true })
      await member.getByRole('checkbox').check()
      for (const [label, value, type] of [[owner.s.request_title, facts.title, 'input'],
        [owner.s.requester_alias, facts.requesterAlias, 'input'], [owner.s.brief, facts.brief, 'textarea'],
        [owner.s.coarse_area, facts.area, 'input']]) await (await field(form, label, type)).fill(value)
      check(await button(form, owner.s.send_request).isDisabled(), 'PREVIEW_REQUIRED_BEFORE_REQUEST')
      await button(form, owner.s.preview).click()
      const preview = form.getByRole('region', { name: owner.s.preview, exact: true })
      await text(preview, facts.brief).waitFor()
      check(await button(form, owner.s.send_request).isDisabled(), 'EXPLICIT_SHARED_BRIEF_CONSENT_REQUIRED')
      await preview.getByRole('checkbox', { name: owner.s.share_ack, exact: true }).check()
      await geometry(owner, 'service_request_preview_en')
      const saved = await write(owner.page, `${network}/service-requests`, 201, () => button(form, owner.s.send_request).click())
      check(uuid.test(saved.request.submission_id) && saved.request.listing_id === facts.listingId
        && saved.request.property_id === null && saved.request.recipient_user_codes.length === 1
        && saved.request.recipient_user_codes[0] === fixture.codes.provider && saved.request.shared_brief_acknowledged === true
        && !Object.hasOwn(saved.request, 'reviewed_match'), 'EXPLICIT_OPT_IN_REQUEST_BODY_INVALID')
      check(uuid.test(saved.data.id) && saved.data.view === 'managed' && saved.data.status === 'open'
        && saved.data.project_id === null && saved.data.recipients.length === 1, 'CREATED_REQUEST_INVALID')
      requestId = saved.data.id; recipientId = saved.data.recipients[0].id
      await owner.page.getByRole('heading', { name: facts.title, exact: true, level: 2 }).waitFor()
    })
    await step('selected_provider_accepts_invitation_from_received_request', async () => {
      const received = await openRequest(provider, true)
      check(received.recipient.id === recipientId && received.recipient.status === 'pending'
        && received.capabilities.can_accept && !received.capabilities.can_quote && !Object.hasOwn(received, 'source'), 'PRIVATE_RECIPIENT_PROJECTION_INVALID')
      const saved = await write(provider.page, `${recipientPath()}/accept`, 200, () => button(panel(provider.page, provider.s.my_thread), provider.s.accept).click())
      check(saved.data.recipient.id === recipientId && saved.data.recipient.status === 'accepted'
        && uuid.test(saved.request.submission_id), 'RECIPIENT_ACCEPTANCE_INVALID')
      await button(panel(provider.page, provider.s.my_thread), provider.s.quote).waitFor()
    })
    await step('provider_submits_first_real_quote_from_spanish_form', async () => {
      await button(panel(provider.page, provider.s.my_thread), provider.s.quote).click()
      const quote = panel(provider.page, provider.s.quote)
      await (await field(quote, provider.s.amount, 'input')).fill('175.00')
      await (await field(quote, provider.s.currency, 'select')).selectOption('USD')
      await (await field(quote, provider.s.pricing_basis, 'select')).selectOption('fixed')
      await (await field(quote, provider.s.scope, 'textarea')).fill(facts.quoteScope)
      await (await field(quote, provider.s.exclusions, 'textarea')).fill(facts.quoteExclusions)
      await (await field(quote, provider.s.estimated_days, 'input')).fill('2')
      await (await field(quote, provider.s.valid_until, 'input[type="date"]')).fill(facts.validUntil)
      await geometry(provider, 'service_quote_form_es')
      const saved = await write(provider.page, `${recipientPath()}/proposals`, 200, () => button(quote, provider.s.submit_quote).click())
      check(saved.request.current_proposal_id === null && saved.request.expected_proposal_version === null
        && saved.request.terms.amount === '175.00' && saved.request.terms.scope_text === facts.quoteScope
        && saved.request.terms.currency === 'USD' && saved.request.terms.pricing_basis === 'fixed'
        && saved.request.terms.estimated_days === 2 && saved.request.terms.valid_until === facts.validUntil,
      'FIRST_QUOTE_BODY_INVALID')
      check(saved.data.current_proposal?.kind === 'provider_quote' && saved.data.current_proposal.revision_no === 1
        && saved.data.current_proposal.status === 'submitted' && saved.data.current_proposal.amount === '175.00', 'FIRST_QUOTE_RESULT_INVALID')
      await text(panel(provider.page, provider.s.my_thread), facts.quoteScope).waitFor()
    })
    await step('owner_compares_exact_quote_while_foreign_and_anonymous_access_stays_private', async () => {
      await openRequest(owner, false)
      const comparison = panel(owner.page, owner.s.compare)
      await text(comparison, facts.quoteScope).waitFor()
      await text(comparison, facts.providerAlias).waitFor()
      const data = await read(owner, `${requestPath()}/comparison`)
      check(data.groups.length === 2 && data.groups[0].currency === 'USD' && data.groups[0].items.length === 1
        && data.groups[0].items[0].current_proposal.amount === '175.00'
        && data.groups[0].items[0].current_proposal.scope_text === facts.quoteScope
        && data.groups[1].currency === 'DOP' && data.groups[1].items.length === 0 && data.unquoted.length === 0,
      'OWNER_COMPARISON_INVALID')
      const outsider = await session(browser, 'outsider', 'en')
      const guest = await session(browser, null, 'en')
      for (const path of [`${requestPath()}?view=managed`, `${requestPath()}?view=received`, `${requestPath()}/comparison`]) {
        await read(outsider, path, 404); await read(guest, path, 401)
      }
      await geometry(owner, 'owner_quote_comparison_en')
    })
    check(result.writes === 3 && !result.unexpected_mutations && !result.page_errors
      && !result.external_navigation && result.geometry.length === 3, 'INTAKE_CONTAINMENT_INVALID')
  } finally {
    await Promise.all(contexts.map(context => context.close())); await browser.close()
  }
}
try { await main(); result.status = 'PASS' }
catch (error) {
  result.failed_step = stage
  result.error_code = error instanceof CheckError ? error.message : error?.name === 'TimeoutError' ? 'TIMEOUT' : 'UNEXPECTED'
  const line = String(error?.stack ?? '').match(/run-service-intake-browser\.mjs:(\d+):\d+/)?.[1]
  if (line) result.source_line = Number(line)
  process.exitCode = 1
}
console.log('SERVICE_INTAKE_BROWSER_RESULT ' + JSON.stringify(result))
