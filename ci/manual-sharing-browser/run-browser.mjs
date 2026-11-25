import { access, lstat, readFile } from 'node:fs/promises'
import { writeSync } from 'node:fs'
import { deflateSync } from 'node:zlib'
import { createHash } from 'node:crypto'
import { chromium, webkit } from 'playwright-core'

const API = 'http://127.0.0.1:8918', DASH = 'http://127.0.0.1:4175', LANDING = 'http://127.0.0.1:8917'
const contexts = [], geometries = [], journeys = [], presence = new WeakSet()
let browser, facts, accounts, stage = 'fixture', timer, failed = false, presenceCount = 0
let pageErrors = 0, unexpectedMutations = 0, externalWrites = 0, trackingRequests = 0, blockedExternal = 0
let actualLogins = 0, boundaryChecks = 0, controlTransitions = 0, clipboardWrites = 0, composerChecks = 0
let nativeCalls = 0, pngDownloads = 0, captionDownloads = 0, imageFailures = 0, readAborts = 0, staleCloses = 0
let freshFetches = 0, guardedPhotoFetches = 0, changedChecks = 0, withdrawalChecks = 0
const imageModes = new WeakMap()
class Failure extends Error {}
const check = (value, reason) => { if (!value) throw new Failure(reason) }
const button = (scope, name) => scope.getByRole('button', { name, exact: true })
const digest = bytes => createHash('sha256').update(bytes).digest('hex')
function advance(value) {
  check(/^[A-Za-z][A-Za-z0-9_]{0,95}$/.test(value), 'FINITE_PHASE_REQUIRED')
  stage = value; clearTimeout(timer)
  timer = setTimeout(() => fail(new Failure('STAGE_DEADLINE')), 60000)
}
function fail(error) {
  if (failed) return
  failed = true
  const reason = error instanceof Failure && /^[A-Z][A-Z0-9_]{0,95}$/.test(error.message)
    ? error.message : error?.name === 'TimeoutError' ? 'TIMEOUT' : 'PROCESS_ERROR'
  const frames = []
  try {
    for (const line of String(error?.stack ?? '').split('\n')) {
      const match = line.match(/(?:file:\/\/)?\/browser-run\/run-browser\.mjs:([0-9]{1,5}):([0-9]{1,5})\)?$/)
      if (!match) continue
      const value = { line: Number(match[1]), column: Number(match[2]) }
      if (value.line > 0 && value.column > 0 && !frames.some(x => x.line === value.line && x.column === value.column)) frames.push(value)
      if (frames.length === 8) break
    }
  } catch { /* Only numeric own-source frames are optional diagnostics. */ }
  writeSync(1, `MANUAL_SHARE_BROWSER_FAILURE ${stage} ${reason}\n`)
  if (frames.length) writeSync(1, 'MANUAL_SHARE_BROWSER_FRAMES ' + JSON.stringify({ frames }) + '\n')
  process.exit(1)
}
process.on('uncaughtException', fail)
process.on('unhandledRejection', fail)

function crc32(bytes) {
  let value = 0xffffffff
  for (const byte of bytes) {
    value ^= byte
    for (let bit = 0; bit < 8; bit++) value = (value >>> 1) ^ (value & 1 ? 0xedb88320 : 0)
  }
  return (value ^ 0xffffffff) >>> 0
}
function pngChunk(kind, data) {
  const type = Buffer.from(kind), size = Buffer.alloc(4), crc = Buffer.alloc(4)
  size.writeUInt32BE(data.length); crc.writeUInt32BE(crc32(Buffer.concat([type, data])))
  return Buffer.concat([size, type, data, crc])
}
function photoPng() {
  const header = Buffer.alloc(13); header.writeUInt32BE(6, 0); header.writeUInt32BE(4, 4); header[8] = 8; header[9] = 6
  const rows = []
  for (let y = 0; y < 4; y++) {
    const row = Buffer.alloc(25)
    for (let x = 0; x < 6; x++) Buffer.from(x < 3 ? [18, 140, 60, 255] : [220, 50, 30, 255]).copy(row, 1 + x * 4)
    rows.push(row)
  }
  return Buffer.concat([Buffer.from('89504e470d0a1a0a', 'hex'), pngChunk('IHDR', header), pngChunk('IDAT', deflateSync(Buffer.concat(rows))), pngChunk('IEND', Buffer.alloc(0))])
}
const PHOTO = photoPng()
const mutation = request => !['GET', 'HEAD', 'OPTIONS'].includes(request.method())
async function contextFor(locale, actor = null, engine = browser, optional = false) {
  const context = await engine.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true,
    acceptDownloads: true, serviceWorkers: 'block' })
  if (!optional) contexts.push(context)
  imageModes.set(context, 'valid')
  context.setDefaultTimeout(15000); context.setDefaultNavigationTimeout(20000)
  if (!optional) for (const origin of [DASH, LANDING])
    await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin })
  context.on('page', page => page.on('pageerror', () => { pageErrors++ }))
  context.on('request', request => {
    const url = new URL(request.url())
    if ([API, DASH, LANDING].includes(url.origin) && mutation(request) && !(url.origin === API && request.method() === 'POST'
      && (['/auth/login', '/__ci/control'].includes(url.pathname) || Object.values(facts.listings).some(id => url.pathname === '/listings/' + id + '/view')))) unexpectedMutations++
    if (/google-analytics|googletagmanager|facebook\.com\/tr|doubleclick/.test(url.href)) trackingRequests++
  })
  await context.addCookies([{ name: 'synthetic_photo_cookie', value: 'must_be_omitted', domain: 'images.example.com', path: '/', secure: true, sameSite: 'None' }])
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url())
    if (request.url() === facts.photoUrl && request.method() === 'GET') {
      if (request.resourceType() === 'fetch') {
        const headers = await request.allHeaders()
        check(!headers.cookie && !headers.referer, 'GUARDED_PHOTO_OMITS_COOKIE_AND_REFERRER')
        guardedPhotoFetches++
      }
      const headers = { 'content-type': 'image/png', 'content-length': String(PHOTO.length), 'cache-control': 'no-store' }
      if (imageModes.get(context) === 'valid') headers['access-control-allow-origin'] = '*'
      else if (imageModes.get(context) === 'cors_failure') headers['access-control-allow-origin'] = 'https://cors-denied.example.invalid'
      return route.fulfill({ status: 200, headers, body: PHOTO })
    }
    if (![API, DASH, LANDING].includes(url.origin)) {
      blockedExternal++; if (mutation(request)) externalWrites++
      return route.abort('blockedbyclient')
    }
    return route.continue()
  })
  await context.addInitScript(({ locale, native, owned, api, photo, realClipboard }) => {
    if (!owned.includes(location.origin)) return
    localStorage.setItem('ildr_lang', locale)
    localStorage.setItem('ildr_measurement_consent_v1', JSON.stringify({ choice: 'denied', savedAt: Date.now() }))
    const before = navigator.onLine
    if (!before) Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => true })
    const state = { before, modeled: !before, clipboard: [], shares: [], shareMode: 'cancel', fetches: [] }
    Object.defineProperty(window, '__manual', { value: state })
    if (realClipboard) {
      const clipboard = navigator.clipboard, write = clipboard.writeText.bind(clipboard)
      Object.defineProperty(clipboard, 'writeText', { configurable: true, value: async text => { await write(text); state.clipboard.push(text) } })
    }
    Object.defineProperty(navigator, 'canShare', { configurable: true, value: native ? data => !!data.files?.length : undefined })
    Object.defineProperty(navigator, 'share', { configurable: true, value: native ? async data => {
      const files = []
      for (const file of data.files ?? []) {
        const hash = await crypto.subtle.digest('SHA-256', await file.arrayBuffer())
        files.push({ name: file.name, type: file.type, bytes: file.size, sha256: [...new Uint8Array(hash)].map(x => x.toString(16).padStart(2, '0')).join('') })
      }
      state.shares.push({ keys: Object.keys(data).sort(), title: data.title, text: data.text, files })
      if (state.shareMode === 'cancel') throw new DOMException('Synthetic native cancellation', 'AbortError')
    } : undefined })
    const original = window.fetch.bind(window)
    window.fetch = (input, init) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      if (url.startsWith(api + '/listings/') && new Headers(init?.headers).get('Accept') === 'application/json')
        state.fetches.push({ kind: 'listing', cache: init?.cache, credentials: init?.credentials, signal: init?.signal instanceof AbortSignal })
      if (url === photo) state.fetches.push({ kind: 'photo', cache: init?.cache, credentials: init?.credentials,
        mode: init?.mode, redirect: init?.redirect, referrerPolicy: init?.referrerPolicy, signal: init?.signal instanceof AbortSignal })
      return original(input, init)
    }
  }, { locale, native: actor === 'admin', owned: [DASH, LANDING], api: API, photo: facts.photoUrl, realClipboard: !optional })
  if (actor) {
    const response = await context.request.post(API + '/auth/login', { data: accounts[actor], headers: { Origin: DASH }, timeout: 15000, maxRedirects: 0 })
    try { check(response.status() === 200 && (await context.cookies(API)).some(x => x.name === 'ildr_token' && x.httpOnly), 'REAL_COOKIE_LOGIN_REQUIRED') }
    finally { await response.dispose() }
    const me = await read(context, '/auth/me')
    check(me.id === facts.ids[actor] && me.role === facts.roles[actor], 'ACTUAL_CURRENT_ROLE_REQUIRED'); actualLogins++
  }
  return context
}
async function read(context, path, expected = 200, boundary = null) {
  check(path.startsWith('/') && !path.startsWith('//'), 'FIXED_OWNED_READ_REQUIRED')
  const response = await context.request.get(API + path, { headers: boundary ? { 'x-ci-boundary': boundary } : {}, timeout: 15000, maxRedirects: 0 })
  try { check(response.status() === expected, 'ACTUAL_READ_STATUS_REQUIRED'); if (boundary) boundaryChecks++; return await response.json() }
  finally { await response.dispose() }
}
async function control(context, action) {
  const response = await context.request.post(API + '/__ci/control', { headers: { 'x-ci-control': facts.controlSecret }, data: { action }, timeout: 15000, maxRedirects: 0 })
  try { check(response.status() === 200 && (await response.json()).status === 'PASS', 'REAL_OWNED_CONTROL_REQUIRED'); controlTransitions++ }
  finally { await response.dispose() }
}
async function foreground(page, optional = false) {
  await page.bringToFront(); await page.waitForFunction(() => document.visibilityState === 'visible' && navigator.onLine)
  if (!optional && !presence.has(page)) {
    const value = await page.evaluate(() => ({ before: window.__manual.before, modeled: window.__manual.modeled, after: navigator.onLine, visible: document.visibilityState === 'visible' }))
    check(Object.values(value).every(x => typeof x === 'boolean') && value.modeled === !value.before && value.after && value.visible, 'LOOPBACK_PRESENCE_REQUIRED')
    console.log('MANUAL_SHARE_LOOPBACK_STATE ' + JSON.stringify(value)); presence.add(page); presenceCount++
  }
}
async function navigate(page, origin, path, optional = false) {
  check([DASH, LANDING].includes(origin) && path.startsWith('/') && !path.startsWith('//'), 'FIXED_OWNED_NAVIGATION_REQUIRED')
  await page.goto(origin + path, { waitUntil: 'domcontentloaded' }); await foreground(page, optional)
}
async function overflowCensus(page, panel, label, width) {
  // Failure-only, numeric DOM facts and fixed source-owned class tokens; no text/URLs/IDs/styles.
  const value = await panel.evaluate((share, { label, width }) => {
    const integer = value => Math.max(-100000, Math.min(100000, Math.round(Number.isFinite(value) ? value : 0)))
    const allowedTags = new Set(['MAIN', 'DIV', 'SECTION', 'ARTICLE', 'DETAILS', 'SUMMARY', 'FORM', 'INPUT', 'TEXTAREA', 'SELECT', 'BUTTON', 'A', 'PRE', 'IMG', 'IFRAME', 'H1', 'H2', 'H3', 'P', 'SPAN', 'LABEL', 'SVG'])
    const allowedClasses = ['min-w-0', 'max-w-full', 'w-full', 'max-w-310', 'grid', 'grid-cols-1', 'grid-cols-2', 'sm:grid-cols-4', 'lg:grid-cols-[1fr_360px]',
      'gap-3', 'gap-10', 'p-4', 'p-5.5', 'px-6', 'flex', 'inline-flex', 'flex-wrap', 'shrink-0', 'whitespace-pre-wrap', 'break-words',
      '[overflow-wrap:anywhere]', 'overflow-hidden', 'overflow-x-auto', 'rounded-xl', 'rounded-2xl', 'text-[18px]', 'font-sans', 'listing-prose']
    const nodes = [...document.body.querySelectorAll('*')].slice(0, 2500), rows = []
    for (const node of nodes) {
      const box = node.getBoundingClientRect()
      if (box.width <= 0 || box.height <= 0 || !(box.right > innerWidth + 1 || box.left < -1 || node.scrollWidth > node.clientWidth + 1)) continue
      rows.push({ tag: allowedTags.has(node.tagName) ? node.tagName : 'OTHER', scope: node === share || share.contains(node) ? 'share' : node.contains(share) ? 'ancestor' : 'page',
        classes: allowedClasses.filter(token => node.classList.contains(token)).slice(0, 8), width: integer(box.width), left: integer(box.left), right: integer(box.right),
        client: integer(node.clientWidth), scroll: integer(node.scrollWidth) })
    }
    rows.sort((a, b) => b.right - a.right || b.scroll - b.client - (a.scroll - a.client) || a.width - b.width)
    return { label, width, inner: integer(innerWidth), client: integer(document.documentElement.clientWidth), scroll: integer(document.documentElement.scrollWidth),
      scanned: nodes.length, matches: rows.length, rows: rows.slice(0, 24) }
  }, { label, width })
  writeSync(1, 'MANUAL_SHARE_OVERFLOW_CENSUS ' + JSON.stringify(value) + '\n')
}
async function geometry(page, panel, label, optional = false) {
  const values = []
  for (const width of [320, 390]) {
    advance(label + '_geometry_' + width)
    await page.setViewportSize({ width, height: 844 }); await foreground(page, optional)
    const value = await page.evaluate(() => ({ width: innerWidth, document: document.documentElement.scrollWidth }))
    if (!(value.width === width && value.document <= width + 1)) await overflowCensus(page, panel, label, width).catch(() => {})
    check(value.width === width && value.document <= width + 1, 'VIEWPORT_OVERFLOW')
    for (const item of await panel.locator('input,textarea,select,button,a,summary').all()) {
      if (!await item.isVisible()) continue
      const box = await item.boundingBox()
      if (!(box && box.x >= -1 && box.x + box.width <= width + 1)) await overflowCensus(page, panel, label, width).catch(() => {})
      check(box && box.x >= -1 && box.x + box.width <= width + 1, 'CONTROL_OVERFLOW')
    }
    if (!optional) { geometries.push({ label, ...value }); console.log('MANUAL_SHARE_BROWSER_GEOMETRY ' + JSON.stringify({ label, ...value })) }
    values.push(value)
  }
  return values
}
async function associatedSelect(scope, label) {
  const candidates = scope.locator('select'), count = await candidates.count(), found = []
  for (let index = 0; index < count; index++) if (await candidates.nth(index).evaluate((control, expected) => {
    const labels = [...(control.labels ?? [])]
    return labels.some(item => { const clone = item.cloneNode(true); clone.querySelectorAll('input,textarea,select,button').forEach(node => node.remove()); return clone.textContent?.trim() === expected })
  }, label)) found.push(candidates.nth(index))
  check(found.length === 1, 'UNIQUE_ASSOCIATED_PUBLIC_SELECT_REQUIRED'); return found[0]
}
async function open(panel, copy) {
  check(await panel.count() === 1, 'UNIQUE_MANUAL_SHARE_PANEL_REQUIRED')
  if (!await panel.evaluate(node => node.open)) await panel.locator('summary').click()
  await panel.getByRole('heading', { name: copy.heading, exact: true }).waitFor()
}
async function preview(panel, content, copy, native, count = true) {
  const text = [content.title, ...content.facts, copy[content.audience], content.url].join('\n')
  await panel.locator('pre').waitFor()
  check(await panel.locator('pre').textContent() === text && !text.includes(facts.privateMarker) && !text.includes('private-owner@example.com')
    && !text.includes('19.7500000') && !text.includes('owned_context='), 'EXACT_REVIEWED_ALLOWLIST_PREVIEW_REQUIRED')
  check(await panel.getByText(copy[content.audience], { exact: true }).isVisible()
    && await panel.getByText(copy.manual, { exact: true }).isVisible() && await panel.getByText(copy.handoff, { exact: true }).isVisible(), 'AUDIENCE_AND_MANUAL_HANDOFF_DISCLOSURE_REQUIRED')
  const expected = { whatsapp: 'https://wa.me/?text=' + encodeURIComponent(text), facebook: 'https://www.facebook.com/sharer/sharer.php?u=' + encodeURIComponent(content.url),
    sms: 'sms:?body=' + encodeURIComponent(text), email: 'mailto:?subject=' + encodeURIComponent(content.title) + '&body=' + encodeURIComponent(text),
    linkedin: 'https://www.linkedin.com/sharing/share-offsite/?url=' + encodeURIComponent(content.url), x: 'https://twitter.com/intent/tweet?text=' + encodeURIComponent(text) }
  for (const [kind, href] of Object.entries(expected)) {
    check(await panel.getByRole('link', { name: copy[kind], exact: true }).getAttribute('href') === href, 'EXACT_COMPOSER_HREF_WITHOUT_DELIVERY_REQUIRED')
    if (count) composerChecks++
  }
  check(await panel.getByRole('link', { name: copy.instagram, exact: true }).getAttribute('href') === 'https://www.instagram.com/', 'MANUAL_INSTAGRAM_DESTINATION_REQUIRED')
  check(await button(panel, copy.native).count() === (native ? 1 : 0), 'ACTUAL_NATIVE_CAPABILITY_FALLBACK_REQUIRED')
  if (count) for (const [label, value] of [[copy.copyLink, content.url], [copy.copyCaption, text]]) {
    const before = await panel.page().evaluate(() => window.__manual.clipboard.length)
    await button(panel, label).click(); await panel.page().waitForFunction(n => window.__manual.clipboard.length === n + 1, before)
    check(await panel.page().evaluate(() => window.__manual.clipboard.at(-1)) === value
      && await panel.page().evaluate(() => navigator.clipboard.readText()) === value, 'EXACT_REAL_OWNED_CLIPBOARD_WRITE_AND_READ_REQUIRED'); clipboardWrites++
  }
  return text
}
async function nativeHandoff(page, panel, copy, content, text, mode, png = null) {
  await page.evaluate(value => { window.__manual.shareMode = value }, mode)
  const before = await page.evaluate(() => window.__manual.shares.length)
  await button(panel, png ? copy.shareImage : copy.native).click()
  await page.waitForFunction(n => window.__manual.shares.length === n + 1, before)
  const actual = await page.evaluate(() => window.__manual.shares.at(-1))
  check(actual.title === content.title && actual.text === text && JSON.stringify(actual.keys) === JSON.stringify(png ? ['files', 'text', 'title'] : ['text', 'title']), 'EXACT_INSTRUMENTED_NATIVE_FIELDS_REQUIRED')
  check(png ? actual.files.length === 1 && actual.files[0].name === `ilovedrrealty-story-${content.locale}.png`
    && actual.files[0].type === 'image/png' && actual.files[0].bytes === png.length && actual.files[0].sha256 === digest(png) : actual.files.length === 0, 'EXACT_PREPARED_NATIVE_FILE_REQUIRED')
  await panel.getByText(mode === 'cancel' ? copy.canceled : copy.nativeClosed, { exact: true }).waitFor(); nativeCalls++
}
async function download(page, panel, label, filename) {
  const pending = page.waitForEvent('download')
  await button(panel, label).click()
  const item = await pending
  check(item.suggestedFilename() === filename && await item.failure() === null, 'ACTUAL_DOWNLOAD_NAME_AND_STATUS_REQUIRED')
  const stream = await item.createReadStream(), chunks = []
  let size = 0
  for await (const chunk of stream) { size += chunk.length; check(size <= 8 * 1024 * 1024, 'BOUNDED_ACTUAL_DOWNLOAD_REQUIRED'); chunks.push(chunk) }
  return Buffer.concat(chunks)
}
async function pngProof(page, bytes, format, photo) {
  check(bytes.subarray(0, 8).equals(Buffer.from('89504e470d0a1a0a', 'hex')), 'NATIVE_PNG_SIGNATURE_REQUIRED')
  let offset = 8, headers = 0, data = 0, end = false
  while (offset < bytes.length) {
    check(offset + 12 <= bytes.length, 'COMPLETE_PNG_CHUNKS_REQUIRED')
    const size = bytes.readUInt32BE(offset), kind = bytes.toString('ascii', offset + 4, offset + 8)
    check(size <= bytes.length - offset - 12 && crc32(bytes.subarray(offset + 4, offset + 8 + size)) === bytes.readUInt32BE(offset + 8 + size), 'ACTUAL_PNG_CRC_REQUIRED')
    if (kind === 'IHDR') { headers++; check(size === 13 && bytes.readUInt32BE(offset + 8) === 1080 && bytes.readUInt32BE(offset + 12) === (format === 'story' ? 1920 : 1080), 'ACTUAL_CANVAS_PNG_SIZE_REQUIRED') }
    if (kind === 'IDAT') data++
    offset += size + 12
    if (kind === 'IEND') { check(size === 0 && offset === bytes.length, 'EXACT_PNG_END_REQUIRED'); end = true }
  }
  check(headers === 1 && data > 0 && end, 'REAL_ENCODED_PNG_REQUIRED')
  const pixels = await page.evaluate(async ({ bytes, story }) => {
    const bitmap = await createImageBitmap(new Blob([new Uint8Array(bytes)], { type: 'image/png' }))
    const canvas = document.createElement('canvas'); canvas.width = bitmap.width; canvas.height = bitmap.height
    const ctx = canvas.getContext('2d'); ctx.drawImage(bitmap, 0, 0)
    const pixel = (x, y) => [...ctx.getImageData(x, y, 1, 1).data]
    const result = { width: bitmap.width, height: bitmap.height, navy: pixel(0, 0), pale: pixel(73, story ? 551 : 371), left: pixel(300, story ? 800 : 500), right: pixel(800, story ? 800 : 500) }
    bitmap.close(); canvas.width = 1; canvas.height = 1; return result
  }, { bytes: [...bytes], story: format === 'story' })
  check(pixels.width === 1080 && pixels.height === (format === 'story' ? 1920 : 1080)
    && JSON.stringify(pixels.navy) === '[8,27,59,255]' && JSON.stringify(pixels.pale) === '[232,239,245,255]', 'DECODED_BRAND_AND_CONTAIN_LETTERBOX_PIXELS_REQUIRED')
  if (photo) check(JSON.stringify(pixels.left) === '[18,140,60,255]' && JSON.stringify(pixels.right) === '[220,50,30,255]', 'WHOLE_EXPLICIT_PHOTO_PIXELS_REQUIRED')
}
async function prepareAndDownload(page, panel, copy, locale, format, photo, text, optional = false) {
  const ready = panel.getByRole('img', { name: copy.imageAlt, exact: true })
  await button(panel, copy.prepareImage).click(); await ready.waitFor()
  check((await ready.getAttribute('src')).startsWith('blob:'), 'LOCAL_PREPARED_PREVIEW_ONLY_REQUIRED')
  const png = await download(page, panel, copy.download, `ilovedrrealty-${format}-${locale}.png`)
  await pngProof(page, png, format, photo)
  if (!optional) {
    pngDownloads++
    const caption = await download(page, panel, copy.captionFile, `ilovedrrealty-caption-${locale}.txt`)
    check(caption.equals(Buffer.from(text + '\n', 'utf8')), 'EXACT_UTF8_CAPTION_FILE_REQUIRED'); captionDownloads++
  }
  return png
}
async function propertyContent(page, locale, copy, kind, amount = facts.basePrice) {
  const currency = kind === 'both' ? 'USD' : 'DOP'
  const amounts = kind === 'both' ? [amount, facts.monthlyUSD, facts.dailyUSD] : [facts.monthlyDOP, facts.dailyDOP]
  const money = await page.evaluate(({ locale, currency, amounts }) => amounts.map(value => new Intl.NumberFormat(locale === 'es' ? 'es-DO' : 'en-US',
    { style: 'currency', currency, currencyDisplay: 'code', maximumFractionDigits: 2 }).format(value)), { locale, currency, amounts })
  return { title: kind === 'both' ? 'Synthetic ocean villa' : copy.types.villa, locale, audience: 'public',
    facts: kind === 'both' ? [copy.both + ' · Cabarete', copy.price + ': ' + money[0], copy.monthly + ': ' + money[1], copy.daily + ': ' + money[2], copy.beds + ': 3 · ' + copy.baths + ': 3']
      : [copy.forRent, copy.monthly + ': ' + money[0], copy.daily + ': ' + money[1], copy.beds + ': 3 · ' + copy.baths + ': 3'],
    url: 'https://ilovedrrealty.com/listing/' + facts.listings[kind] + '/' }
}
async function propertyJourney(locale, copy, context, page) {
  advance(locale + '_property_open')
  const view = page.waitForResponse(response => response.url() === API + '/listings/' + facts.listings.both + '/view' && response.request().method() === 'POST')
  await navigate(page, LANDING, '/listing/' + facts.listings.both + '/')
  check((await view).status() === 204, 'REAL_PROPERTY_VIEW_REQUIRED')
  const panel = page.locator('details').filter({ has: page.locator('summary').filter({ hasText: new RegExp('^' + copy.open + '$') }) }).first()
  await open(panel, copy)
  const content = await propertyContent(page, locale, copy, 'both'), text = await preview(panel, content, copy, true)
  await geometry(page, panel, locale + '_property')
  await nativeHandoff(page, panel, copy, content, text, 'cancel'); await nativeHandoff(page, panel, copy, content, text, 'resolve')
  check(await (await associatedSelect(panel, copy.choosePhoto)).inputValue() === '', 'PHOTO_EXPLICIT_DEFAULT_NONE_REQUIRED')
  advance(locale + '_square_no_photo')
  await prepareAndDownload(page, panel, copy, locale, 'post', false, text)
  await geometry(page, panel, locale + '_assets')
  await (await associatedSelect(panel, copy.format)).selectOption('story')
  await (await associatedSelect(panel, copy.choosePhoto)).selectOption(facts.photoUrl)
  check(await panel.getByRole('img', { name: copy.selectedPhoto, exact: true }).count() === 0, 'NO_DIRECT_REMOTE_PHOTO_PREVIEW_REQUIRED')
  imageModes.set(context, 'cors_failure')
  advance(locale + '_guarded_photo_failure')
  await button(panel, copy.prepareImage).click(); await panel.getByText(copy.imageFailed, { exact: true }).waitFor()
  check(await panel.getByRole('img', { name: copy.imageAlt, exact: true }).count() === 0, 'FAILED_IMAGE_HAS_NO_STALE_ASSET'); imageFailures++
  imageModes.set(context, 'valid')
  advance(locale + '_story_explicit_photo')
  const png = await prepareAndDownload(page, panel, copy, locale, 'story', true, text)
  await nativeHandoff(page, panel, copy, content, text, 'resolve', png)
  advance(locale + '_actual_changed_listing')
  await control(context, 'price_raise'); await button(panel, copy.prepareImage).click()
  await panel.getByText(copy.changed, { exact: true }).waitFor()
  await preview(panel, await propertyContent(page, locale, copy, 'both', facts.changedPrice), copy, true, false)
  check(await panel.getByRole('img', { name: copy.imageAlt, exact: true }).count() === 0, 'CHANGED_LISTING_INVALIDATES_ASSET'); changedChecks++
  await control(context, 'price_restore'); await panel.locator('summary').click(); await open(panel, copy)
  await preview(panel, content, copy, true, false)
  advance(locale + '_actual_withdrawal')
  await control(context, 'withdraw'); await button(panel, copy.prepareImage).click()
  await panel.getByText(copy.imageFailed, { exact: true }).waitFor()
  check(await panel.locator('pre').count() === 0 && await button(panel, copy.copyLink).count() === 0, 'WITHDRAWN_LISTING_REMOVES_SHARE_PROJECTION'); withdrawalChecks++
  await control(context, 'restore'); await button(panel, copy.retry).click(); await preview(panel, content, copy, true, false)
  advance(locale + '_fresh_read_failure')
  await panel.locator('summary').click()
  let abort = true
  const endpoint = API + '/listings/' + facts.listings.both
  const failedRead = async route => {
    if (abort && route.request().headers().accept === 'application/json') { abort = false; readAborts++; return route.abort('failed') }
    return route.continue()
  }
  await page.route(endpoint, failedRead); await open(panel, copy)
  await panel.getByText(copy.unavailable, { exact: true }).waitFor()
  check(await panel.locator('pre').count() === 0 && await button(panel, copy.copyLink).count() === 0, 'FAILED_FRESH_READ_HAS_NO_CACHED_PROJECTION')
  await button(panel, copy.retry).click(); await preview(panel, content, copy, true, false); await page.unroute(endpoint, failedRead)
  advance(locale + '_stale_close')
  await panel.locator('summary').click()
  let held, release, finished
  const captured = new Promise(resolve => { held = resolve }), gate = new Promise(resolve => { release = resolve })
  const done = new Promise(resolve => { finished = resolve })
  const hold = async route => {
    const request = route.request(), headers = await request.allHeaders()
    const cookiePresent = Object.hasOwn(headers, 'cookie'), authorizationPresent = Object.hasOwn(headers, 'authorization')
    check(request.method() === 'GET' && headers.accept === 'application/json' && headers.origin === LANDING
      && !cookiePresent && !authorizationPresent, 'ONLY_EXACT_ANONYMOUS_FRESH_GET_HOLD_REQUIRED')
    // APIRequestContext otherwise adds the signed browser jar to this genuinely anonymous browser request.
    const replayHeaders = { ...headers, cookie: '' }
    const response = await route.fetch({ headers: replayHeaders, maxRetries: 0, timeout: 15000 })
    if (response.status() !== 200) writeSync(1, 'MANUAL_SHARE_HELD_RESPONSE ' + JSON.stringify({ phase: locale + '_stale_close', status: response.status(),
      cookie_present: cookiePresent, authorization_present: authorizationPresent, origin_expected: headers.origin === LANDING,
      accept_expected: headers.accept === 'application/json', replay_cookie_empty: replayHeaders.cookie === '' }) + '\n')
    check(response.status() === 200, 'REAL_HELD_PUBLIC_RESPONSE_REQUIRED'); held()
    await gate; await route.abort('failed'); await response.dispose(); finished()
  }
  await page.route(endpoint, hold); await open(panel, copy); await captured
  await panel.locator('summary').click(); release(); await done; await page.unroute(endpoint, hold)
  await panel.locator('pre').waitFor({ state: 'detached' })
  check(!await panel.evaluate(node => node.open) && await panel.locator('pre').count() === 0, 'CLOSED_PREVIEW_CANNOT_PUBLISH_HELD_RESULT')
  check(await panel.locator('summary').evaluate(node => node === document.activeElement), 'CLOSE_FOCUS_REMAINS_ON_SUMMARY'); staleCloses++
  const fetches = await page.evaluate(() => window.__manual.fetches)
  const reads = fetches.filter(item => item.kind === 'listing'), photos = fetches.filter(item => item.kind === 'photo')
  check(reads.length === 11 && reads.every(item => item.cache === 'no-store' && item.credentials === 'omit' && item.signal), 'ACTUAL_LISTING_FETCH_ARGUMENTS_REQUIRED')
  check(photos.length === 2 && photos.every(item => item.cache === 'no-store' && item.credentials === 'omit' && item.signal && item.mode === 'cors'
    && item.redirect === 'error' && item.referrerPolicy === 'no-referrer'), 'ACTUAL_GUARDED_PHOTO_FETCH_ARGUMENTS_REQUIRED')
  check((await context.cookies(facts.photoUrl)).some(item => item.name === 'synthetic_photo_cookie'), 'EXISTING_IMAGE_COOKIE_CONTROL_REQUIRED')
  freshFetches += reads.length; journeys.push(locale + '_property')
}
async function simpleJourney(page, origin, path, scope, label, content, copy, mutate = null) {
  advance(label)
  await navigate(page, origin, path)
  const panel = typeof scope === 'function' ? scope(page) : page.locator('main details').first()
  if (mutate) await mutate(page)
  await open(panel, copy); await preview(panel, content, copy, false); await geometry(page, panel, label)
  await panel.locator('summary').press('Escape')
  check(!await panel.evaluate(node => node.open) && await panel.locator('summary').evaluate(node => node === document.activeElement), 'ESCAPE_CLOSE_AND_FOCUS_REQUIRED')
  journeys.push(label)
}
async function localeRun(locale) {
  advance(locale + '_accounts')
  const dictionary = JSON.parse(await readFile('/browser-run/' + locale + '.json', 'utf8')), copy = dictionary.share
  const signed = {}
  for (const actor of ['admin', 'owner', 'buyer', 'agent']) signed[actor] = await contextFor(locale, actor)
  const anonymous = await contextFor(locale), pages = {}
  for (const [actor, context] of Object.entries({ ...signed, anonymous })) pages[actor] = await context.newPage()
  await read(anonymous, '/listings/' + facts.listings.both, 200, 'public_both')
  await read(anonymous, '/listings/' + facts.listings.rent, 200, 'public_rent')
  await read(anonymous, '/listings/' + facts.listings.archived, 404, 'archived')
  for (const actor of ['anonymous', 'buyer', 'owner', 'agent', 'admin']) await read(actor === 'anonymous' ? anonymous : signed[actor], '/site/realtor-tools',
    ['anonymous', 'buyer', 'owner'].includes(actor) ? actor === 'anonymous' ? 401 : 403 : 200, 'research_' + actor)
  await propertyJourney(locale, copy, signed.admin, pages.admin)
  advance(locale + '_rent')
  const rentView = pages.anonymous.waitForResponse(response => response.url() === API + '/listings/' + facts.listings.rent + '/view' && response.request().method() === 'POST')
  await navigate(pages.anonymous, LANDING, '/listing/' + facts.listings.rent + '/')
  check((await rentView).status() === 204, 'ACTUAL_RENT_VIEW_REQUIRED')
  const rent = pages.anonymous.locator('details').filter({ has: pages.anonymous.locator('summary').filter({ hasText: new RegExp('^' + copy.open + '$') }) }).first()
  await open(rent, copy); await preview(rent, await propertyContent(pages.anonymous, locale, copy, 'rent'), copy, false)
  check(!(await rent.locator('pre').textContent()).includes('Cabarete') && (await rent.locator('pre').textContent()).includes(copy.daily)
    && !(await rent.locator('pre').textContent()).includes(locale === 'es' ? 'Por noche' : 'Per night'), 'HIDDEN_LOCATION_AND_LITERAL_DAY_UNIT_REQUIRED')
  await geometry(pages.anonymous, rent, locale + '_rent')
  const rentFetches = await pages.anonymous.evaluate(() => window.__manual.fetches.filter(item => item.kind === 'listing'))
  check(rentFetches.length === 1 && rentFetches[0].credentials === 'omit' && rentFetches[0].cache === 'no-store', 'ANONYMOUS_RENT_FRESH_ARGUMENTS_REQUIRED')
  freshFetches++; journeys.push(locale + '_rent')
  const generic = (title, url, audience = 'public') => ({ title, facts: [], url, audience })
  const resource = generic(facts.resource.title[locale], 'https://ilovedrrealty.com/blog/#resource-' + facts.resource.id)
  await simpleJourney(pages.anonymous, LANDING, '/blog/#resource-' + facts.resource.id,
    page => page.locator('#resource-' + facts.resource.id + ' details'), locale + '_public_resource', resource, copy)
  await simpleJourney(pages.anonymous, LANDING, '/calculator/?private_input=999888777', null, locale + '_calculator',
    generic(copy.calculator, 'https://ilovedrrealty.com/calculator/'), copy, async page => {
      const input = page.locator('input[type=number]').first(); await input.waitFor(); await input.fill('777777'); await input.press('Tab')
    })
  await simpleJourney(pages.buyer, DASH, '/resources?workspace=buyer', page => page.locator('main article').filter({ has: page.getByRole('heading', { name: facts.resource.title[locale], exact: true }) }).locator('details'),
    locale + '_dashboard_resource', resource, copy)
  await simpleJourney(pages.buyer, DASH, '/buyer-tools?workspace=buyer', page => page.locator('section[aria-labelledby="area-converter-heading"] details'), locale + '_area',
    generic(copy.area, 'https://app.ilovedrrealty.com/buyer-tools#area-converter-heading', 'member'), copy, async page => {
      const section = page.locator('section[aria-labelledby="area-converter-heading"]'); await section.locator('input').fill('876543')
      await section.locator('select').first().selectOption('hectares')
    })
  for (const [actor, workspace] of [['buyer', 'Buyer'], ['owner', 'Owner'], ['agent', 'Realtor']]) await simpleJourney(pages[actor], DASH,
    '/' + workspace.toLowerCase() + '-tools?workspace=' + workspace.toLowerCase(), null, locale + '_' + workspace.toLowerCase() + '_hub',
    generic(copy.hub, 'https://app.ilovedrrealty.com/' + workspace.toLowerCase() + '-tools', 'member'), copy)
  await simpleJourney(pages.agent, DASH, '/research?workspace=realtor', page => page.locator('main article').filter({ has: page.getByRole('heading', { name: facts.privateMarker, exact: true }) }).locator('details'),
    locale + '_research', generic(copy.researchTitle, 'https://app.ilovedrrealty.com/research', 'research'), copy)
}
async function optionalWebKit() {
  let available = false
  try { await access(webkit.executablePath()); available = true }
  catch (error) { check(error?.code === 'ENOENT', 'WEBKIT_AVAILABILITY_READ_FAILED') }
  if (!available) return { available: false, journeys: 0, geometry: 0, png_downloads: 0, native_delivery: false }
  advance('webkit_launch')
  const engine = await webkit.launch({ headless: true })
  try {
    const context = await contextFor('en', null, engine, true), page = await context.newPage()
    const copy = JSON.parse(await readFile('/browser-run/en.json', 'utf8')).share
    await navigate(page, LANDING, '/calculator/', true)
    const panel = page.locator('main details').first(); await open(panel, copy)
    const content = { title: copy.calculator, facts: [], url: 'https://ilovedrrealty.com/calculator/', audience: 'public' }
    const text = await preview(panel, content, copy, false, false)
    await prepareAndDownload(page, panel, copy, 'en', 'post', false, text, true)
    await geometry(page, panel, 'webkit_calculator', true)
    await panel.locator('summary').press('Escape'); check(!await panel.evaluate(node => node.open) && await panel.locator('summary').evaluate(node => node === document.activeElement), 'WEBKIT_ESCAPE_FOCUS_REQUIRED')
    await context.close()
    return { available: true, journeys: 1, geometry: 2, png_downloads: 1, native_delivery: false }
  } finally { await engine.close() }
}

try {
  const path = process.env.ILDR_BROWSER_FIXTURES
  check(path === '/browser-fixtures/fixtures.json', 'EXACT_PRIVATE_FIXTURE_PATH_REQUIRED')
  const info = await lstat(path)
  check(info.isFile() && !info.isSymbolicLink() && info.size > 0 && info.size < 65536 && (info.mode & 0o077) === 0, 'PRIVATE_FIXTURE_BOUNDS_REQUIRED')
  const fixture = JSON.parse(await readFile(path, 'utf8'))
  check(fixture.scope === 'manual-sharing-only', 'EXACT_FIXTURE_SCOPE_REQUIRED'); ({ accounts, facts } = fixture)
  browser = await chromium.launch({ headless: true })
  await localeRun('en'); await localeRun('es')
  const webkitResult = await optionalWebKit()
  advance('authoritative_metrics')
  check(journeys.length === 20 && new Set(journeys).size === 20 && geometries.length === 44
    && new Set(geometries.map(x => x.label + ':' + x.width)).size === 44 && contexts.length === 10 && presenceCount === 10, 'EXACT_FINITE_JOURNEY_GEOMETRY_CONTEXTS_REQUIRED')
  check(actualLogins === 8 && boundaryChecks === 16 && controlTransitions === 8 && clipboardWrites === 40 && composerChecks === 120
    && nativeCalls === 6 && pngDownloads === 4 && captionDownloads === 4 && imageFailures === 2 && readAborts === 2 && staleCloses === 2
    && freshFetches === 24 && guardedPhotoFetches === 4 && changedChecks === 2 && withdrawalChecks === 2, 'EXACT_MANUAL_SHARE_ACTION_METRICS_REQUIRED')
  check(pageErrors === 0 && unexpectedMutations === 0 && externalWrites === 0 && trackingRequests === 0, 'NO_ERRORS_OR_UNEXPECTED_WRITES_REQUIRED')
  console.log('MANUAL_SHARE_BROWSER_RESULT ' + JSON.stringify({ status: 'PASS', locales: ['en', 'es'], journeys: 20, geometry: 44, contexts: 10,
    actual_logins: 8, api_boundary_checks: 16, clipboard_writes: 40, clipboard_mode: 'owned_chromium_actual_api', composer_hrefs: 120, instrumented_native_calls: 6,
    native_share_mode: 'instrumented_no_os_delivery', png_downloads: 4, caption_downloads: 4, guarded_image_failures: 2,
    fresh_read_aborts: 2, stale_close_checks: 2, source_change_checks: 2, withdrawal_checks: 2, listing_fetch_options: 24,
    guarded_photo_fetches: 4, control_transitions: 8, page_errors: pageErrors, unexpected_mutations: unexpectedMutations,
    external_writes: externalWrites, tracking_requests: trackingRequests, blocked_external_requests: blockedExternal, webkit: webkitResult }))
  clearTimeout(timer)
  for (const context of contexts) await context.close()
  await browser.close()
} catch (error) { fail(error) }
