import { lstat, readFile } from 'node:fs/promises'
import { writeSync } from 'node:fs'
import { chromium } from 'playwright-core'

const API = 'http://127.0.0.1:8918', DASH = 'http://127.0.0.1:4175', LANDING = 'http://127.0.0.1:8917'
const contexts = [], journeys = [], geometries = [], presence = new WeakSet(), commands = new Map(), methods = new WeakMap()
let browser, accounts, facts, stage = 'fixture', timer, failed = false
let pageErrors = 0, externalWrites = 0, unexpectedMutations = 0, trackingRequests = 0, blockedExternal = 0
let actualLogins = 0, boundaryChecks = 0, deniedPosts = 0, controlTransitions = 0, presenceCount = 0, lastLoginAt = 0
class Failure extends Error {}
const check = (value, reason) => { if (!value) throw new Failure(reason) }
const button = (scope, name) => scope.getByRole('button', { name, exact: true })
const translated = (value, fields) => value.replace(/\{\{(\w+)\}\}/g, (_, key) => String(fields[key]))
const uuid = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/
function advance(value) {
  check(/^[A-Za-z][A-Za-z0-9_]{0,95}$/.test(value), 'FINITE_PHASE_REQUIRED')
  stage = value; clearTimeout(timer)
  timer = setTimeout(() => fail(new Failure('STAGE_DEADLINE')), 60000)
}
function fail(error) {
  if (failed) return
  failed = true
  let reason = 'PROCESS_ERROR'
  try {
    if (error instanceof Failure && /^[A-Z][A-Z0-9_]{0,95}$/.test(error.message)) reason = error.message
    else if (error?.name === 'TimeoutError') reason = 'TIMEOUT'
  } catch { /* Never expose arbitrary exception values. */ }
  const frames = []
  try {
    for (const line of String(error?.stack ?? '').split('\n')) {
      const match = line.match(/(?:file:\/\/)?\/browser-run\/run-browser\.mjs:([0-9]{1,5}):([0-9]{1,5})\)?$/)
      if (!match) continue
      const frame = { line: Number(match[1]), column: Number(match[2]) }
      if (frame.line > 0 && frame.column > 0 && !frames.some(x => x.line === frame.line && x.column === frame.column)) frames.push(frame)
      if (frames.length === 8) break
    }
  } catch { /* A missing safe frame does not turn failure into success. */ }
  try {
    writeSync(1, `INBOX_BROWSER_FAILURE ${stage} ${reason}\n`)
    if (frames.length) writeSync(1, 'INBOX_BROWSER_FRAMES ' + JSON.stringify({ frames }) + '\n')
  } catch { /* The nonzero exit remains authoritative. */ }
  process.exit(1)
}
function mainFlowFailure(error) {
  let reason = 'PROCESS_ERROR', errorName = 'OtherError'
  const frames = []
  try {
    if (error instanceof Failure && /^[A-Z][A-Z0-9_]{0,95}$/.test(error.message)) reason = error.message
    else if (error?.name === 'TimeoutError') reason = 'TIMEOUT'
    if (['Error', 'TypeError', 'SyntaxError', 'TimeoutError', 'TargetClosedError', 'AbortError'].includes(error?.name)) errorName = error.name
    for (const line of String(error?.stack ?? '').split('\n')) {
      const match = line.match(/(?:file:\/\/)?\/browser-run\/run-browser\.mjs:([0-9]{1,5}):([0-9]{1,5})\)?$/)
      if (!match) continue
      const frame = { line: Number(match[1]), column: Number(match[2]) }
      if (frame.line > 0 && frame.column > 0 && !frames.some(x => x.line === frame.line && x.column === frame.column)) frames.push(frame)
      if (frames.length === 8) break
    }
  } catch { /* Only finite source-owned diagnostics, never error text. */ }
  try { writeSync(1, 'INBOX_BROWSER_MAIN_FLOW_FAILURE ' + JSON.stringify({ stage, reason, error_name: errorName, frames }) + '\n') }
  catch { /* Preserve the original throw and cleanup. */ }
}
process.on('uncaughtException', fail)
process.on('unhandledRejection', fail)
const isMutation = request => !['GET', 'HEAD', 'OPTIONS'].includes(request.method())
const noStore = response => /(?:^|,)\s*no-store(?:\s*,|$)/i.test(response.headers()['cache-control'] || '')

async function read(context, path, expected = 200) {
  check(path.startsWith('/') && !path.startsWith('//') && !path.includes('#'), 'OWNED_API_PATH_REQUIRED')
  const response = await context.request.get(API + path, { timeout: 15000, maxRedirects: 0 })
  try { check(response.status() === expected && (!path.startsWith('/messaging/') || noStore(response)), 'ACTUAL_PRIVATE_READ_STATUS_REQUIRED'); return await response.json() }
  finally { await response.dispose() }
}
async function control(context, action) {
  const response = await context.request.post(API + '/__ci/control', { data: { action },
    headers: { 'x-ci-control': facts.controlSecret }, timeout: 15000, maxRedirects: 0 })
  try { check(response.status() === 200 && (await response.json()).status === 'PASS', 'ACTUAL_OWNED_CONTROL_REQUIRED'); controlTransitions++ }
  finally { await response.dispose() }
}
async function contextFor(locale, actor = null) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, serviceWorkers: 'block' })
  contexts.push(context); methods.set(context, [])
  context.setDefaultTimeout(15000); context.setDefaultNavigationTimeout(20000)
  context.on('page', page => page.on('pageerror', () => { pageErrors++ }))
  context.on('request', request => {
    const url = new URL(request.url())
    if (url.origin === API && isMutation(request)) {
      methods.get(context).push({ method: request.method(), path: url.pathname })
      if (!(request.method() === 'POST' && ['/auth/login', '/__ci/control'].includes(url.pathname))
        && !url.pathname.startsWith('/messaging/')) unexpectedMutations++
    }
    if (/google-analytics|googletagmanager|facebook\.com\/tr|doubleclick/.test(url.href)) trackingRequests++
  })
  await context.route('**/*', route => {
    const request = route.request(), url = new URL(request.url())
    if (![API, DASH, LANDING].includes(url.origin)) {
      blockedExternal++; if (isMutation(request)) externalWrites++
      return route.abort('blockedbyclient')
    }
    return route.continue()
  })
  await context.addInitScript(({ locale, owned }) => {
    if (!owned.includes(location.origin)) return
    localStorage.setItem('ildr_lang', locale)
    localStorage.setItem('ildr_measurement_consent_v1', JSON.stringify({ choice: 'denied', savedAt: Date.now() }))
    const before = navigator.onLine
    if (!before) Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => true })
    Object.defineProperty(window, '__inboxOnline', { value: { before, modeled: !before } })
  }, { locale, owned: [DASH, LANDING] })
  if (actor) {
    if (actualLogins === 7) {
      while (Date.now() - lastLoginAt < 61000) {
        advance(locale + '_real_login_rate_window')
        await new Promise(resolve => setTimeout(resolve, Math.min(30000, 61000 - (Date.now() - lastLoginAt))))
      }
      advance(locale + '_accounts')
    }
    const response = await context.request.post(API + '/auth/login', { data: accounts[actor], headers: { Origin: DASH }, timeout: 15000, maxRedirects: 0 })
    try { check(response.status() === 200 && (await context.cookies(API)).some(x => x.name === 'ildr_token' && x.httpOnly), 'REAL_COOKIE_LOGIN_REQUIRED') }
    finally { await response.dispose() }
    const identity = await read(context, '/auth/me')
    check(identity.id === facts.ids[actor] && identity.role === facts.roles[actor], 'REAL_CURRENT_ROLE_REQUIRED'); actualLogins++; lastLoginAt = Date.now()
  }
  return context
}
async function foreground(page) {
  await page.bringToFront()
  await page.waitForFunction(() => document.visibilityState === 'visible' && navigator.onLine)
  if (!presence.has(page)) {
    const value = await page.evaluate(() => ({ ...window.__inboxOnline, after: navigator.onLine, visible: document.visibilityState === 'visible' }))
    check(Object.keys(value).length === 4 && Object.values(value).every(x => typeof x === 'boolean')
      && value.modeled === !value.before && value.after && value.visible, 'ACTUAL_LOOPBACK_PRESENCE_REQUIRED')
    console.log('INBOX_LOOPBACK_STATE ' + JSON.stringify(value)); presence.add(page); presenceCount++
  }
}
async function navigate(page, path, discard = false) {
  check(path.startsWith('/') && !path.startsWith('//'), 'FIXED_DASHBOARD_PATH_REQUIRED')
  const handler = async dialog => { check(discard && dialog.type() === 'beforeunload', 'ONLY_OWNED_DISCARD_DIALOG_ALLOWED'); await dialog.accept() }
  if (discard) page.on('dialog', handler)
  try { await page.goto(DASH + path, { waitUntil: 'domcontentloaded' }); await foreground(page) }
  finally { if (discard) page.off('dialog', handler) }
}
async function geometry(page, label) {
  for (const width of [320, 390]) {
    advance(label + '_geometry_' + width)
    await page.setViewportSize({ width, height: 844 }); await foreground(page)
    const value = await page.evaluate(() => ({ width: innerWidth, document: document.documentElement.scrollWidth }))
    check(value.width === width && value.document <= width + 1, 'VIEWPORT_OVERFLOW')
    for (const item of await page.locator('main').locator('input,textarea,select,button,a,summary').all()) {
      if (!await item.isVisible()) continue
      const box = await item.boundingBox()
      check(box && box.x >= -1 && box.x + box.width <= width + 1, 'CONTROL_OVERFLOW')
    }
    geometries.push({ label, ...value }); console.log('INBOX_BROWSER_GEOMETRY ' + JSON.stringify({ label, ...value }))
  }
}
async function inputFailureCensus(page, copy, locale) {
  const diagnosedStage = stage
  let deadline, result = { state: 'unavailable', stage: diagnosedStage, locale }
  try {
    result = await Promise.race([
      page.evaluate(({ labels, phase, language }) => {
        const main = document.querySelector('main')
        const panels = [...main?.querySelectorAll('section') ?? []].filter(section =>
          [...section.children].some(child => child.tagName === 'H2' && child.textContent?.trim() === labels.thread))
        const fields = panels.flatMap(section => [...section.querySelectorAll('textarea')])
        const field = fields.length === 1 ? fields[0] : null
        const posts = panels.flatMap(section => [...section.querySelectorAll('button')])
          .filter(button => button.textContent?.trim() === labels.post_message)
        const notices = [...main?.querySelectorAll('[role="status"],[role="alert"],[aria-live] > p') ?? []]
        const flag = key => notices.some(node => node.textContent?.trim() === labels[key])
        const box = field?.getBoundingClientRect(), style = field ? getComputedStyle(field) : null
        // Only public control state and lengths of the known synthetic input.
        // Never return its value, document text, account IDs or React internals.
        return { state: 'observed', stage: phase, locale: language, panel_count: panels.length,
          textarea_count: fields.length, present: field !== null,
          visible: !!box && box.width > 0 && box.height > 0 && style.display !== 'none' && style.visibility !== 'hidden',
          disabled: field?.matches(':disabled') ?? false, read_only: field?.readOnly ?? false,
          fieldset_disabled: field?.closest('fieldset')?.disabled ?? false,
          code_units: field ? field.value.length : -1, code_points: field ? [...field.value].length : -1,
          max_length: field?.maxLength ?? -1, focused: field !== null && document.activeElement === field,
          post_button_count: posts.length, post_disabled: posts.length === 1 && posts[0].matches(':disabled'),
          checking: flag('checking'), stale: flag('stale'), access_lost: flag('access_lost'),
          read_error: flag('read_error'), review_expired: flag('review_expired'), rate_limit: flag('rate_limit') }
      }, { labels: Object.fromEntries(['thread', 'post_message', 'checking', 'stale', 'access_lost', 'read_error', 'review_expired', 'rate_limit'].map(key => [key, copy.inbox[key]])), phase: diagnosedStage, language: locale }),
      new Promise((_, reject) => { deadline = setTimeout(() => reject(new Failure('CENSUS_DEADLINE')), 2000) }),
    ])
  } catch { /* Unavailable observation never changes the original failure. */ }
  finally { clearTimeout(deadline) }
  try { writeSync(1, 'INBOX_BROWSER_INPUT_CENSUS ' + JSON.stringify(result) + '\n') }
  catch { /* The original error is still rethrown by the caller. */ }
}
const panel = (page, title) => page.getByRole('heading', { name: title, exact: true }).locator('..')
// React18 controlled textarea.defaultValue becomes descendant text, and select
// options are also descendant text. Playwright1.64 strict getByLabel includes it.
// Keep real public label association/copy checks, then use a stable scoped control.
async function labelledControl(scope, selector, expected) {
  check(['textarea', 'select'].includes(selector), 'FINITE_LABELLED_CONTROL_REQUIRED')
  const control = scope.locator('label').filter({ hasText: expected }).locator(':scope > ' + selector)
  await control.waitFor({ state: 'visible' })
  check(await control.count() === 1, 'UNIQUE_SCOPED_LABELLED_CONTROL_REQUIRED')
  check(await control.evaluate((field, labelText) => {
    const labels = [...field.labels ?? []], label = field.closest('label')
    if (labels.length !== 1 || labels[0] !== label || field.parentElement !== label) return false
    const copy = label.cloneNode(true)
    for (const child of copy.querySelectorAll('input,textarea,select')) child.remove()
    const normalize = text => text.replace(/\s+/g, ' ').trim()
    return normalize(copy.textContent ?? '') === normalize(labelText)
  }, expected), 'ACTUAL_ASSOCIATED_LABEL_COPY_REQUIRED')
  return control
}
async function inbox(page, copy, path = '/inbox') {
  await navigate(page, path)
  await page.locator('main').getByText(copy.inbox.channels_notice, { exact: true }).waitFor()
  await page.getByRole('navigation', { name: copy.inbox.inbox_sections, exact: true }).waitFor()
}
async function tab(page, copy, kind) {
  await button(page.getByRole('navigation', { name: copy.inbox.inbox_sections, exact: true }), copy.inbox.tabs[kind]).click()
}
function messagingResponse(response, path, method = 'GET') {
  const url = new URL(response.url())
  return url.origin === API && url.pathname === '/messaging' + path && response.request().method() === method
}
async function displayedThread(page, thread, data, copy) {
  await thread.getByText(translated(copy.inbox.entry_count, { count: data.total }), { exact: true }).waitFor()
  const bodies = data.items.slice().reverse().map(x => x.body)
  await page.waitForFunction(expected => [...document.querySelectorAll('main ol > li > p:nth-child(3)')].map(x => x.textContent).join('\u0001') === expected,
    bodies.join('\u0001'))
  check((await thread.locator('ol > li > p:nth-child(3)').allTextContents()).join('\u0001') === bodies.join('\u0001'), 'REAL_THREAD_ORDER_AND_TEXT_REQUIRED')
}
async function threadAt(page, copy, id, discard = false) {
  await navigate(page, '/inbox?conversation=' + id, discard)
  const thread = panel(page, copy.inbox.thread)
  await thread.getByText(copy.inbox.sources.native, { exact: true }).waitFor()
  await thread.locator('ol > li').first().waitFor()
  return thread
}
function recordCommand(actor, receipt) {
  check(receipt && Object.keys(receipt).sort().join(',') === 'action,submission_id,target_id' && uuid.test(receipt.submission_id) && uuid.test(receipt.target_id), 'MINIMAL_REAL_RECEIPT_REQUIRED')
  const key = actor + ':' + receipt.submission_id
  check(!commands.has(key), 'NO_DUPLICATE_BROWSER_COMMAND_REQUIRED'); commands.set(key, receipt)
}
async function write(page, copy, actor, action, invoke) {
  const [response] = await Promise.all([page.waitForResponse(r => r.url().startsWith(API + '/messaging/') && isMutation(r.request())), invoke()])
  check([200, 201].includes(response.status()) && noStore(response), 'REAL_PRIVATE_COMMAND_SUCCESS_REQUIRED')
  const receipt = await response.json(); check(receipt.action === action, 'EXACT_COMMAND_ACTION_REQUIRED')
  recordCommand(actor, receipt)
  if (action !== 'conversation_opened') await page.locator('main').getByText(copy.inbox.saved, { exact: true }).first().waitFor()
  return receipt
}
async function post(page, copy, actor, content) {
  const thread = panel(page, copy.inbox.thread)
  await (await labelledControl(thread, 'select', copy.inbox.entry_kind)).selectOption('message')
  await (await labelledControl(thread, 'textarea', copy.inbox.message_body)).fill(content)
  await write(page, copy, actor, 'entry_created', () => button(thread, copy.inbox.post_message).click())
  await thread.getByText(content, { exact: true }).waitFor()
  check(await thread.locator('img').count() === 0, 'PLAIN_TEXT_NOT_HTML_REQUIRED')
}
const complete = (locale, name) => journeys.push(locale + '_' + name)

async function boundaries(byActor, anonymous) {
  await read(anonymous, '/messaging/policy', 401); boundaryChecks++
  const buyers = await read(byActor.buyer, '/messaging/recipients?q=&page=1')
  check(buyers.total === 2 && buyers.items.map(x => x.id).sort().join(',') === [facts.ids.admin, facts.ids.agent].sort().join(','), 'CURRENT_BUYER_ROUTING_ONLY')
  check(buyers.items.every(x => Object.keys(x).sort().join(',') === 'display_name,id,relationship,role,user_code'), 'MINIMAL_RECIPIENT_FIELDS_REQUIRED'); boundaryChecks++
  const owners = await read(byActor.owner, '/messaging/recipients?q=&page=1')
  check(owners.items.some(x => x.id === facts.ids.member) && !owners.items.some(x => x.id === facts.ids.private_member)
    && !JSON.stringify(owners).includes('@') && !JSON.stringify(owners).includes(facts.privateMarker), 'MEMBER_DISCOVERY_AND_PRIVACY_REQUIRED'); boundaryChecks++
  const agents = await read(byActor.agent, '/messaging/recipients?q=&page=1')
  check(agents.items.some(x => x.id === facts.ids.buyer && x.relationship === 'assigned_buyer'), 'ACTUAL_ASSIGNED_BUYER_DISCOVERY_REQUIRED'); boundaryChecks++
  await read(byActor.buyer, '/messaging/admin/legacy', 404); boundaryChecks++
  await read(byActor.owner, '/messaging/conversations?view=oversight&page=1', 404); boundaryChecks++
  await read(byActor.private_member, '/messaging/conversations/' + facts.buyerThread, 404); boundaryChecks++
  await read(byActor.new_agent, '/messaging/conversations/' + facts.buyerThread, 404); boundaryChecks++
}
async function chooseRecipient(page, copy, actor, target) {
  await tab(page, copy, 'new')
  const directory = panel(page, copy.inbox.new_conversation)
  await directory.getByLabel(copy.inbox.find_member, { exact: true }).fill(facts.names[target])
  await button(directory, copy.inbox.search).click()
  const row = directory.locator('li').filter({ has: page.getByText(facts.names[target], { exact: true }) })
  await button(row, copy.inbox.select_member).click()
  const receipt = await write(page, copy, actor, 'conversation_opened', () => button(directory, copy.inbox.start_conversation).click())
  await panel(page, copy.inbox.thread).waitFor()
  check(new URL(page.url()).searchParams.get('conversation') === receipt.target_id, 'ACTUAL_DIRECT_PAIR_NAVIGATION_REQUIRED')
  return receipt
}
async function manual(page, copy, actor, kind, body, locale) {
  const thread = panel(page, copy.inbox.thread)
  await (await labelledControl(thread, 'select', copy.inbox.entry_kind)).selectOption(kind)
  await (await labelledControl(thread, 'textarea', copy.inbox.manual_body)).fill(body)
  const at = await page.evaluate(() => {
    const d = new Date(Date.now() - 60000), pad = value => String(value).padStart(2, '0')
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  })
  await thread.getByLabel(copy.inbox.occurred_local, { exact: true }).fill(at)
  await (await labelledControl(thread, 'select', copy.inbox.reported_direction)).selectOption('outgoing')
  if (kind === 'manual_call') await (await labelledControl(thread, 'select', copy.inbox.reported_outcome)).selectOption('no_answer')
  check(await button(thread, copy.inbox.save_manual).isDisabled(), 'MANUAL_CONFIRMATION_REQUIRED')
  await thread.getByLabel(copy.inbox.manual_confirm, { exact: true }).check()
  if (kind === 'manual_call') await geometry(page, locale + '_manual')
  await write(page, copy, actor, 'entry_created', () => button(thread, copy.inbox.save_manual).click())
  const entry = thread.locator('ol > li').filter({ has: page.getByText(body, { exact: true }) })
  await entry.getByText(copy.inbox.manual_record_notice, { exact: true }).waitFor()
  if (kind === 'manual_call') check((await entry.innerText()).includes(copy.inbox.outcomes.no_answer), 'REPORTED_CALL_OUTCOME_REQUIRED')
}
async function routeFetchFailureCensus(error, request, page, locale, path, operation = 'fetch', response = null, receiptPhase = 'not_checked', receiptFailure = 'none') {
  const result = { stage: stage, locale, operation, error_name: 'OtherError', transport_reason: 'other',
    response_status: 0, no_store: false, has_dispose: false, receipt_phase: receiptPhase, receipt_failure: receiptFailure,
    method_post: false, path_matches: false, origin_matches: false, json_content_type: false,
    cookie_observed: false, cookie_present: false }
  let deadline
  try {
    const name = typeof error?.name === 'string' ? error.name : ''
    if (['Error', 'TypeError', 'SyntaxError', 'TimeoutError', 'TargetClosedError', 'AbortError'].includes(name)) result.error_name = name
    // Classify internally only. Never emit an exception message, URL, header,
    // request/response body, cookie value, account or submission identifier.
    const message = typeof error?.message === 'string' ? error.message : ''
    const reasons = [
      ['connection_reset', /ECONNRESET|socket hang up/i], ['connection_refused', /ECONNREFUSED/i],
      ['timeout', /ETIMEDOUT|\btimeout\b|timed out/i], ['name_resolution', /ENOTFOUND|EAI_AGAIN/i],
      ['invalid_header', /ERR_INVALID_HTTP_TOKEN|invalid (?:character in header|header|http token)/i],
      ['closed', /target.*closed|request context disposed|context.*closed|browser.*closed/i],
      ['tls_error', /EPROTO|certificate|\bTLS\b|\bSSL\b/i], ['redirect_error', /redirect/i],
    ]
    result.transport_reason = reasons.find(([, pattern]) => pattern.test(message))?.[0] ?? 'other'
    if (response !== null) {
      const status = response.status()
      result.response_status = Number.isInteger(status) && status >= 100 && status <= 599 ? status : 0
      result.no_store = noStore(response)
      result.has_dispose = typeof response.dispose === 'function'
    }
    const headers = request.headers()
    result.method_post = request.method() === 'POST'
    result.path_matches = request.url() === path
    result.origin_matches = headers.origin === DASH
    result.json_content_type = /^application\/json(?:\s*;|$)/i.test(headers['content-type'] ?? '')
    const cookies = await Promise.race([
      page.context().cookies(API),
      new Promise((_, reject) => { deadline = setTimeout(() => reject(new Failure('FETCH_CENSUS_DEADLINE')), 2000) }),
    ])
    result.cookie_observed = true
    result.cookie_present = cookies.some(cookie => cookie.name === 'ildr_token')
  } catch { /* A failed local observation never changes the original rejection. */ }
  finally { clearTimeout(deadline) }
  try { writeSync(1, 'INBOX_BROWSER_FETCH_CENSUS ' + JSON.stringify(result) + '\n') }
  catch { /* The original rejection is still rethrown. */ }
}
async function uncertain(page, context, copy, locale) {
  advance(locale + '_uncertain_committed_response')
  const thread = panel(page, copy.inbox.thread), path = API + '/messaging/conversations/' + facts.buyerThread + '/entries'
  await (await labelledControl(thread, 'select', copy.inbox.entry_kind)).selectOption('message')
  await (await labelledControl(thread, 'textarea', copy.inbox.message_body)).fill(facts.messages[locale].uncertain)
  let held = false, actualReceipt, committedAndAbortedReady
  const committedAndAborted = new Promise(resolve => { committedAndAbortedReady = resolve })
  const handler = async route => {
    const request = route.request()
    if (request.method() !== 'POST' || held) return route.continue()
    check(request.postDataJSON().body === facts.messages[locale].uncertain, 'EXACT_UNCERTAIN_BODY_REQUIRED')
    held = true
    let response
    try { response = await route.fetch({ timeout: 15000, maxRetries: 0, maxRedirects: 0 }) }
    catch (error) { await routeFetchFailureCensus(error, request, page, locale, path); throw error }
    let receiptPhase = 'not_checked', receiptFailure = 'none'
    try {
      check(response.status() === 201 && noStore(response), 'ACTUAL_COMMITTED_ENTRY_REQUIRED')
      receiptPhase = 'status_checked'
      actualReceipt = await response.json()
      receiptPhase = 'json_parsed'
    } catch (error) {
      receiptFailure = error instanceof Failure && error.message === 'ACTUAL_COMMITTED_ENTRY_REQUIRED' ? 'commit_required'
        : error?.name === 'SyntaxError' ? 'json_parse' : 'other'
      await routeFetchFailureCensus(error, request, page, locale, path, 'receipt', response, receiptPhase, receiptFailure)
      throw error
    } finally {
      try { await response.dispose() }
      catch (error) { await routeFetchFailureCensus(error, request, page, locale, path, 'dispose', response, receiptPhase, receiptFailure); throw error }
    }
    recordCommand('buyer', actualReceipt)
    await route.abort('failed')
    committedAndAbortedReady()
  }
  await page.route(path, handler)
  const before = methods.get(context).filter(x => x.path.endsWith('/entries')).length
  try {
    await button(thread, copy.inbox.post_message).click()
    // Visibility alone includes the initial busy attempt. Observe the actual
    // owned commit+abort, then the normal enabled resolver before assertions.
    await committedAndAborted
    await button(thread, copy.inbox.check_result).waitFor()
    await page.waitForFunction(label => {
      const matches = [...document.querySelectorAll('main button')].filter(node => node.textContent?.trim() === label)
      return matches.length === 1 && !matches[0].matches(':disabled')
    }, copy.inbox.check_result)
    check(held && actualReceipt?.action === 'entry_created' && await (await labelledControl(thread, 'textarea', copy.inbox.message_body)).inputValue() === facts.messages[locale].uncertain,
      'FROZEN_ORIGINAL_ATTEMPT_REQUIRED')
    check(await button(thread, copy.inbox.post_message).isDisabled(), 'PENDING_ATTEMPT_LOCKS_SEND')
    const [response] = await Promise.all([page.waitForResponse(r => messagingResponse(r, '/commands/' + actualReceipt.submission_id)), button(thread, copy.inbox.check_result).click()])
    check(response.status() === 200 && (await response.json()).target_id === actualReceipt.target_id, 'REAL_RECEIPT_RESOLUTION_REQUIRED')
    await thread.getByText(facts.messages[locale].uncertain, { exact: true }).waitFor()
    await (await labelledControl(thread, 'textarea', copy.inbox.message_body)).waitFor()
    await page.waitForFunction(() => document.querySelector('main textarea')?.value === '')
    check(methods.get(context).filter(x => x.path.endsWith('/entries')).length === before + 1, 'NO_AUTOMATIC_OR_CHECK_RESEND_REQUIRED')
  } finally { await page.unroute(path, handler) }
}
async function denial(page, context, admin, byActor, copy, locale, action, restore, expected) {
  advance(locale + '_' + action)
  const thread = await threadAt(page, copy, facts.buyerThread)
  await (await labelledControl(thread, 'select', copy.inbox.entry_kind)).selectOption('message')
  await (await labelledControl(thread, 'textarea', copy.inbox.message_body)).fill(facts.messages[locale].denied)
  await button(thread, copy.inbox.post_message).waitFor({ state: 'visible' })
  await page.waitForLoadState('networkidle')
  check(await button(thread, copy.inbox.post_message).isEnabled(), 'CURRENT_AFFORDANCE_BEFORE_ACCESS_CHANGE_REQUIRED')
  let release
  const gate = new Promise(resolve => { release = resolve }), held = []
  const handler = async route => {
    if (route.request().method() !== 'GET') return route.continue()
    held.push(route); await gate
    try { await route.abort('blockedbyclient') } catch { /* Page teardown is still a failure unless main assertions completed. */ }
  }
  await page.route(API + '/messaging/**', handler)
  try {
    await control(admin, action)
    if (action === 'assignment_new') {
      await read(byActor.new_agent, '/messaging/conversations/' + facts.buyerThread, 404)
      const retained = await read(byActor.buyer, '/messaging/conversations/' + facts.buyerThread)
      check(retained.read_only_reason === 'assignment_changed' && retained.can_send === false, 'BUYER_RETAINS_HISTORY_NEW_AGENT_GETS_NO_OLD_BODIES')
    }
    const [response] = await Promise.all([page.waitForResponse(r => messagingResponse(r, '/conversations/' + facts.buyerThread + '/entries', 'POST')),
      button(thread, copy.inbox.post_message).click()])
    check(response.status() === expected && noStore(response), 'REAL_CURRENT_AUTHORIZATION_DENIAL_REQUIRED'); deniedPosts++
    await thread.getByText(copy.inbox.errors.error_access, { exact: true }).waitFor()
    check(await thread.locator('ol > li,textarea,form').count() === 0 && await thread.getByText(facts.messages[locale].denied, { exact: true }).count() === 0,
      'DENIED_MUTATION_HIDES_CACHED_BODY_AND_DRAFT_BEFORE_READ')
    await control(admin, restore)
  } finally {
    release(); await page.unroute(API + '/messaging/**', handler)
  }
  await navigate(page, '/inbox', true)
}

async function runLocale(locale, copy) {
  advance(locale + '_accounts')
  const byActor = {}
  for (const actor of Object.keys(facts.roles)) byActor[actor] = await contextFor(locale, actor)
  const anonymous = await contextFor(locale)
  const pages = {}
  for (const actor of ['buyer', 'agent', 'admin', 'owner', 'member']) pages[actor] = await byActor[actor].newPage()
  await boundaries(byActor, anonymous); complete(locale, 'boundaries_and_routing')
  const buyer = pages.buyer, owner = pages.owner, admin = pages.admin

  advance(locale + '_new_direct')
  await inbox(buyer, copy); await geometry(buyer, locale + '_mine')
  await tab(buyer, copy, 'new'); await geometry(buyer, locale + '_directory')
  await chooseRecipient(buyer, copy, 'buyer', 'admin'); complete(locale, 'new_direct_conversation')

  advance(locale + '_native_cursor')
  let thread = await threadAt(buyer, copy, facts.buyerThread)
  const first = await read(byActor.buyer, '/messaging/conversations/' + facts.buyerThread + '/entries')
  const expectedSequence = locale === 'en' ? 35 : 40
  check(first.total === expectedSequence && first.items.length === 30 && first.next_cursor !== null, 'REAL_NATIVE_HISTORY_PAGE_REQUIRED')
  await displayedThread(buyer, thread, first, copy)
  const unread = await read(byActor.buyer, '/messaging/unread')
  check(unread.entries === (locale === 'en' ? 18 : 2), 'OPENING_HISTORY_DOES_NOT_MARK_READ')
  const [olderResponse] = await Promise.all([buyer.waitForResponse(r => messagingResponse(r, '/conversations/' + facts.buyerThread + '/entries') && new URL(r.url()).searchParams.get('cursor') === first.next_cursor), button(thread, copy.inbox.older).click()])
  const older = await olderResponse.json()
  check(olderResponse.status() === 200 && older.cursor === first.next_cursor && older.items.length === expectedSequence - 30
    && !older.items.some(x => first.items.some(y => x.id === y.id)), 'REAL_KEYSET_BOUNDARY_REQUIRED')
  await displayedThread(buyer, thread, older, copy)
  const [newerResponse] = await Promise.all([buyer.waitForResponse(r => messagingResponse(r, '/conversations/' + facts.buyerThread + '/entries') && !new URL(r.url()).searchParams.has('cursor')), button(thread, copy.inbox.newer).click()])
  const newer = await newerResponse.json()
  check(newerResponse.status() === 200 && newer.total === expectedSequence && newer.items.map(x => x.id).join(',') === first.items.map(x => x.id).join(','), 'REAL_NEWER_RETURNS_LATEST_AFTER_PRIOR_LOCALE_ARRIVAL')
  await displayedThread(buyer, thread, newer, copy)
  const [olderAgain] = await Promise.all([buyer.waitForResponse(r => messagingResponse(r, '/conversations/' + facts.buyerThread + '/entries') && new URL(r.url()).searchParams.get('cursor') === first.next_cursor), button(thread, copy.inbox.older).click()])
  await displayedThread(buyer, thread, await olderAgain.json(), copy)
  const [latestResponse] = await Promise.all([buyer.waitForResponse(r => messagingResponse(r, '/conversations/' + facts.buyerThread + '/entries') && !new URL(r.url()).searchParams.has('cursor')), button(thread, copy.inbox.latest).click()])
  await displayedThread(buyer, thread, await latestResponse.json(), copy)
  await geometry(buyer, locale + '_thread')
  const bodyLimit = await labelledControl(thread, 'textarea', copy.inbox.message_body)
  try {
    advance(locale + '_codepoint_ascii_fill')
    await bodyLimit.fill('x'.repeat(2001))
    advance(locale + '_codepoint_ascii_disabled')
    check(await button(thread, copy.inbox.post_message).isDisabled(), 'OVER_CODEPOINT_LIMIT_BLOCKS_SUBMIT')
    advance(locale + '_codepoint_astral_fill')
    await bodyLimit.fill('🏡'.repeat(2000))
    advance(locale + '_codepoint_astral_trial')
    await button(thread, copy.inbox.post_message).click({ trial: true })
    advance(locale + '_codepoint_astral_enabled')
    check(await button(thread, copy.inbox.post_message).isEnabled() && [...await bodyLimit.inputValue()].length === 2000, 'CODEPOINT_BOUNDARY_ACCEPTS_2000_ASTRAL_CHARACTERS')
    advance(locale + '_codepoint_clear')
    await bodyLimit.fill('')
  } catch (error) {
    await inputFailureCensus(buyer, copy, locale)
    throw error
  }
  await write(buyer, copy, 'buyer', 'read_marked', () => button(thread, copy.inbox.mark_shown_read).click())
  check((await read(byActor.buyer, '/messaging/unread')).entries === 0, 'EXPLICIT_READ_COMMAND_REQUIRED')
  complete(locale, 'native_keyset_read_cursor')

  advance(locale + '_entries')
  const [parkedResponse] = await Promise.all([buyer.waitForResponse(r => messagingResponse(r, '/conversations/' + facts.buyerThread + '/entries') && new URL(r.url()).searchParams.get('cursor') === first.next_cursor), button(thread, copy.inbox.older).click()])
  const parked = await parkedResponse.json()
  await displayedThread(buyer, thread, parked, copy)
  await threadAt(pages.agent, copy, facts.buyerThread)
  await post(pages.agent, copy, 'agent', facts.messages[locale].message)
  // Source read loop polls every30s with a15s request bound; focus may not emit in headless contexts.
  const incomingWhileOlder = buyer.waitForResponse(r => messagingResponse(r, '/conversations/' + facts.buyerThread + '/entries') && new URL(r.url()).searchParams.get('cursor') === first.next_cursor, { timeout: 45000 })
  await foreground(buyer)
  const arrivalResponse = await incomingWhileOlder, arrival = await arrivalResponse.json()
  check(arrivalResponse.status() === 200 && arrival.total === expectedSequence + 1 && arrival.cursor === first.next_cursor
    && arrival.items.map(x => x.id).join(',') === parked.items.map(x => x.id).join(','), 'REAL_NEW_ARRIVAL_RETAINS_MOUNTED_OLDER_CURSOR_AND_IDS')
  await displayedThread(buyer, thread, arrival, copy)
  const [arrivalLatest] = await Promise.all([buyer.waitForResponse(r => messagingResponse(r, '/conversations/' + facts.buyerThread + '/entries') && !new URL(r.url()).searchParams.has('cursor')), button(thread, copy.inbox.newer).click()])
  await displayedThread(buyer, thread, await arrivalLatest.json(), copy)
  await thread.getByText(facts.messages[locale].message, { exact: true }).waitFor()
  await manual(buyer, copy, 'buyer', 'manual_whatsapp', facts.messages[locale].manual_whatsapp, locale)
  await manual(buyer, copy, 'buyer', 'manual_call', facts.messages[locale].manual_call, locale)
  complete(locale, 'website_and_manual_entries')
  await uncertain(buyer, byActor.buyer, copy, locale); complete(locale, 'uncertain_result_resolution')

  advance(locale + '_preferences')
  await inbox(owner, copy); await tab(owner, copy, 'preferences')
  const prefs = panel(owner, copy.inbox.preferences), discovery = prefs.getByLabel(copy.inbox.allow_discovery, { exact: true })
  await discovery.waitFor(); check(await discovery.isChecked(), 'ACTUAL_MEMBER_DISCOVERY_BASELINE_REQUIRED')
  await discovery.uncheck()
  let canceled = false
  const dismiss = async dialog => { check(dialog.type() === 'confirm', 'ACTUAL_DIRTY_CONFIRM_REQUIRED'); canceled = true; await dialog.dismiss() }
  owner.once('dialog', dismiss); await button(owner.getByRole('navigation', { name: copy.inbox.inbox_sections, exact: true }), copy.inbox.tabs.mine).click()
  check(canceled && await discovery.isVisible() && !await discovery.isChecked(), 'DIRTY_NAVIGATION_CANCEL_RETAINS_DRAFT')
  await geometry(owner, locale + '_preferences')
  await write(owner, copy, 'owner', 'visibility_saved', () => button(prefs, copy.inbox.save_preferences).click())
  await discovery.check(); await write(owner, copy, 'owner', 'visibility_saved', () => button(prefs, copy.inbox.save_preferences).click())
  complete(locale, 'member_preferences_guard')

  advance(locale + '_block')
  const ownerThread = await threadAt(owner, copy, facts.ownerThread)
  await write(owner, copy, 'owner', 'block_saved', () => button(ownerThread, copy.inbox.block_member).click())
  await ownerThread.getByText(copy.inbox.reasons.blocked, { exact: true }).waitFor()
  check(await ownerThread.locator('textarea').isDisabled(), 'BLOCK_RETAINS_HISTORY_AND_DISABLES_POSTING')
  await inbox(owner, copy); await tab(owner, copy, 'blocks')
  const blocks = panel(owner, copy.inbox.blocks)
  await blocks.getByText(facts.names.member, { exact: true }).waitFor(); await geometry(owner, locale + '_blocks')
  await write(owner, copy, 'owner', 'block_saved', () => button(blocks, copy.inbox.unblock).click())
  await blocks.getByText(copy.inbox.empty_blocks, { exact: true }).waitFor(); complete(locale, 'block_and_unblock')

  await denial(pages.agent, byActor.agent, byActor.admin, byActor, copy, locale, 'assignment_new', 'assignment_restore', 404)
  await denial(pages.agent, byActor.agent, byActor.admin, byActor, copy, locale, 'agent_unverified', 'agent_verify', 403)
  complete(locale, 'current_access_revocation')

  advance(locale + '_admin_native')
  await inbox(admin, copy); await tab(admin, copy, 'oversight')
  const oversight = panel(admin, copy.inbox.oversight)
  const row = oversight.locator('article').filter({ has: admin.getByText(facts.names.agent, { exact: true }) })
  await button(row, copy.inbox.choose_review).waitFor()
  check(!(await oversight.innerText()).includes(facts.messages[locale].message), 'UNAUDITED_OVERSIGHT_HAS_NO_PREVIEW')
  await button(row, copy.inbox.choose_review).click()
  const audit = panel(admin, copy.inbox.audited_review)
  const initialReview = await write(admin, copy, 'admin', 'review_opened', () => button(audit, copy.inbox.open_review).click())
  await geometry(admin, locale + '_admin')
  await post(admin, copy, 'admin', facts.messages[locale].admin)
  const adminEntry = panel(admin, copy.inbox.thread).locator('ol > li').filter({ has: admin.getByText(facts.messages[locale].admin, { exact: true }) })
  check((await adminEntry.innerText()).includes(facts.names.admin) && (await adminEntry.innerText()).includes(copy.inbox.roles.admin), 'ADMIN_INTERVENTION_IDENTIFIES_REAL_ADMIN')
  complete(locale, 'admin_native_audit')

  advance(locale + '_expiry')
  const reviewedThread = panel(admin, copy.inbox.thread)
  const [reviewOlderResponse] = await Promise.all([admin.waitForResponse(r => messagingResponse(r, '/admin/reviews/' + initialReview.target_id + '/entries') && new URL(r.url()).searchParams.has('cursor')), button(reviewedThread, copy.inbox.older).click()])
  const reviewedOlder = await reviewOlderResponse.json()
  check(reviewOlderResponse.status() === 200 && reviewedOlder.cursor !== null && reviewedOlder.items.length > 0, 'ACTUAL_REVIEW_OLDER_CURSOR_REQUIRED')
  await displayedThread(admin, reviewedThread, reviewedOlder, copy)
  await control(byActor.admin, 'clock_future')
  const [expired] = await Promise.all([admin.waitForResponse(r => new URL(r.url()).pathname.includes('/messaging/admin/reviews/') && r.status() === 410), button(panel(admin, copy.inbox.thread), copy.inbox.refresh).click()])
  check(expired.status() === 410, 'REAL_REVIEW_EXPIRY_REQUIRED')
  await panel(admin, copy.inbox.thread).getByText(copy.inbox.review_expired, { exact: true }).first().waitFor()
  check(await panel(admin, copy.inbox.thread).locator('ol > li,textarea').count() === 0, 'EXPIRED_REVIEW_HIDES_RETAINED_BODY')
  await control(byActor.admin, 'clock_reset')
  const renewedOlderReply = admin.waitForResponse(r => new URL(r.url()).origin === API && new URL(r.url()).pathname.startsWith('/messaging/admin/reviews/') && new URL(r.url()).searchParams.get('cursor') === reviewedOlder.cursor && r.status() === 200)
  const renewed = await write(admin, copy, 'admin', 'review_opened', () => button(audit, copy.inbox.renew_review).click())
  const renewedResponse = await renewedOlderReply, renewedPage = await renewedResponse.json()
  check(new URL(renewedResponse.url()).pathname === '/messaging/admin/reviews/' + renewed.target_id + '/entries'
    && renewed.target_id !== initialReview.target_id && renewedPage.cursor === reviewedOlder.cursor
    && renewedPage.items.map(x => x.id).join(',') === reviewedOlder.items.map(x => x.id).join(','), 'FRESH_REVIEW_RENEWS_SAME_OLDER_CURSOR_AND_ORDER')
  await displayedThread(admin, reviewedThread, renewedPage, copy)
  complete(locale, 'review_expiry_and_renewal')

  advance(locale + '_legacy')
  for (const kind of ['service_award', 'service_negotiation']) {
    await inbox(admin, copy); await tab(admin, copy, 'legacy')
    const legacy = panel(admin, copy.inbox.legacy), item = legacy.locator('li').filter({ has: admin.getByRole('heading', { name: copy.inbox.sources[kind], exact: true }) })
    await button(item, copy.inbox.choose_review).waitFor()
    check(!(await legacy.innerText()).includes('Synthetic awarded history') && !(await legacy.innerText()).includes('Synthetic negotiation history'), 'UNAUDITED_LEGACY_METADATA_ONLY')
    await button(item, copy.inbox.choose_review).click()
    const auditPanel = panel(admin, copy.inbox.audited_review)
    await write(admin, copy, 'admin', 'review_opened', () => button(auditPanel, copy.inbox.open_review).click())
    const legacyThread = panel(admin, copy.inbox.thread)
    await legacyThread.getByText(copy.inbox.legacy_read_only, { exact: true }).waitFor()
    check(await legacyThread.locator('form,textarea').count() === 0 && await legacyThread.locator('ol > li').count() === 30, 'LEGACY_REVIEW_IS_READONLY')
    const latestBodies = await legacyThread.locator('ol > li > p:nth-child(3)').allTextContents()
    const [olderReply] = await Promise.all([admin.waitForResponse(r => new URL(r.url()).pathname.includes('/messaging/admin/reviews/') && new URL(r.url()).searchParams.has('cursor')), button(legacyThread, copy.inbox.older).click()])
    const old = await olderReply.json(); check(old.total === 35 && old.items.length === 5 && !old.items.some(x => latestBodies.includes(x.body)), 'LEGACY_TIE_KEYSET_NO_OVERLAP_REQUIRED')
    await displayedThread(admin, legacyThread, old, copy)
    if (kind === 'service_negotiation') await geometry(admin, locale + '_legacy')
  }
  complete(locale, 'legacy_readonly_keysets')

  advance(locale + '_member_routes')
  await inbox(pages.member, copy)
  const beforeUnread = await read(byActor.member, '/messaging/unread')
  await chooseRecipient(pages.member, copy, 'member', 'owner')
  check((await read(byActor.member, '/messaging/unread')).entries === beforeUnread.entries, 'MEMBER_OPEN_DOES_NOT_IMPLICITLY_MARK_READ')
  await inbox(owner, copy, '/inbox?workspace=admin')
  check(await button(owner, copy.inbox.tabs.oversight).count() === 0 && await button(owner, copy.inbox.tabs.legacy).count() === 0, 'FORGED_WORKSPACE_GRANTS_NO_ADMIN_TABS')
  await navigate(owner, '/inbox?conversation=invalid')
  await owner.getByText(copy.inbox.invalid_link, { exact: true }).waitFor()
  check(await owner.locator('main textarea').count() === 0, 'INVALID_LINK_SHOWS_NO_CACHED_THREAD')
  complete(locale, 'member_and_error_route')
}

async function run() {
  const filename = process.env.ILDR_BROWSER_FIXTURES
  check(filename === '/browser-fixtures/fixtures.json' && process.platform === 'linux' && process.env.GITHUB_ACTIONS === 'true'
    && process.env.GITHUB_REPOSITORY === 'ilovedrrealty/realty-api', 'OWNED_HOSTED_FIXTURE_REQUIRED')
  const info = await lstat(filename)
  check(info.isFile() && !info.isSymbolicLink() && info.size > 0 && info.size <= 32768 && (info.mode & 0o777) === 0o600, 'PRIVATE_FIXTURE_FILE_REQUIRED')
  const input = JSON.parse(await readFile(filename, 'utf8'))
  check(input.scope === 'native-inbox-only' && Object.keys(input.accounts).length === 7, 'EXACT_INBOX_FIXTURE_SCOPE_REQUIRED')
  accounts = input.accounts; facts = input.facts
  browser = await chromium.launch({ headless: true, args: ['--disable-background-networking', '--disable-component-update', '--no-first-run'] })
  try {
    for (const locale of ['en', 'es']) await runLocale(locale, JSON.parse(await readFile(`/browser-run/${locale}.json`, 'utf8')))
    advance('final_checks')
    check(journeys.length === 24 && new Set(journeys).size === 24 && geometries.length === 32 && presenceCount === 10
      && contexts.length === 16 && actualLogins === 14 && boundaryChecks === 16 && commands.size === 32 && deniedPosts === 4 && controlTransitions === 12, 'EXACT_FINITE_BROWSER_COUNTS_REQUIRED')
    const totals = {}
    for (const receipt of commands.values()) totals[receipt.action] = (totals[receipt.action] || 0) + 1
    check(JSON.stringify(Object.entries(totals).sort()) === JSON.stringify(Object.entries({ conversation_opened: 4, entry_created: 10, read_marked: 2, visibility_saved: 4, block_saved: 4, review_opened: 8 }).sort()), 'EXACT_BROWSER_COMMAND_CATEGORIES_REQUIRED')
    check(pageErrors === 0 && externalWrites === 0 && unexpectedMutations === 0 && trackingRequests === 0, 'NO_BROWSER_ERRORS_OR_EXTERNAL_WRITES')
    console.log('INBOX_BROWSER_RESULT ' + JSON.stringify({ status: 'PASS', locales: ['en', 'es'], journeys: 24, geometry: 32,
      api_boundary_checks: 16, successful_commands: 32, denied_entry_posts: 4, control_transitions: 12, contexts: 16, actual_logins: 14,
      page_errors: pageErrors, unexpected_mutations: unexpectedMutations, external_writes: externalWrites, tracking_requests: trackingRequests, blocked_external_requests: blockedExternal }))
  } catch (error) {
    // Record the awaited cause before closing contexts can reject pending routes
    // and race run().catch(fail) through the existing unhandled-rejection hook.
    mainFlowFailure(error)
    throw error
  } finally {
    clearTimeout(timer)
    for (const context of contexts.reverse()) await context.close()
    await browser.close()
  }
}
run().catch(fail)
