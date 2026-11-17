import { lstat, readFile, readdir, realpath } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { inflateSync } from 'node:zlib'
import { chromium } from 'playwright-core'

const require = createRequire(import.meta.url)
const landing = 'http://127.0.0.1:8917'
const api = 'http://127.0.0.1:8918'
const id = '10000000-0000-0000-0000-000000000001'
const listing = '/listing/' + id + '/'
const apiListing = '/listings/' + id
const canonical = 'https://ilovedrrealty.com' + listing
const fixtureFile = '/browser-fixtures/fixtures.json'
const expectedPhoto = '/__brochure_fixture/exterior.png'
const checks = []
let stage = 'fixture'

class CheckFailure extends Error {}
function check(valid, reason) {
  if (!valid) throw new CheckFailure(reason)
}

async function verifyFixture() {
  check(process.platform === 'linux' && process.env.GITHUB_ACTIONS === 'true'
    && process.env.GITHUB_REPOSITORY === 'ilovedrrealty/realty-api'
    && process.env.ILDR_BROWSER_FIXTURES === fixtureFile, 'HOSTED_FIXTURE_REQUIRED')
  check((await readdir('/sys/class/net')).join(',') === 'lo', 'LOOPBACK_ONLY_REQUIRED')
  const stat = await lstat(fixtureFile)
  check(stat.isFile() && (stat.mode & 0o777) === 0o600 && stat.size <= 512, 'PRIVATE_FIXTURE_REQUIRED')
  const value = JSON.parse(await readFile(fixtureFile, 'utf8'))
  check(value.listing_id === id && value.photo_path === expectedPhoto, 'EXACT_STATIC_LISTING_ID_REQUIRED')
  check(require('/package-source/pdf-lib/package.json').version === '1.17.1', 'EXACT_PDF_PARSER_VERSION_REQUIRED')
}

function isDetail(response) {
  return response.url() === api + apiListing && response.request().method() === 'GET'
}

function noStore(response) {
  check(/(?:^|,\s*)no-store(?:,|$)/.test(response.headers()['cache-control'] ?? ''), 'FRESH_DETAIL_NO_STORE_REQUIRED')
}

async function session(browser, locale) {
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true, serviceWorkers: 'block', acceptDownloads: true,
  })
  context.setDefaultTimeout(12_000)
  await context.route('**/*', route => {
    const url = new URL(route.request().url())
    return url.origin === api || url.origin === landing ? route.continue() : route.abort('blockedbyclient')
  })
  await context.addInitScript(language => {
    localStorage.setItem('ildr_lang', language)
    sessionStorage.setItem('lead_modal_dismissed', '1')
  }, locale)
  return context
}

async function visit(context) {
  const page = await context.newPage()
  const [detail, document] = await Promise.all([
    page.waitForResponse(isDetail),
    page.goto(landing + listing, { waitUntil: 'domcontentloaded' }),
  ])
  check(document?.status() === 200 && detail.status() === 200, 'ACTUAL_EXPORTED_DETAIL_REQUIRED')
  noStore(detail)
  return { page, body: await detail.json() }
}

async function transition(context, operation) {
  const result = await context.request.post(api + '/__brochure_fixture/' + operation, { timeout: 5_000, maxRedirects: 0 })
  try {
    check(result.status() === 204, 'EXACT_SYNTHETIC_TRANSITION_REQUIRED')
  } finally {
    await result.dispose()
  }
}

function button(page, name) {
  return page.getByRole('button', { name, exact: true })
}

async function exterior(page, locale) {
  const photo = button(page, locale === 'es' ? 'Ver foto 1 de 1' : 'View photo 1 of 1')
  await photo.waitFor()
  const image = await photo.evaluate(node => getComputedStyle(node).backgroundImage)
  check(image === 'url("' + api + expectedPhoto + '")', 'EXISTING_EXTERIOR_SOURCE_REQUIRED')
  return image
}

async function readPdf(download, locale) {
  stage = locale + '_pdf_file'
  const { PDFArray, PDFDict, PDFDocument, PDFName, PDFString } = require('/package-source/pdf-lib')
  check(download.suggestedFilename() === 'ilovedrrealty-' + id + '-' + locale + '.pdf',
    'EXACT_PDF_FILENAME_REQUIRED')
  check(await download.failure() === null, 'NATIVE_ATTACHMENT_MUST_COMPLETE')
  const file = await download.path()
  check(file.startsWith('/tmp/') && await realpath(file) === file, 'OWNED_DOWNLOAD_FILE_REQUIRED')
  const stat = await lstat(file)
  check(stat.isFile() && stat.size >= 1500 && stat.size <= 96 * 1024, 'BOUND_PDF_ATTACHMENT_REQUIRED')
  const bytes = await readFile(file)
  check(bytes.length === stat.size && bytes.subarray(0, 5).toString('ascii') === '%PDF-', 'ACTUAL_PDF_DOWNLOAD_REQUIRED')
  stage = locale + '_pdf_parse'
  const pdf = await PDFDocument.load(bytes)
  check(pdf.getPageCount() === 1 && pdf.getPage(0).getWidth() === 612 && pdf.getPage(0).getHeight() === 792
    && pdf.getAuthor() === 'I Love DR Realty', 'SINGLE_BRANDED_LETTER_PAGE_REQUIRED')
  check(pdf.catalog.lookup(PDFName.of('Lang'), PDFString).decodeText() === locale, 'PDF_LANGUAGE_REQUIRED')
  const annotations = pdf.getPage(0).node.lookup(PDFName.of('Annots'), PDFArray)
  check(annotations.size() === 1, 'EXACT_SINGLE_CANONICAL_LINK_REQUIRED')
  const action = annotations.lookup(0, PDFDict).lookup(PDFName.of('A'), PDFDict)
  check(action.lookup(PDFName.of('S'), PDFName).asString() === '/URI'
    && action.lookup(PDFName.of('URI'), PDFString).decodeText() === canonical, 'CANONICAL_PUBLIC_LINK_REQUIRED')
  stage = locale + '_pdf_text'
  const references = pdf.getPage(0).node.Contents().asArray()
  check(references.length === 1, 'SINGLE_PDF_CONTENT_STREAM_REQUIRED')
  const operations = inflateSync(pdf.context.lookup(references[0]).getContents(), { maxOutputLength: 131072 }).toString('ascii')
  const text = [...operations.matchAll(/<([A-F0-9]+)> Tj/g)]
    .map(match => Buffer.from(match[1], 'hex').toString('latin1')).join('\n')
  for (const privateValue of ['Synthetic undisclosed property address', 'Synthetic private brochure description',
    'Descripción privada de prueba', 'SYNTHETIC_BROCHURE_', 'internal-brochure', 'brochure-private-agreement',
    'maps.example', '19.719482', '-70.519482', 'Browser Owner', expectedPhoto]) {
    check(!text.includes(privateValue) && !pdf.getTitle().includes(privateValue), 'PRIVATE_SOURCE_EXCLUDED_FROM_PDF')
  }
  return text
}

async function downloadBrochure(page, label, locale) {
  const action = button(page, label)
  await action.scrollIntoViewIfNeeded()
  const bounds = await action.boundingBox()
  const viewport = await page.evaluate(() => ({
    inner: innerWidth, client: document.documentElement.clientWidth,
    scroll: document.documentElement.scrollWidth, visual: visualViewport?.width ?? innerWidth,
  }))
  check(bounds, 'MOBILE_DOWNLOAD_CONTROL_MISSING')
  const geometry = [page.viewportSize().width, viewport.inner, viewport.client, viewport.scroll, viewport.visual,
    bounds.x, bounds.x + bounds.width, bounds.height].map(value => Math.round(value * 100) / 100)
  check(geometry.every(value => Number.isFinite(value) && Math.abs(value) < 10000), 'MOBILE_GEOMETRY_OUTSIDE_DIAGNOSTIC_RANGE')
  console.log('LISTING_BROCHURE_GEOMETRY locale=' + locale + ' ' + geometry.join(' '))
  check(viewport.inner === 390 && bounds.x >= -1 && bounds.x + bounds.width <= 391 && bounds.height >= 44,
    'MOBILE_DOWNLOAD_CONTROL_MUST_FIT')
  stage = locale + '_request_attachment'
  const [detail, download] = await Promise.all([
    page.waitForResponse(isDetail),
    page.waitForEvent('download'),
    action.click(),
  ])
  check(detail.status() === 200, 'DOWNLOAD_REVALIDATES_ACTIVE_LISTING')
  noStore(detail)
  stage = locale + '_read_detail'
  const body = await detail.json()
  const text = await readPdf(download, locale)
  stage = locale + '_review_receipt'
  return { text, body }
}

async function main() {
  await verifyFixture()
  const browser = await chromium.launch({
    headless: true, timeout: 20_000, proxy: { server: 'http://127.0.0.1:9', bypass: '127.0.0.1' },
    args: ['--disable-background-networking', '--disable-component-update', '--disable-sync', '--no-first-run'],
  })
  try {
    stage = 'english_mobile_listing'
    const english = await session(browser, 'en')
    const first = await visit(english)
    check(first.body.id === id && first.body.status === 'active' && Number(first.body.price) === 15550000
      && first.body.images?.[0] === api + expectedPhoto, 'EXACT_OLD_ACTIVE_LISTING_REQUIRED')
    const before = await exterior(first.page, 'en')
    const asset = await english.request.get(api + expectedPhoto, { timeout: 5_000, maxRedirects: 0 })
    check(asset.status() === 200 && asset.headers()['content-type'] === 'image/png'
      && (await asset.body()).subarray(0, 8).equals(Buffer.from('89504e470d0a1a0a', 'hex')),
    'SYNTHETIC_PUBLIC_EXTERIOR_REQUIRED')
    await asset.dispose()

    stage = 'english_fresh_download'
    await transition(english, 'fresh-price')
    const en = await downloadBrochure(first.page, 'Download property brochure', 'en')
    check(Number(en.body.price) === 16000000.25 && en.text.includes('DOP 16,000,000.25')
      && en.text.includes('Rent per month') && en.text.includes('DOP 45,750.50')
      && en.text.includes('Rent per day') && en.text.includes('DOP 1,450.00')
      && en.text.includes('Sosúa') && !en.text.includes('15,550,000'), 'FRESH_NATIVE_PRICE_AND_PERIODS_REQUIRED')
    check(await exterior(first.page, 'en') === before, 'EXTERIOR_CHANGED_AFTER_DOWNLOAD')
    checks.push('english_mobile_attachment_contains_fresh_native_price_and_terms')

    stage = 'spanish_hidden_listing'
    await transition(english, 'hidden-rent')
    const spanish = await session(browser, 'es')
    const second = await visit(spanish)
    check(second.body.location_precision === 'hidden' && second.body.latitude === null
      && second.body.title === 'Synthetic undisclosed property address' && second.body.currency === 'USD',
    'REAL_HIDDEN_PROJECTION_REQUIRED')
    const spanishPhoto = await exterior(second.page, 'es')
    const downloadEvents = []
    second.page.on('download', item => downloadEvents.push(item.suggestedFilename()))
    stage = 'spanish_public_rent_download'
    const es = await downloadBrochure(second.page, 'Descargar folleto de la propiedad', 'es')
    check(es.body.location_precision === 'hidden' && es.text.includes('Folleto de la propiedad')
      && es.text.includes('Alquiler por mes') && es.text.includes('USD 700.00')
      && !es.text.includes('Alquiler por día') && !es.text.includes('15,000')
      && !es.text.includes('Sosúa') && !es.text.includes('DOP '), 'HIDDEN_LOCATION_AND_UNVERIFIED_RENT_EXCLUDED')
    checks.push('spanish_hidden_rental_has_only_declared_usd_rate_and_safe_fields')
    check(await exterior(second.page, 'es') === spanishPhoto && spanishPhoto === before, 'EXTERIOR_CHANGED_ACROSS_LANGUAGES')
    checks.push('existing_public_exterior_media_preserved')

    stage = 'withdrawal_blocks_stale_download'
    await transition(spanish, 'withdraw')
    const [withdrawn] = await Promise.all([
      second.page.waitForResponse(isDetail),
      button(second.page, 'Descargar folleto de la propiedad').click(),
    ])
    check(withdrawn.status() === 404, 'WITHDRAWN_DETAIL_MUST_RETURN_404')
    noStore(withdrawn)
    await second.page.getByText('No se pudo descargar el folleto. Inténtalo de nuevo.', { exact: true }).waitFor()
    check(downloadEvents.length === 1, 'WITHDRAWN_LISTING_MUST_NOT_DOWNLOAD')
    stage = 'withdrawn_page_hides_action'
    await second.page.reload({ waitUntil: 'domcontentloaded' })
    await second.page.getByText('Propiedad no encontrada.', { exact: true }).waitFor()
    check(await button(second.page, 'Descargar folleto de la propiedad').count() === 0
      && downloadEvents.length === 1, 'WITHDRAWN_PROPERTY_HIDES_BROCHURE_ACTION')
    checks.push('withdrawal_blocks_stale_click_and_hides_download_on_reload')
    checks.push('actual_loopback_only_without_outbound')
    console.log('LISTING_BROCHURE_BROWSER', JSON.stringify({ status: 'PASS', checks }))
  } finally {
    await browser.close()
  }
}

try {
  await main()
} catch (error) {
  const reason = error instanceof CheckFailure ? error.message
    : error?.name === 'TimeoutError' ? 'TIMED_OUT' : 'BROWSER_OPERATION_FAILED'
  if (!(error instanceof CheckFailure)) {
    const type = ['Error', 'TypeError', 'RangeError', 'SyntaxError', 'TimeoutError'].includes(error?.name)
      ? error.name : 'OtherError'
    const line = /\/rehearsal\/run-listing-brochures\.mjs:([0-9]{1,4}):[0-9]{1,4}/.exec(error?.stack ?? '')?.[1] ?? 'none'
    const code = ['MODULE_NOT_FOUND', 'ERR_INVALID_ARG_TYPE', 'ENOENT', 'EACCES', 'Z_DATA_ERROR'].includes(error?.code)
      ? error.code : 'none'
    console.error('LISTING_BROCHURE_DIAGNOSTIC type=' + type + ' line=' + line + ' code=' + code)
  }
  console.error('LISTING_BROCHURE_BROWSER_FAILURE', stage, reason)
  process.exitCode = 1
}
