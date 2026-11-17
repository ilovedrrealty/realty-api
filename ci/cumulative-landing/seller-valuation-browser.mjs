import { readFile } from 'node:fs/promises'
import { isDeepStrictEqual } from 'node:util'

const copies = {
  en: {
    locale: 'en-US', cta: 'Get free valuation', heading: 'Request a free property valuation',
    group: 'Property details', submit: 'Request valuation', receipt: 'We’ve received your request.',
    unconfirmed: 'We couldn’t confirm your valuation request. Your answers are still here. Please try again.',
  },
  es: {
    locale: 'es-DO', cta: 'Obtener valoración gratuita', heading: 'Solicita una valoración gratuita de tu propiedad',
    group: 'Datos de la propiedad', submit: 'Solicitar valoración', receipt: 'Recibimos tu solicitud.',
    unconfirmed: 'No pudimos confirmar tu solicitud de valoración. Tus respuestas se conservaron. Inténtalo de nuevo.',
  },
}
const cases = {
  en: {
    contact: 'seller-valuation-en@example.test', lead: 'b1d34b23-cbf4-4c1c-873b-536442027029', language: 'en',
    subject: { property_type: 'villa', location: 'Synthetic Cedar Ridge', area_m2: 89.75, bedrooms: 0, bathrooms: 2.5, condition: 'fair' },
  },
  es: {
    contact: 'seller-valuation-es@example.test', lead: 'ff327250-216e-43c7-8b3e-1593d9702048', language: 'es',
    subject: { property_type: 'condo', location: 'Zona sintética Mirador', area_m2: 94.25, bedrooms: 2, bathrooms: 1.5, condition: 'good' },
  },
  ordinary: {
    contact: 'ordinary-seller@example.test', lead: 'f82db674-78f0-4b7d-8d3a-4aeeca4dd8c6', language: 'en',
    comment: 'Synthetic ordinary seller comment.',
  },
}
const name = 'Synthetic seller journey'
const inquiry = '[Get Started inquiry]'
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/

function check(condition, code) {
  if (!condition) throw new Error(code)
}

async function requestJSON(request) {
  let size = 0
  const chunks = []
  for await (const chunk of request) {
    size += chunk.length
    if (size > 4096) return null
    chunks.push(chunk)
  }
  try { return JSON.parse(Buffer.concat(chunks).toString('utf8')) } catch { return null }
}

export function createSellerValuationFixture() {
  const results = new Map()
  const identifiers = new Set()
  const denied = { status: 422, data: { detail: 'Invalid synthetic lead contract' } }
  let visits = 0

  return {
    visits: () => visits,
    count: key => results.get(key)?.attempts ?? 0,
    complete: () => identifiers.size === 3 && results.get('en')?.attempts === 2
      && results.get('es')?.attempts === 2 && results.get('ordinary')?.attempts === 1 && visits === 5,
    async responseFor(request) {
      if (request.method !== 'POST' || request.url !== '/leads') return null
      visits++
      if (request.headers.authorization || request.headers.cookie
        || request.headers['content-type']?.split(';', 1)[0] !== 'application/json') return denied
      const body = await requestJSON(request)
      const key = Object.keys(cases).find(candidate => body?.email === cases[candidate].contact)
      if (!key) return denied
      const expected = cases[key]
      const tracking = {
        form: 'get_started', page_path: '/get-started/', locale: expected.language, intent: 'sell',
        contact_consent: true, measurement_consent: 'denied', internal_test: false, attribution: {},
      }
      const message = expected.subject ? ['Interest: Property valuation', inquiry].join('\n\n')
        : ['Interest: Selling a property', inquiry, expected.comment].join('\n\n')
      const payload = {
        type: 'seller_interest', name, email: expected.contact, message, tracking, submission_id: body.submission_id,
        ...(expected.subject ? { valuation_subject: expected.subject } : {}),
      }
      if (!uuid.test(body.submission_id ?? '') || !isDeepStrictEqual(body, payload)) return denied

      const previous = results.get(key)
      if (previous) {
        if (!expected.subject || previous.attempts !== 1 || !isDeepStrictEqual(previous.payload, body)) return denied
        previous.attempts++
      } else {
        if (identifiers.has(body.submission_id)) return denied
        identifiers.add(body.submission_id)
        results.set(key, { payload: body, attempts: 1 })
      }
      return { status: 201, data: { id: expected.lead, status: 'new',
        ...(expected.subject && previous ? { valuation_request_registered: true } : {}) } }
    },
  }
}

async function submit(page, button, api, setStage, stage) {
  setStage(stage)
  const pending = page.waitForResponse(response => response.url() === api + '/leads'
    && response.request().method() === 'POST', { timeout: 12_000 }).catch(() => null)
  await button.click()
  const response = await pending
  check(response, 'SELLER_POST_RESPONSE_MISSING')
  check(response.status() === 201, 'SELLER_POST_CONTRACT_REJECTED')
}

async function verifyCase(browser, fixture, { landing, api }, key, setStage) {
  const fixtureCase = cases[key]
  const copy = copies[fixtureCase.language]
  setStage('seller_context')
  const context = await browser.newContext({ locale: copy.locale, viewport: { width: 390, height: 844 },
    isMobile: true, hasTouch: true, serviceWorkers: 'block' })
  try {
    context.setDefaultTimeout(12_000)
    context.setDefaultNavigationTimeout(12_000)
    await context.route('**/*', route => [landing, api].includes(new URL(route.request().url()).origin)
      ? route.continue() : route.abort('blockedbyclient'))
    await context.addInitScript(language => {
      if (location.protocol !== 'http:') return
      if (!navigator.onLine) Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => true })
      localStorage.setItem('ildr_lang', language)
      localStorage.setItem('ildr_measurement_consent_v1', JSON.stringify({ choice: 'denied', savedAt: Date.now() }))
      sessionStorage.setItem('lead_modal_dismissed', '1')
    }, fixtureCase.language)
    const page = await context.newPage()
    if (fixtureCase.subject) {
      setStage('seller_selling')
      const home = await page.goto(landing + '/selling/', { waitUntil: 'domcontentloaded' })
      check(home?.ok(), 'ACTUAL_SELLING_EXPORT_REQUIRED')
      setStage('seller_cta')
      await page.getByRole('button', { name: copy.cta, exact: true }).click()
      await page.waitForURL(url => url.origin === landing && url.pathname === '/get-started/'
        && url.searchParams.getAll('intent').join() === 'sell' && url.searchParams.getAll('request').join() === 'valuation')
      await page.getByRole('heading', { name: copy.heading, exact: true }).waitFor()
    } else {
      setStage('seller_ordinary')
      const document = await page.goto(landing + '/get-started/?intent=sell', { waitUntil: 'domcontentloaded' })
      check(document?.ok(), 'ACTUAL_SELLING_EXPORT_REQUIRED')
      await page.getByRole('heading', { name: 'Selling your property in the DR?', exact: true }).waitFor()
    }

    setStage('seller_form_fields')
    const form = page.locator('form').filter({ has: page.locator('input[name="consent"]') })
    const consent = form.locator('input[name="consent"]')
    const comments = form.locator('textarea[name="message"]')
    const propertyGroup = form.getByRole('group', { name: copy.group, exact: true })
    const submitButton = form.getByRole('button', { name: fixtureCase.subject ? copy.submit : 'Contact me', exact: true })
    await form.locator('input[name="name"]').fill(name)
    await form.locator('input[name="email"]').fill(fixtureCase.contact)
    if (fixtureCase.subject) {
      const subject = fixtureCase.subject
      await propertyGroup.waitFor()
      check(await comments.count() === 0, 'SELLER_PRIVATE_FORM_INCLUDED_COMMENTS')
      await propertyGroup.locator('[name="valuation-property-type"]').selectOption(subject.property_type)
      await propertyGroup.locator('[name="valuation-location"]').fill(' ' + subject.location + ' ')
      const localNumber = value => key === 'es' ? String(value).replace('.', ',') : String(value)
      await propertyGroup.locator('[name="valuation-area"]').fill(localNumber(subject.area_m2))
      await propertyGroup.locator('[name="valuation-bedrooms"]').fill(String(subject.bedrooms))
      await propertyGroup.locator('[name="valuation-bathrooms"]').fill(localNumber(subject.bathrooms))
      await propertyGroup.locator('[name="valuation-condition"]').selectOption(subject.condition)
    } else {
      check(await propertyGroup.count() === 0 && await form.locator('[name^="valuation-"]').count() === 0,
        'ORDINARY_SELLER_INCLUDES_VALUATION_FIELDS')
      await comments.fill(fixtureCase.comment)
    }
    check(await form.evaluate(node => {
      const bounds = node.getBoundingClientRect()
      return navigator.maxTouchPoints > 0 && bounds.left >= 0 && bounds.right <= window.innerWidth
    }), 'SELLER_MOBILE_FORM_REQUIRED')

    setStage('seller_consent')
    const existingVisits = fixture.visits()
    check(await consent.evaluate(input => input.required && input.validity.valueMissing), 'SELLER_CONSENT_REQUIRED')
    await submitButton.click()
    check(fixture.visits() === existingVisits && !await consent.isChecked(), 'SELLER_SENT_WITHOUT_CONSENT')
    await consent.check()

    await submit(page, submitButton, api, setStage, 'seller_initial_post')
    if (fixtureCase.subject) {
      setStage('seller_receipt_unconfirmed')
      const warning = page.getByText(copy.unconfirmed, { exact: true })
      await warning.waitFor()
      check(await warning.getAttribute('role') === 'alert', 'SELLER_UNCONFIRMED_RECEIPT_NOT_ANNOUNCED')
      check(fixture.count(key) === 1 && await propertyGroup.locator('[name="valuation-location"]').inputValue() ===
        ' ' + fixtureCase.subject.location + ' ' && await form.locator('input[name="email"]').inputValue() === fixtureCase.contact,
      'SELLER_FORM_NOT_PRESERVED_ON_RETRY')
      await submit(page, submitButton, api, setStage, 'seller_retry')
    }
    setStage('seller_receipt_confirmed')
    await page.getByRole('heading', { name: copy.receipt, exact: true }).waitFor()
    check(await form.count() === 0 && !(await context.cookies(api)).some(cookie => cookie.name === 'ildr_token'),
      'SELLER_ANONYMOUS_RECEIPT_REQUIRED')
  } finally {
    await context.close()
  }
}

export async function verifySellerValuation(browser, fixture, origins, exportRoot, setStage) {
  const documents = await Promise.all(['/selling/index.html', '/get-started/index.html'].map(
    path => readFile(exportRoot + path, 'utf8')))
  check(documents.every(html => Object.values(cases).every(item =>
    !html.includes(item.contact) && (!item.subject || !html.includes(item.subject.location)))),
  'SELLER_SYNTHETIC_PRIVATE_DATA_IN_STATIC_HTML')
  for (const key of Object.keys(cases)) await verifyCase(browser, fixture, origins, key, setStage)
  check(fixture.complete(), 'SELLER_SYNTHETIC_RECEIPT_COUNT_MISMATCH')
  return { status: 'PASS', actual_static_export: true, selling_cta_en_es: true, mobile_structured_fields: true,
    contact_consent_required: true, anonymous_seller_post: true, subject_only_private_channel: true,
    independent_submission_ids: true, same_submission_id_on_missing_ack_retry: true,
    success_requires_valuation_ack: true, ordinary_seller_unchanged: true, synthetic_loopback_only: true }
}
