import { lstat, readFile, readdir } from 'node:fs/promises'
import { randomUUID } from 'node:crypto'
import { chromium } from 'playwright-core'

const dashboard = 'http://127.0.0.1:4175'
const api = 'http://127.0.0.1:8000'
const fixturePath = '/browser-fixtures/fixtures.json'
const prefix = '/property-network'
const requests = prefix + '/service-requests'
const inbox = prefix + '/service-referrals'
const title = 'Synthetic pool referral request'
const brief = 'Private fixture brief for scheduled pool maintenance.'
const alias = 'Synthetic requester alias'
const area = 'Synthetic coastal area'
const targetAlias = 'Synthetic Reviewed North'
const sponsorAlias = 'Synthetic Reviewed English'
const approval = 'I approve sharing this brief with this professional if they accept.'
const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/
const checks = []
let stage = 'fixture'

class FailedCheck extends Error {}
function check(condition, reason) {
  if (!condition) throw new FailedCheck(reason)
}

function failureReason(error) {
  if (error instanceof FailedCheck) return error.message
  const message = typeof error?.message === 'string' ? error.message : ''
  if (message.includes('did not find some options')) return 'SELECT_OPTION_NOT_FOUND'
  if (message.includes('intercepts pointer events')) return 'POINTER_INTERCEPTED'
  if (message.includes('strict mode violation')) return 'LOCATOR_AMBIGUOUS'
  return error?.name === 'TimeoutError' ? 'LOCATOR_OR_EVENT_NOT_FOUND' : 'BROWSER_OPERATION_FAILED'
}

async function getFixture() {
  check(process.platform === 'linux' && process.env.GITHUB_ACTIONS === 'true'
    && process.env.GITHUB_REPOSITORY === 'ilovedrrealty/realty-api'
    && process.env.ILDR_BROWSER_FIXTURES === fixturePath
    && (await readdir('/sys/class/net')).join(',') === 'lo', 'OWNED_SYNTHETIC_FIXTURE_REQUIRED')
  const file = await lstat(fixturePath)
  check(file.isFile() && (file.mode & 0o777) === 0o600 && file.size < 4096, 'PRIVATE_FIXTURE_REQUIRED')
  const data = JSON.parse(await readFile(fixturePath, 'utf8'))
  check(uuid.test(data.referralRequestId) && uuid.test(data.referralSponsorRecipientId)
    && uuid.test(data.serviceSourceId) && uuid.test(data.foreignListingId)
    && data.serviceSourceId !== data.foreignListingId && Number.isSafeInteger(data.serviceCodes.north)
    && data.serviceCodes.north > 0, 'EXACT_OWNED_REQUEST_REQUIRED')
  for (const [account, email] of [
    [data.accounts.owner, 'browser-owner@example.com'], [data.accounts.buyer, 'browser-buyer@example.com'],
    [data.serviceProviders.english, 'browser-reviewed-english@example.com'],
    [data.serviceProviders.north, 'browser-reviewed-north@example.com'],
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

async function session(browser, account) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true,
    hasTouch: true, serviceWorkers: 'block', locale: 'en-US' })
  context.setDefaultTimeout(12_000)
  context.setDefaultNavigationTimeout(12_000)
  await context.route('**/*', route => [dashboard, api].includes(new URL(route.request().url()).origin)
    ? route.continue() : route.abort('blockedbyclient'))
  await context.addInitScript(() => {
    if (location.origin === 'http://127.0.0.1:4175') localStorage.setItem('ildr_lang', 'en')
    if (!navigator.onLine) Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => true })
  })
  if (account) {
    await request(context, '/auth/login', { method: 'POST', data: account, noStore: false })
    check((await context.cookies(api)).some(cookie => cookie.name === 'ildr_token' && cookie.httpOnly), 'REAL_SYNTHETIC_LOGIN_REQUIRED')
  }
  return context
}

const button = (scope, name) => scope.getByRole('button', { name, exact: true })
const label = (scope, name) => scope.getByLabel(name, { exact: true })
const text = (scope, name) => scope.getByText(name, { exact: true })

function observe(page, method, path, filters = {}) {
  return page.waitForResponse(response => {
    const url = new URL(response.url())
    return url.origin === api && url.pathname === path && response.request().method() === method
      && Object.entries(filters).every(([key, value]) => url.searchParams.get(key) === value)
  }, { timeout: 12_000 }).catch(() => null)
}

async function observed(result, status = 200, noStore = true) {
  const response = await result
  check(response, 'BROWSER_API_RESPONSE_NOT_OBSERVED')
  check(response.status() === status, 'BROWSER_HTTP_' + response.status())
  if (noStore) check(response.headers()['cache-control'] === 'no-store', 'NO_STORE_REQUIRED')
  return response
}

async function action(page, control, method, path, status = 200, filters = {}) {
  const response = observe(page, method, path, filters)
  await control.click()
  return observed(response, status)
}

function nav(page) {
  return page.getByRole('navigation', { name: 'Private service workflow', exact: true })
}

function card(page, title) {
  return page.getByRole('article').filter({ has: page.getByRole('heading', { name: title, exact: true }) })
}

async function openServices(context) {
  const page = await context.newPage()
  await page.bringToFront()
  const identity = observe(page, 'GET', '/auth/me')
  check((await page.goto(dashboard + '/', { waitUntil: 'domcontentloaded' }))?.ok(), 'REAL_DASHBOARD_REQUIRED')
  await observed(identity, 200, false)
  await page.waitForFunction(() => document.visibilityState === 'visible' && navigator.onLine)
  await page.locator('main button:has(svg.lucide-menu)').click()
  await page.waitForFunction(() => {
    const rect = document.querySelector('aside')?.getBoundingClientRect()
    return Boolean(rect && rect.left >= -1 && rect.right <= innerWidth + 1)
  })
  await button(page.locator('aside'), 'Projects & member community').click()
  await page.waitForURL(url => url.pathname === '/property-network')
  await action(page, button(page, 'Private services'), 'GET', requests, 200, { view: 'managed' })
  await nav(page).waitFor()
  return page
}

async function openRequest(page, path, view) {
  await action(page, button(card(page, title), 'Open request'), 'GET', path, 200, { view })
  await text(page, brief).waitFor()
}

function assertMinimal(row, data, status) {
  const keys = ['id','version','status','category_id','property_class','area_id','language','relationship',
    'created_at','updated_at','provider_alias','can_offer','can_accept','can_decline','can_revoke','request_id'].sort()
  check(Object.keys(row).sort().join() === keys.join() && uuid.test(row.id) && row.status === status
    && row.category_id === 'pool_service' && row.property_class === 'house_villa' && row.area_id === 'cabarete'
    && row.language === 'es'
    && row.provider_alias === null && row.request_id === (status === 'accepted' ? data.referralRequestId : null),
  'EXACT_MINIMAL_TARGET_ROW_REQUIRED')
}

function unopened(value, requestId) {
  const content = typeof value === 'string' ? value : JSON.stringify(value)
  check([title, brief, alias, area, requestId, 'synthetic-unshared@example.com', sponsorAlias].every(item => !content.includes(item)),
    'PRE_ACCEPT_PRIVATE_CONTEXT_VISIBLE')
}

async function proveRevocation(owner, target, path, data) {
  stage = 'revoke'
  const offered = await request(owner, path + '/referrals', { method: 'POST', status: 201, data: {
    submission_id: randomUUID(), provider_user_code: data.serviceCodes.north, area_id: 'cabarete', language: 'es',
    relationship: 'no_prior_work', share_confirmed: true,
  } })
  const targetInbox = await request(target, inbox + '?view=received')
  check(targetInbox.total === 1 && offered.id === targetInbox.items[0].id, 'EXACT_DIRECT_OFFER_REQUIRED')
  assertMinimal(targetInbox.items[0], data, 'offered')
  const result = await request(owner, inbox + '/' + offered.id + '/revoke', { method: 'POST',
    data: { submission_id: randomUUID(), expected_version: offered.version, share_confirmed: false } })
  check(result.status === 'revoked' && (await request(target, inbox + '?view=received')).total === 0,
    'REVOKED_OFFER_REMAINS_VISIBLE')
  await request(target, inbox + '/' + offered.id + '/accept', { method: 'POST', status: 404,
    data: { submission_id: randomUUID(), expected_version: offered.version, share_confirmed: false } })
}

async function sponsorProposal(sponsor, target, owner, path, data) {
  stage = 'sponsor_services'
  const page = await openServices(sponsor)
  stage = 'sponsor_request'
  await action(page, button(nav(page), 'Requests for me'), 'GET', requests, 200, { view: 'received' })
  await openRequest(page, path, 'received')
  stage = 'sponsor_accept'
  await action(page, button(page, 'Accept request'), 'POST',
    path + '/recipients/' + data.referralSponsorRecipientId + '/accept')
  const accepted = await request(sponsor, path + '?view=received')
  check(accepted.recipient.id === data.referralSponsorRecipientId && accepted.recipient.status === 'accepted'
    && accepted.capabilities.can_quote, 'ORIGINAL_ACCEPTED_REQUEST_REQUIRED')
  checks.push('provider_accepts_original_request_from_services')

  stage = 'sponsor_referrals'
  await action(page, button(page, 'View referrals'), 'GET', path + '/referrals')
  await button(page, 'Suggest a professional').click()
  const form = page.locator('form').filter({ has: page.getByRole('heading', { name: 'Suggest a professional', exact: true }) })
  stage = 'sponsor_candidate'
  await form.locator('select:has(>option[value="es"])').selectOption('es')
  const candidateResponse = observe(page, 'GET', path + '/referral-providers', { area_id: 'cabarete', language: 'es' })
  await form.locator('select:has(>option[value="cabarete"])').selectOption('cabarete')
  await observed(candidateResponse)
  const candidates = await request(sponsor, path + '/referral-providers?area_id=cabarete&language=es')
  check(candidates.total === 1 && candidates.items[0].user_code === data.serviceCodes.north
    && candidates.items[0].member_alias === targetAlias
    && Object.keys(candidates.items[0]).sort().join() === 'category_ids,member_alias,user_code', 'EXACT_REVIEWED_CANDIDATE_REQUIRED')
  await form.getByRole('radio', { name: targetAlias, exact: true }).check()
  await form.locator('select:has(>option[value="know_their_work"])').selectOption('know_their_work')
  check(await form.getByRole('checkbox').count() === 0, 'SPONSOR_CANNOT_CONSENT_FOR_MANAGER')
  stage = 'sponsor_suggestion'
  const response = await action(page, button(form, 'Send suggestion for review'), 'POST', path + '/referrals', 201)
  const dataSent = response.request().postDataJSON()
  check(dataSent.provider_user_code === data.serviceCodes.north && dataSent.share_confirmed === false
    && dataSent.area_id === 'cabarete' && dataSent.language === 'es' && dataSent.relationship === 'know_their_work'
    && uuid.test(dataSent.submission_id), 'EXACT_PRIVATE_SUGGESTION_REQUIRED')
  await text(page, 'Awaiting manager review').waitFor()
  const proposed = await request(sponsor, prefix + '/service-referral-submissions/' + dataSent.submission_id)
  const ownerRows = await request(owner, path + '/referrals')
  check(proposed.status === 'proposed' && proposed.version === 1 && proposed.request_id === data.referralRequestId
    && ownerRows.items.some(item => item.id === proposed.id && item.can_offer)
    && (await request(target, inbox + '?view=received')).total === 0, 'PRIVATE_PROPOSAL_SCOPE_REQUIRED')
  await request(target, path + '?view=received', { status: 404 })
  await request(target, prefix + '/service-referral-submissions/' + dataSent.submission_id, { status: 404 })
  checks.push('accepted_provider_suggests_reviewed_named_target')
  return proposed
}

async function ownerOffer(owner, target, path, proposal, data) {
  stage = 'owner_services'
  const page = await openServices(owner)
  stage = 'owner_request'
  await openRequest(page, path, 'managed')
  await action(page, button(page, 'View referrals'), 'GET', path + '/referrals')
  stage = 'owner_review'
  const offeredCard = card(page, targetAlias).filter({ has: page.getByRole('button', { name: 'Review referral offer', exact: true }) })
  await button(offeredCard, 'Review referral offer').click()
  const form = page.locator('form').filter({ has: page.getByRole('heading', { name: 'Review referral offer', exact: true }) })
  await text(form, 'Review what ' + targetAlias + ' can see after accepting.').waitFor()
  const shared = form.getByRole('region', { name: 'Brief shared on acceptance', exact: true })
  await text(shared, title).waitFor()
  await text(shared, brief).waitFor()
  check((await shared.innerText()).includes(alias) && (await shared.innerText()).includes(area), 'EXACT_SHARED_BRIEF_PREVIEW_REQUIRED')
  const confirm = button(form, 'Offer referral')
  check(await confirm.isDisabled(), 'UNCONFIRMED_OFFER_ENABLED')
  await request(owner, inbox + '/' + proposal.id + '/offer', { method: 'POST', status: 422,
    data: { submission_id: randomUUID(), expected_version: proposal.version, share_confirmed: false } })
  check((await request(target, inbox + '?view=received')).total === 0, 'UNCONFIRMED_OFFER_VISIBLE')
  await label(form, approval).check()
  stage = 'owner_offer'
  const response = await action(page, confirm, 'POST', inbox + '/' + proposal.id + '/offer')
  const body = response.request().postDataJSON()
  check(body.share_confirmed === true && body.expected_version === proposal.version && uuid.test(body.submission_id),
    'EXPLICIT_MANAGER_OFFER_REQUIRED')
  await text(card(page, targetAlias).filter({ hasText: 'Awaiting professional response' }), 'Awaiting professional response').waitFor()
  const receipt = await request(owner, prefix + '/service-referral-submissions/' + body.submission_id)
  check(receipt.id === proposal.id && receipt.status === 'offered' && receipt.provider_alias === targetAlias
    && receipt.request_id === data.referralRequestId, 'EXACT_MANAGER_OFFER_REQUIRED')
  checks.push('manager_explicitly_approves_only_selected_target')
  return receipt
}

async function targetAcceptance(target, path, offered, data) {
  stage = 'target_services'
  const page = await openServices(target)
  stage = 'target_offer'
  await action(page, button(nav(page), 'Private referrals'), 'GET', inbox, 200, { view: 'received' })
  await page.getByRole('heading', { name: 'Referrals received', exact: true }).waitFor()
  await text(page, 'Awaiting professional response').waitFor()
  const projection = await request(target, inbox + '?view=received')
  check(projection.total === 1 && projection.items[0].id === offered.id && projection.items[0].can_accept
    && projection.items[0].relationship === 'know_their_work',
    'EXACT_TARGET_OFFER_REQUIRED')
  assertMinimal(projection.items[0], data, 'offered')
  unopened(await page.locator('main').innerText(), data.referralRequestId)
  unopened(projection, data.referralRequestId)
  check(await button(page, 'Open request').count() === 0, 'QUOTE_REQUEST_OPEN_BEFORE_ACCEPTANCE')
  const layout = await page.locator('main').evaluate(node => ({
    touches: navigator.maxTouchPoints, width: innerWidth, right: node.getBoundingClientRect().right,
    root: document.documentElement.scrollWidth,
  }))
  check(layout.touches > 0 && layout.width === 390 && layout.right <= 391 && layout.root <= 391, 'MOBILE_LAYOUT_REQUIRED')
  checks.push('target_mobile_offer_contains_minimal_fields')

  stage = 'target_accept'
  await button(page, 'Review acceptance').click()
  const form = page.locator('form').filter({ has: page.getByRole('heading', { name: 'Review acceptance', exact: true }) })
  check(await form.getByRole('checkbox').count() === 0, 'TARGET_CANNOT_AUTHOR_CONSENT')
  unopened(await form.innerText(), data.referralRequestId)
  const response = await action(page, button(form, 'Accept referral'), 'POST', inbox + '/' + offered.id + '/accept')
  const patch = response.request().postDataJSON()
  check(patch.share_confirmed === false && patch.expected_version === offered.version && uuid.test(patch.submission_id),
    'EXACT_TARGET_ACCEPT_REQUIRED')
  const result = await request(target, prefix + '/service-referral-submissions/' + patch.submission_id)
  assertMinimal(result, data, 'accepted')

  stage = 'target_quote'
  await action(page, button(page, 'Open request'), 'GET', path, 200, { view: 'received' })
  await text(page, brief).waitFor()
  await button(page, 'Prepare or replace quote').waitFor()
  const quote = await request(target, path + '?view=received')
  check(quote.id === data.referralRequestId && quote.title === title && quote.brief === brief
    && quote.recipient.status === 'accepted' && quote.capabilities.can_quote && !quote.project_id
    && !Object.hasOwn(quote, 'source') && !Object.hasOwn(quote, 'recipients'), 'NORMAL_PRIVATE_QUOTE_SCOPE_REQUIRED')
  checks.push('accepted_target_opens_normal_quote_scope')
}

async function main() {
  const data = await getFixture()
  const path = requests + '/' + data.referralRequestId
  const browser = await chromium.launch({
    headless: true, timeout: 20_000, proxy: { server: 'http://127.0.0.1:9', bypass: '127.0.0.1' },
    args: ['--disable-background-networking', '--disable-component-update', '--disable-sync', '--no-first-run'],
  })
  try {
    stage = 'auth'
    const owner = await session(browser, data.accounts.owner)
    const sponsor = await session(browser, data.serviceProviders.english)
    const target = await session(browser, data.serviceProviders.north)
    const outsider = await session(browser, data.accounts.buyer)
    const anonymous = await session(browser, null)
    stage = 'scope'
    await request(anonymous, inbox + '?view=received', { status: 401 })
    await request(outsider, path + '/referrals', { status: 404 })
    await request(outsider, path + '/referral-providers?area_id=cabarete&language=es', { status: 404 })
    await proveRevocation(owner, target, path, data)
    const proposal = await sponsorProposal(sponsor, target, owner, path, data)
    const offered = await ownerOffer(owner, target, path, proposal, data)
    await targetAcceptance(target, path, offered, data)
    stage = 'outsider_denied'
    check((await request(outsider, inbox + '?view=received')).total === 0, 'UNRELATED_REFERRAL_VISIBLE')
    await request(outsider, path + '?view=received', { status: 404 })
    await request(outsider, inbox + '/' + offered.id + '/accept', { method: 'POST', status: 404,
      data: { submission_id: randomUUID(), expected_version: offered.version, share_confirmed: false } })
    const managed = await request(owner, path + '?view=managed')
    check(managed.recipients.length === 2 && managed.recipients.every(row => row.status === 'accepted'),
      'ORIGINAL_AND_REFERRED_RECIPIENTS_REQUIRED')
    checks.push('other_accounts_and_revoked_offers_denied', 'actual_loopback_only_without_outbound')
    console.log('PRIVATE_REFERRAL_BROWSER', JSON.stringify({ status: 'PASS', checks }))
  } finally {
    await browser.close()
  }
}

main().catch(error => {
  console.error('PRIVATE_REFERRAL_BROWSER_FAILURE', stage, failureReason(error))
  process.exitCode = 1
})
