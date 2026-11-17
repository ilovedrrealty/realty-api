import { readFile } from 'node:fs/promises'

const projectName = { en: 'Synthetic East Shore Development', es: 'Proyecto Costa Este Sintético' }
const draftNote = 'SYNTHETIC_PRIVATE_DEVELOPMENT_DRAFT_NOTE'
const plan = 'https://example.invalid/development/a101.pdf'
const unsafePlan = 'http://example.invalid/development/b201.pdf'
const syntheticNow = '2026-11-17T14:00:00Z'
const verified = '2026-11-15'
const validThrough = '2026-11-30'
const updateDate = '2026-11-12'
const paymentDate = '2026-12-01'
const locales = {
  en: { locale: 'en-US', heading: 'Development availability', phase: 'Phase', plan: 'View floor plan for unit',
    available: 'Available', reserved: 'Reserved', sold: 'Sold', unconfirmed: 'Unconfirmed', phaseA: 'Ocean Phase',
    payments: 'Payment stages', updates: 'Development updates', checked: 'Developer information verified',
    delivery: 'Delivery target Q2 2028', current: 'Availability confirmed through', stage: 'Reservation payment',
    due: 'On contract signing', updateTitle: 'Foundation work recorded' },
  es: { locale: 'es-DO', heading: 'Disponibilidad del proyecto', phase: 'Etapa', plan: 'Ver plano de la unidad',
    available: 'Disponible', reserved: 'Reservada', sold: 'Vendida', unconfirmed: 'Por confirmar', phaseA: 'Etapa Océano',
    payments: 'Etapas de pago', updates: 'Actualizaciones del proyecto', checked: 'Información del desarrollador verificada el',
    delivery: 'Entrega prevista para T2 de 2028', current: 'Disponibilidad confirmada hasta el', stage: 'Pago de reserva',
    due: 'Al firmar el contrato', updateTitle: 'Registro de la cimentación' },
}

function check(condition, diagnostic) {
  if (!condition) throw new Error(diagnostic)
}

export function createDevelopmentFixture(api, listing) {
  let visibility = 'absent'
  const requestPath = `/listings/${listing}/development`
  const seen = new Map()
  const shape = {
    listing_id: listing, revision: 1, published_at: '2026-11-16T12:00:00Z',
    verified_on: verified, availability_valid_through: validThrough, availability_current: true,
    project: {
      name_en: projectName.en, name_es: projectName.es,
      summary_en: 'Synthetic information from the developer.', summary_es: 'Información sintética del desarrollador.',
      target_delivery: '2028-Q2',
      phases: [
        { code: 'ocean', name_en: 'Ocean Phase', name_es: 'Etapa Océano', target_delivery: '2028-Q2', units: [
          { code: 'A101', floor: '1', area_m2: '81.25', price: '212500.00', currency: 'USD', status: 'available', floor_plan_url: plan },
          { code: 'A102', floor: '1', area_m2: null, price: null, currency: null, status: 'reserved', floor_plan_url: null },
        ] },
        { code: 'garden', name_en: 'Garden Phase', name_es: 'Etapa Jardín', target_delivery: null, units: [
          { code: 'B201', floor: null, area_m2: '75', price: '11000000.00', currency: 'DOP', status: 'sold', floor_plan_url: unsafePlan },
        ] },
      ],
      payment_stages: [{ label_en: 'Reservation payment', label_es: 'Pago de reserva',
        due_en: 'On contract signing', due_es: 'Al firmar el contrato', due_date: paymentDate, percent: '15',
        amount: null, currency: null }],
      updates: [{ date: updateDate, title_en: 'Foundation work recorded', title_es: 'Registro de la cimentación',
        text_en: 'Synthetic developer update.', text_es: 'Actualización sintética del desarrollador.' }],
    },
  }
  return {
    requestPath,
    setVisibility(next) {
      check(['absent', 'current', 'unconfirmed', 'withdrawn'].includes(next), 'DEVELOPMENT_FIXTURE_STATE_INVALID')
      visibility = next
    },
    visits(state) { return seen.get(state) ?? 0 },
    responseFor(request) {
      if (request.method !== 'GET') return null
      const url = new URL(request.url, api)
      if (url.pathname !== requestPath) return null
      if (url.search || request.headers.cookie || request.headers.authorization) {
        return { status: 400, data: { detail: 'Expected anonymous development read' } }
      }
      seen.set(visibility, (seen.get(visibility) ?? 0) + 1)
      if (visibility === 'absent' || visibility === 'withdrawn') {
        return { status: 404, data: { detail: 'Development unavailable' } }
      }
      return { status: 200, data: { ...shape, availability_current: visibility === 'current' } }
    },
  }
}

function dateLabel(day, locale) {
  return new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeZone: 'UTC' }).format(new Date(`${day}T12:00:00Z`))
}

async function observeRead(page, api, fixture, status, action) {
  const pending = page.waitForResponse(response => response.url() === api + fixture.requestPath
    && response.request().method() === 'GET' && response.status() === status).catch(() => null)
  await action()
  const response = await pending
  check(response && /(?:^|,\s*)no-store(?:,|$)/i.test(response.headers()['cache-control'] ?? ''),
    'DEVELOPMENT_NO_STORE_RESPONSE_REQUIRED')
  await response.finished()
  check(!(await response.text()).includes(draftNote), 'DEVELOPMENT_PRIVATE_RESPONSE_LEAK')
}

async function verifyLocale(browser, fixture, { landing, api }, route, language, setStage) {
  const copy = locales[language]
  const context = await browser.newContext({ locale: copy.locale, viewport: { width: 390, height: 844 },
    isMobile: true, hasTouch: true, serviceWorkers: 'block' })
  try {
    context.setDefaultTimeout(12_000)
    await context.addCookies([{ name: 'ildr_synthetic_dev_cookie', value: 'fixture-only', url: api }])
    await context.route('**/*', request => [landing, api].includes(new URL(request.request().url()).origin)
      ? request.continue() : request.abort('blockedbyclient'))
    await context.addInitScript(lang => {
      if (!navigator.onLine) Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => true })
      if (location.protocol === 'http:') localStorage.setItem('ildr_lang', lang)
    }, language)
    const page = await context.newPage()
    await page.clock.setFixedTime(new Date(syntheticNow))
    const section = page.getByRole('region', { name: copy.heading, exact: true })
    const pageRead = () => page.reload({ waitUntil: 'domcontentloaded' })
    const card = code => section.getByRole('listitem').filter({ has: page.getByRole('heading', { name: code, exact: true }) })
    const absence = async () => {
      await section.waitFor({ state: 'hidden' })
      const rendered = await page.locator('body').innerText()
      check(!rendered.includes(projectName[language]) && !rendered.includes('A101') && !rendered.includes(draftNote),
        'DEVELOPMENT_WITHDRAWN_CONTENT_VISIBLE')
    }

    setStage('development_absent')
    fixture.setVisibility('absent')
    await observeRead(page, api, fixture, 404, async () => {
      const document = await page.goto(landing + route, { waitUntil: 'domcontentloaded' })
      check(document?.ok(), 'ACTUAL_DEVELOPMENT_LISTING_EXPORT_REQUIRED')
    })
    await absence()

    setStage('development_published')
    fixture.setVisibility('current')
    await observeRead(page, api, fixture, 200, pageRead)
    await section.waitFor()
    await section.getByText(projectName[language], { exact: true }).waitFor()
    await section.getByText(`${copy.checked} ${dateLabel(verified, copy.locale)}.`, { exact: true }).waitFor()
    await section.getByText(`${copy.current} ${dateLabel(validThrough, copy.locale)}.`, { exact: true }).waitFor()
    await section.getByText(copy.delivery, { exact: true }).first().waitFor()
    await card('A101').getByText(copy.available, { exact: true }).waitFor()
    await card('A102').getByText(copy.reserved, { exact: true }).waitFor()
    await card('B201').getByText(copy.sold, { exact: true }).waitFor()
    const floorPlan = card('A101').getByRole('link', { name: `${copy.plan} A101`, exact: true })
    check(await floorPlan.getAttribute('href') === plan && await floorPlan.getAttribute('target') === '_blank'
      && (await floorPlan.getAttribute('rel')).split(' ').includes('noopener')
      && await card('B201').getByRole('link').count() === 0, 'DEVELOPMENT_FLOOR_PLAN_SAFETY_REQUIRED')
    await section.getByRole('heading', { name: copy.payments, exact: true }).waitFor()
    await section.getByText(copy.due, { exact: true }).waitFor()
    const payment = section.getByRole('listitem').filter({ hasText: copy.stage })
    check(/15\s*%/.test(await payment.innerText()) && await payment.locator(`time[datetime="${paymentDate}"]`).count() === 1,
      'DATED_DEVELOPMENT_PAYMENT_REQUIRED')
    await section.getByRole('heading', { name: copy.updates, exact: true }).waitFor()
    const update = section.getByRole('listitem').filter({ has: page.getByRole('heading', { name: copy.updateTitle, exact: true }) })
    check(await update.locator(`time[datetime="${updateDate}"]`).count() === 1, 'DATED_DEVELOPMENT_UPDATE_REQUIRED')
    check(await section.locator('table:visible').count() === 0 && await card('A101').isVisible(),
      'DEVELOPMENT_MOBILE_CARDS_REQUIRED')
    const filter = section.getByRole('combobox', { name: copy.phase, exact: true })
    await filter.selectOption({ label: copy.phaseA })
    await card('B201').waitFor({ state: 'detached' })
    check(await card('A101').isVisible() && await card('A102').isVisible(), 'DEVELOPMENT_PHASE_FILTER_REQUIRED')

    setStage('development_unconfirmed')
    fixture.setVisibility('unconfirmed')
    await observeRead(page, api, fixture, 200, pageRead)
    await card('A101').getByText(copy.unconfirmed, { exact: true }).waitFor()
    await card('A102').getByText(copy.unconfirmed, { exact: true }).waitFor()
    await card('B201').getByText(copy.unconfirmed, { exact: true }).waitFor()
    check(await section.getByText(copy.available, { exact: true }).count() === 0, 'DEVELOPMENT_STALE_STOCK_VISIBLE')

    setStage('development_withdrawn')
    fixture.setVisibility('withdrawn')
    await observeRead(page, api, fixture, 404, () => page.evaluate(() => window.dispatchEvent(new Event('focus'))))
    await absence()
    await observeRead(page, api, fixture, 404, pageRead)
    await absence()
  } finally {
    await context.close()
  }
}

export async function verifyDevelopment(browser, fixture, origins, exportRoot, listing, setStage) {
  const route = `/listing/${listing}/`
  const raw = await readFile(exportRoot + route + 'index.html', 'utf8')
  check(raw.includes('Synthetic CI villa') && !raw.includes('schema.org/InStock')
    && !raw.includes(projectName.en) && !raw.includes(projectName.es) && !raw.includes(draftNote),
  'DEVELOPMENT_STATIC_STOCK_OR_DRAFT_REQUIRED_ABSENT')
  for (const language of Object.keys(locales)) await verifyLocale(browser, fixture, origins, route, language, setStage)
  check(['absent', 'current', 'unconfirmed', 'withdrawn'].every(state => fixture.visits(state) >= 2),
    'DEVELOPMENT_PUBLIC_API_LIFECYCLE_REQUIRED')
  return { status: 'PASS', actual_listing_export: true, bilingual_390_touch_cards: true, phase_filter: true,
    sourced_current_and_unconfirmed: true, safe_plan_link: true, dated_updates_and_payment_labels: true,
    anonymous_public_no_store: true, unavailable_before_and_after: true, withdrawal_removes_same_page: true,
    no_false_static_stock_or_draft: true, synthetic_public_endpoint: true }
}
