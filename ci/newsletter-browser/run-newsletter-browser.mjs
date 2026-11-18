import assert from 'node:assert/strict'
import { readFile, readdir, stat } from 'node:fs/promises'
import { chromium } from 'playwright-core'

const ORIGIN = 'http://127.0.0.1:8917'
const API = 'http://127.0.0.1:8918'
const TOKEN = /[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\.[0-9a-f]{64}/
const emails = { en: 'newsletter-browser-en@example.com', es: 'newsletter-browser-es@example.com' }
const copy = {
  en: {
    email: 'Email address', consent: 'I agree to receive property updates and guides by email from I Love DR Realty. I can unsubscribe at any time.',
    submit: 'Request a free subscription', accepted: 'Request received. If your address needs confirmation, look for our email in your inbox or spam folder. Open the link and confirm to finish.',
    heading: 'Newsletter preferences', confirm: 'Confirm subscription', unsubscribe: 'Unsubscribe',
    subscribed: 'Your newsletter subscription is confirmed.', unsubscribed: 'You’re unsubscribed from the newsletter.',
    invalid: 'This link is invalid or has expired. Use the link from your most recent newsletter email.',
    working: 'Updating your preference…',
    unsubscribeNote: 'Confirm that you want to stop receiving the I Love DR Realty newsletter.',
  },
  es: {
    email: 'Correo electrónico', consent: 'Acepto recibir por correo electrónico novedades inmobiliarias y guías de I Love DR Realty. Puedo cancelar mi suscripción en cualquier momento.',
    submit: 'Solicitar una suscripción gratuita', accepted: 'Recibimos tu solicitud. Si tu dirección requiere confirmación, busca nuestro correo en tu bandeja de entrada o en correo no deseado. Abre el enlace y confirma para completar la suscripción.',
    heading: 'Preferencias del boletín', confirm: 'Confirmar suscripción', unsubscribe: 'Cancelar suscripción',
    subscribed: 'Confirmamos tu suscripción al boletín.', unsubscribed: 'Cancelamos tu suscripción al boletín.',
    invalid: 'Este enlace no es válido o ha vencido. Usa el enlace de tu correo más reciente del boletín.',
    working: 'Actualizando tu preferencia…',
    unsubscribeNote: 'Confirma que quieres dejar de recibir el boletín de I Love DR Realty.',
  },
}
const checks = []
const pageErrorEvidence = []
let stage = 'fixture'
let responseEvidence = null
let bodyError = 'none'
let browser
const contexts = []
const advance = name => { stage = name }

async function readJson(response) {
  let timer
  try {
    return await Promise.race([
      response.json(),
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(Object.assign(new Error('Body observer timed out'),
          { code: 'BODY_OBSERVER_TIMEOUT' })), 15000)
      }),
    ])
  } catch (error) {
    bodyError = error?.code === 'BODY_OBSERVER_TIMEOUT' ? 'timeout'
      : /Target.*closed|browser.*closed|context.*closed|page.*closed|has been closed/i.test(error?.message ?? '')
        ? 'context_closed'
        : /No resource with given identifier|not available|unavailable|No data found|could not load body/i.test(error?.message ?? '')
          ? 'unavailable' : 'other'
    throw error
  } finally {
    clearTimeout(timer)
  }
}

async function links() {
  const path = '/browser-fixtures/mail-links.json'
  const info = await stat(path)
  assert.equal(info.mode & 0o777, 0o600)
  assert.ok(info.isFile() && info.size <= 4096)
  const parsed = JSON.parse(await readFile(path, 'utf8'))
  assert.deepEqual(Object.keys(parsed), ['deliveries'])
  for (const [lang, delivery] of Object.entries(parsed.deliveries)) {
    assert.ok(lang in emails)
    assert.deepEqual(Object.keys(delivery).sort(), ['confirm', 'unsubscribe'])
    for (const [action, value] of Object.entries(delivery)) {
      const url = new URL(value)
      assert.equal(url.origin, ORIGIN)
      assert.equal(url.pathname, '/newsletter/')
      assert.equal(url.search, '')
      assert.ok(new RegExp(`^#${action}=`).test(url.hash))
      const token = url.hash.split('=')[1]
      assert.equal(token.length, 101)
      assert.ok(new RegExp(`^${TOKEN.source}$`).test(token))
    }
  }
  return parsed.deliveries
}

async function db(context) {
  const result = await context.request.get(API + '/__newsletter_fixture__/state', { timeout: 15000 })
  assert.equal(result.status(), 200)
  const value = await readJson(result)
  assert.deepEqual(Object.keys(value).sort(), ['customer_accounts', 'inquiries', 'leads', 'mail_count', 'rows'])
  assert.equal(value.customer_accounts, 0)
  assert.equal(value.leads, 0)
  assert.equal(value.inquiries, 0)
  return value
}

async function expectRow(context, lang, status, mailCount) {
  const current = await db(context)
  assert.equal(current.mail_count, mailCount)
  assert.deepEqual(current.rows[lang], {
    status, consent: true, confirmed: status !== 'pending', left: status === 'unsubscribed',
    provider_accepted: status !== 'unsubscribed',
  })
}

function safeHeaders(response, expectedCode) {
  const originalStage = stage
  advance(originalStage + '_response_status')
  const headers = response.headers()
  const policy = headers['referrer-policy']
  responseEvidence = {
    status: response.status(), cache: headers['cache-control'] === 'no-store' ? 'no-store' : 'other',
    referrer: policy === undefined ? 'missing'
      : ['no-referrer', 'strict-origin-when-cross-origin'].includes(policy) ? policy : 'other',
  }
  assert.equal(response.status(), expectedCode)
  advance(originalStage + '_response_cache')
  assert.equal(headers['cache-control'], 'no-store')
  advance(originalStage + '_response_referrer')
  assert.equal(headers['referrer-policy'], 'no-referrer')
  advance(originalStage + '_response_cookie')
  assert.equal(headers['set-cookie'], undefined)
  advance(originalStage)
}

async function safeResponse(response, expectedCode, expectedBody) {
  const originalStage = stage
  safeHeaders(response, expectedCode)
  advance(originalStage + '_response_body')
  assert.deepEqual(await readJson(response), expectedBody)
  advance(originalStage)
}

async function mobileBox(page, locator, minimumHeight = 44) {
  const box = await locator.boundingBox()
  assert.ok(box && box.width >= 35 && box.height >= minimumHeight && box.x >= 0 && box.x + box.width <= 391)
  assert.ok(await page.evaluate(() => window.innerWidth === 390 && navigator.maxTouchPoints > 0
    && document.documentElement.scrollWidth <= 391))
}

async function newMobile(lang, actionPage) {
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 }, screen: { width: 390, height: 844 },
    isMobile: true, hasTouch: true, deviceScaleFactor: 1, locale: lang === 'es' ? 'es-DO' : 'en-US',
    serviceWorkers: 'block', acceptDownloads: false,
  })
  contexts.push(context)
  await context.addInitScript(({ locale, origin }) => {
    if (window.location.origin === origin) localStorage.setItem('ildr_lang', locale)
  }, { locale: lang, origin: ORIGIN })
  const events = { posts: [], external: 0, localRefused: 0, errors: 0 }
  const hold = { armed: false }
  hold.ready = new Promise(resolve => { hold.capture = resolve })
  hold.released = new Promise(resolve => { hold.release = resolve })
  await context.route('**/*', async route => {
    const request = route.request()
    const url = new URL(request.url())
    assert.ok(!TOKEN.test(request.url()))
    const headers = await request.allHeaders()
    assert.ok(!TOKEN.test(headers.referer ?? ''))
    assert.equal(headers.cookie, undefined)
    assert.equal(headers.authorization, undefined)
    if (url.origin !== ORIGIN && url.origin !== API) {
      events.external += 1
      return route.abort('blockedbyclient')
    }
    if (url.origin === API && !['GET', 'HEAD', 'OPTIONS'].includes(request.method())) {
      if (!/^\/newsletter\/(subscribe|confirm|unsubscribe)$/.test(url.pathname)) {
        events.localRefused += 1
        return route.abort('blockedbyclient')
      }
      assert.equal(url.search, '')
      assert.equal(headers.referer, undefined)
      assert.equal(headers['content-type'], 'application/json')
      events.posts.push({ path: url.pathname, body: request.postDataJSON() })
      if (actionPage && hold.armed && url.pathname === '/newsletter/confirm') {
        hold.armed = false
        const response = await route.fetch({ maxRedirects: 0, timeout: 15000 })
        hold.capture(response)
        await hold.released
        return route.fulfill({ response })
      }
    }
    return route.continue()
  })
  const page = await context.newPage()
  page.setDefaultTimeout(10000)
  page.on('pageerror', error => {
    events.errors += 1
    if (pageErrorEvidence.length < 8) {
      const kind = ['SecurityError', 'TypeError', 'ReferenceError', 'SyntaxError', 'Error'].includes(error.name)
        ? error.name : 'OtherError'
      const document = page.url() === 'about:blank' ? 'blank'
        : page.url().startsWith(ORIGIN + '/') ? 'frontend' : 'other'
      const code = kind === 'SecurityError' && /localStorage|access is denied|access.*denied/i.test(error.message)
        ? 'storage_denied' : /Minified React error #(?:418|423|425)|hydration/i.test(error.message)
          ? 'hydration' : 'application'
      pageErrorEvidence.push({ kind, document, surface: actionPage ? 'action' : 'signup', code })
    }
  })
  return { context, page, events, actionPage, hold }
}

async function openAction(test, lang, url, action, status, mailCount) {
  const { page, context, events } = test
  const before = events.posts.length
  const sameDocument = page.url() === ORIGIN + '/newsletter/'
  const response = await page.goto(url, { waitUntil: 'networkidle' })
  if (sameDocument) assert.equal(response, null)
  else assert.equal(response.status(), 200)
  await page.getByRole('heading', { name: copy[lang].heading, exact: true }).waitFor()
  const button = page.getByRole('button', { name: copy[lang][action], exact: true })
  await button.waitFor()
  await page.waitForFunction(() => window.location.hash === '' && window.location.search === '')
  assert.ok(await button.isEnabled())
  assert.equal(page.url(), ORIGIN + '/newsletter/')
  assert.equal(events.posts.length, before)
  assert.equal(events.external, 0)
  assert.equal(events.localRefused, 0)
  assert.equal(events.errors, 0)
  assert.deepEqual(await context.cookies(), [])
  const storage = await page.evaluate(() => ({
    local: Object.fromEntries(Object.keys(localStorage).map(key => [key, localStorage.getItem(key)])),
    session: Object.fromEntries(Object.keys(sessionStorage).map(key => [key, sessionStorage.getItem(key)])),
  }))
  assert.deepEqual(storage, { local: { ildr_lang: lang }, session: {} })
  await mobileBox(page, button, 48)
  await mobileBox(page, page.getByRole('button', { name: 'English', exact: true }))
  await mobileBox(page, page.getByRole('button', { name: 'Español', exact: true }))
  await expectRow(context, lang, status, mailCount)
  return button
}

async function clickAction(test, lang, button, action, status, mailCount) {
  const before = test.events.posts.length
  const [response] = await Promise.all([
    test.page.waitForResponse(value => value.url() === API + '/newsletter/' + action
      && value.request().method() === 'POST'),
    button.click(),
  ])
  await safeResponse(response, 200, { status })
  await test.page.getByRole('status').filter({ hasText: copy[lang][status] }).waitFor()
  assert.equal(test.events.posts.length, before + 1)
  assert.deepEqual(Object.keys(test.events.posts.at(-1).body), ['token'])
  assert.ok(TOKEN.test(test.events.posts.at(-1).body.token))
  assert.equal(test.page.url(), ORIGIN + '/newsletter/')
  await expectRow(test.context, lang, status, mailCount)
}

try {
  assert.equal(process.platform, 'linux')
  assert.equal(process.env.GITHUB_ACTIONS, 'true')
  assert.equal(process.env.GITHUB_REPOSITORY, 'ilovedrrealty/realty-api')
  assert.deepEqual(await readdir('/sys/class/net'), ['lo'])
  assert.deepEqual(await links(), {})
  advance('browser_launch')
  browser = await chromium.launch({ headless: true, timeout: 20000,
    args: ['--no-sandbox', '--disable-dev-shm-usage'] })

  for (const [index, lang] of ['en', 'es'].entries()) {
    const mailCount = index + 1
    advance('signup_' + lang)
    const signup = await newMobile(lang, false)
    const { context, page, events } = signup
    const before = await db(context)
    assert.equal(before.mail_count, index)
    assert.equal(before.rows[lang], undefined)
    await page.goto(ORIGIN + '/blog/', { waitUntil: 'networkidle' })
    const input = page.getByRole('textbox', { name: copy[lang].email, exact: true })
    await input.fill(emails[lang])
    const form = input.locator('xpath=ancestor::form')
    const submit = form.getByRole('button', { name: copy[lang].submit, exact: true })
    const consent = form.getByRole('checkbox', { name: copy[lang].consent, exact: true })
    assert.equal(await consent.isChecked(), false)
    assert.equal(await submit.isDisabled(), true)
    await input.press('Enter')
    await page.waitForTimeout(180)
    assert.equal(events.posts.length, 0)
    assert.deepEqual(await db(context), before)
    const missing = await context.request.post(API + '/newsletter/subscribe', {
      data: { email: emails[lang], language: lang }, headers: { Origin: ORIGIN },
    })
    await safeResponse(missing, 400, { detail: 'Check your newsletter request' })
    assert.deepEqual(await db(context), before)
    await consent.check()
    assert.equal(await submit.isEnabled(), true)
    await submit.scrollIntoViewIfNeeded()
    await mobileBox(page, submit, 48)
    await mobileBox(page, input, 48)
    const [accepted] = await Promise.all([
      page.waitForResponse(value => value.url() === API + '/newsletter/subscribe'
        && value.request().method() === 'POST'),
      submit.click(),
    ])
    await safeResponse(accepted, 202, { status: 'accepted' })
    await form.getByRole('status').filter({ hasText: copy[lang].accepted }).waitFor()
    assert.deepEqual(events.posts, [{ path: '/newsletter/subscribe', body: { email: emails[lang], consent: true, language: lang } }])
    assert.equal(await input.inputValue(), '')
    assert.equal(await consent.isChecked(), false)
    await expectRow(context, lang, 'pending', mailCount)
    const repeated = await context.request.post(API + '/newsletter/subscribe', {
      data: { email: emails[lang], language: lang, consent: true }, headers: { Origin: ORIGIN },
    })
    await safeResponse(repeated, 202, { status: 'accepted' })
    await expectRow(context, lang, 'pending', mailCount)
    const delivery = (await links())[lang]
    assert.ok(delivery)
    checks.push('mobile_' + lang + '_explicit_consent_and_pending_state')
    checks.push('mobile_' + lang + '_same_request_no_duplicate_email')
    await context.close()

    const action = await newMobile(lang, true)
    advance('confirm_' + lang)
    const noActionGet = await action.context.request.get(API + '/newsletter/confirm')
    assert.equal(noActionGet.status(), 405)
    await expectRow(action.context, lang, 'pending', mailCount)
    const confirm = await openAction(action, lang, delivery.confirm, 'confirm', 'pending', mailCount)
    await clickAction(action, lang, confirm, 'confirm', 'subscribed', mailCount)
    const confirmAgain = await openAction(action, lang, delivery.confirm, 'confirm', 'subscribed', mailCount)
    action.hold.armed = true
    const repeatedResponse = action.page.waitForResponse(value => value.url() === API + '/newsletter/confirm'
      && value.request().method() === 'POST')
    await confirmAgain.click()
    let heldTimer
    let heldResponse
    try {
      heldResponse = await Promise.race([action.hold.ready,
        new Promise((_, reject) => { heldTimer = setTimeout(() => reject(new Error('Held response deadline')), 15000) })])
    } finally {
      clearTimeout(heldTimer)
    }
    await safeResponse(heldResponse, 200, { status: 'subscribed' })
    await expectRow(action.context, lang, 'subscribed', mailCount)
    const pendingPosts = action.events.posts.length
    assert.equal(pendingPosts, 2)
    const secondLink = await action.page.goto(delivery.unsubscribe, { waitUntil: 'commit' })
    assert.equal(secondLink, null)
    await action.page.waitForFunction(() => window.location.hash === '' && window.location.search === '')
    await action.page.getByText(copy[lang].unsubscribeNote, { exact: true }).waitFor()
    const working = action.page.getByRole('button', { name: copy[lang].working, exact: true })
    assert.ok(await working.isDisabled())
    assert.equal(action.events.posts.length, pendingPosts)
    action.hold.release()
    await safeResponse(await repeatedResponse, 200, { status: 'subscribed' })
    const retainedUnsubscribe = action.page.getByRole('button', { name: copy[lang].unsubscribe, exact: true })
    await retainedUnsubscribe.waitFor()
    assert.ok(await retainedUnsubscribe.isEnabled())
    assert.equal(await action.page.getByRole('status').count(), 0)
    assert.equal(action.events.posts.length, pendingPosts)
    checks.push('mobile_' + lang + '_real_email_link_requires_human_confirmation')
    checks.push('mobile_' + lang + '_confirmation_repeat_is_idempotent')
    checks.push('mobile_' + lang + '_same_document_links_are_consumed_and_scrubbed')
    checks.push('mobile_' + lang + '_inflight_old_response_preserves_new_action')

    advance('unsubscribe_' + lang)
    const unsubscribeGet = await action.context.request.get(API + '/newsletter/unsubscribe')
    assert.equal(unsubscribeGet.status(), 405)
    await expectRow(action.context, lang, 'subscribed', mailCount)
    await clickAction(action, lang, retainedUnsubscribe, 'unsubscribe', 'unsubscribed', mailCount)
    const unsubscribeAgain = await openAction(action, lang, delivery.unsubscribe, 'unsubscribe', 'unsubscribed', mailCount)
    await clickAction(action, lang, unsubscribeAgain, 'unsubscribe', 'unsubscribed', mailCount)
    checks.push('mobile_' + lang + '_unsubscribe_get_no_effect_explicit_action_required')
    checks.push('mobile_' + lang + '_unsubscribe_repeat_is_idempotent')

    advance('privacy_' + lang)
    const invalidated = await openAction(action, lang, delivery.confirm, 'confirm', 'unsubscribed', mailCount)
    const [refused] = await Promise.all([
      action.page.waitForResponse(value => value.url() === API + '/newsletter/confirm'
        && value.request().method() === 'POST'), invalidated.click(),
    ])
    // The real frontend intentionally handles 400 by status without consuming
    // its JSON. Observe that browser contract, then prove the exact error body
    // independently against the same synthetic refused action and token.
    safeHeaders(refused, 400)
    await action.page.getByRole('alert').filter({ hasText: copy[lang].invalid }).waitFor()
    const refusedJson = await action.context.request.post(API + '/newsletter/confirm', {
      data: { token: new URL(delivery.confirm).hash.slice('#confirm='.length) },
      headers: { Origin: ORIGIN }, timeout: 15000,
    })
    await safeResponse(refusedJson, 400, { detail: 'The newsletter link is invalid or has expired' })
    await expectRow(action.context, lang, 'unsubscribed', mailCount)
    assert.equal(action.events.posts.length, 5)
    assert.equal(action.events.external, 0)
    assert.equal(action.events.localRefused, 0)
    assert.equal(action.events.errors, 0)
    assert.deepEqual(await action.context.cookies(), [])
    checks.push('mobile_' + lang + '_tokens_scrubbed_no_tracking_no_accounts')
    checks.push('mobile_' + lang + '_unsubscribe_revokes_prior_confirmation')
    if (lang === 'es') {
      const final = await db(action.context)
      assert.deepEqual(Object.keys(final.rows).sort(), ['en', 'es'])
      assert.equal(final.mail_count, 2)
      assert.ok(Object.values(final.rows).every(row => row.status === 'unsubscribed'))
      assert.equal(Object.keys(await links()).length, 2)
    }
    await action.context.close()
  }
  advance('browser_teardown')
  await browser.close()
  browser = undefined
  assert.equal(checks.length, 20)
  console.log('NEWSLETTER_BROWSER_ACCEPTANCE ' + JSON.stringify({
    checks, viewport: 390, locales: ['en', 'es'], mail_messages: 2, live_messages: 0,
    application: 'c3c4ac8d0371941b0bfa2d086ade8752c42585c9',
    frontend: '82c83977ee27503ba7ee80c92190561e58302ba5',
    authoritative_rows: 2, final_status: 'unsubscribed', action_external_requests: 0,
    browser_stale_confirmation_body_observed: false, independent_stale_confirmation_json: true,
  }))
} catch (error) {
  const kind = /^[A-Za-z][A-Za-z0-9_]{0,62}$/.test(error?.name ?? '') ? error.name : 'UnhandledError'
  const match = /\/run-newsletter-browser\.mjs:([1-9][0-9]{0,3}):[1-9][0-9]{0,3}/.exec(error?.stack ?? '')
  console.log('NEWSLETTER_BROWSER_FAILURE ' + JSON.stringify({
    stage, error_type: kind, driver_line: match ? Number(match[1]) : null, response: responseEvidence,
    page_errors: pageErrorEvidence,
    body_error: bodyError,
  }))
  process.exitCode = 1
} finally {
  for (const context of contexts) await context.close().catch(() => {})
  if (browser) await browser.close().catch(() => {})
}
