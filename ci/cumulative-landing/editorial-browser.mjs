import { readFile } from 'node:fs/promises'

const directory = '/local-guides/'
const reader = '/local-guides/read/'
const slug = 'synthetic-coastal-neighborhood'
const publishedAt = '2026-10-25T12:00:00Z'
const content = {
  en: {
    title: 'Synthetic Coastal Neighborhood', summary: 'A synthetic neighborhood guide.',
    body: 'SYNTHETIC_EN_FIRST_PUBLIC_REVISION', updated: 'SYNTHETIC_EN_SECOND_PUBLIC_REVISION',
    missing: 'This page is unavailable.', source: 'Synthetic source EN',
  },
  es: {
    title: 'Barrio Costero Sintético', summary: 'Una guía sintética del vecindario.',
    body: 'SYNTHETIC_ES_FIRST_PUBLIC_REVISION', updated: 'SYNTHETIC_ES_SECOND_PUBLIC_REVISION',
    missing: 'Esta página no está disponible.', source: 'Fuente sintética ES',
  },
}

function check(condition, failure) {
  if (!condition) throw new Error(failure)
}

export function createEditorialFixture(api) {
  let publishedRevision = 0
  const seen = new Set()
  return {
    setPublishedRevision(next) {
      check([0, 1, 2].includes(next), 'FIXTURE_REVISION_REFUSED')
      publishedRevision = next
    },
    responseFor(request) {
      if (request.method !== 'GET') return null
      const url = new URL(request.url, api)
      const route = url.pathname
      if (route !== '/site/editorial' && !route.startsWith('/site/editorial/')) return null
      const locale = url.searchParams.get('locale')
      if (!Object.hasOwn(content, locale)) return { status: 400, data: { detail: 'Fixture locale required' } }
      const entry = content[locale]
      const card = { slug, kind: 'neighborhood', title: entry.title, summary: entry.summary, published_at: publishedAt }
      if (route === '/site/editorial') {
        if (url.searchParams.get('page') !== '1') return { status: 400, data: { detail: 'Fixture page required' } }
        seen.add(`list:${publishedRevision}:${locale}`)
        const items = publishedRevision ? [card] : []
        return { status: 200, data: { items, total: items.length } }
      }
      if (route !== `/site/editorial/${slug}`) return { status: 404, data: { detail: 'Content unavailable' } }
      seen.add(`detail:${publishedRevision}:${locale}`)
      if (!publishedRevision) return { status: 404, data: { detail: 'Content unavailable' } }
      return { status: 200, data: {
        ...card, body: publishedRevision === 1 ? entry.body : entry.updated,
        sources: [{ label: entry.source, url: `https://example.invalid/source/${locale}` }],
      } }
    },
    saw(kind, phase, locale) { return seen.has(`${kind}:${phase}:${locale}`) },
  }
}

function detailURL(landing, locale) {
  return `${landing}${reader}?slug=${slug}&lang=${locale}`
}

function expectedResponse(page, api, route, locale, status) {
  return page.waitForResponse(response => {
    const url = new URL(response.url())
    return url.origin === api && url.pathname === route && response.request().method() === 'GET'
      && url.searchParams.getAll('locale').length === 1 && url.searchParams.get('locale') === locale
      && response.status() === status
  })
}

async function checkedPublicResponse(responsePending) {
  const response = await responsePending
  check(/(?:^|,\s*)no-store(?:,|$)/i.test(response.headers()['cache-control'] ?? ''), 'PUBLIC_NO_STORE_REQUIRED')
  return response
}

async function readDirectory(page, { landing, api }, locale, published) {
  const response = expectedResponse(page, api, '/site/editorial', locale, 200)
  const document = await page.goto(`${landing}${directory}?lang=${locale}`, { waitUntil: 'domcontentloaded' })
  check(document?.ok(), 'DIRECTORY_EXPORT_REQUIRED')
  check((await (await checkedPublicResponse(response)).json()).total === Number(published), 'PUBLIC_INDEX_PHASE_REQUIRED')
  if (published) {
    await page.getByText(content[locale].title, { exact: true }).first().waitFor()
    const links = await page.locator('a[href]').evaluateAll(nodes => nodes.map(node => node.href))
    check(links.some(href => {
      const link = new URL(href)
      return link.origin === landing && link.pathname === reader
        && link.searchParams.get('slug') === slug && link.searchParams.get('lang') === locale
    }), 'LOCALIZED_READER_LINK_REQUIRED')
  } else {
    check(await page.getByText(content[locale].title, { exact: true }).count() === 0, 'UNPUBLISHED_INDEX_LEAK')
  }
}

async function readGuide(page, { landing, api }, locale, revision, reload = false) {
  const pending = expectedResponse(page, api, `/site/editorial/${slug}`, locale, revision ? 200 : 404)
  const document = reload
    ? await page.reload({ waitUntil: 'domcontentloaded' })
    : await page.goto(detailURL(landing, locale), { waitUntil: 'domcontentloaded' })
  check(document?.ok() && page.url() === detailURL(landing, locale), 'READER_EXPORT_ROUTE_REQUIRED')
  await checkedPublicResponse(pending)
  const robots = await page.locator('meta[name=robots]').getAttribute('content')
  check(robots?.toLowerCase().split(/[ ,]+/).includes('noindex'), 'FIXED_READER_NOINDEX_REQUIRED')
  const entry = content[locale]
  if (!revision) {
    await page.getByText(entry.missing, { exact: true }).waitFor()
    const rendered = await page.locator('body').innerText()
    check(!rendered.includes(entry.body) && !rendered.includes(entry.updated), 'UNPUBLISHED_BODY_STILL_VISIBLE')
    return
  }
  const activeBody = revision === 1 ? entry.body : entry.updated
  const staleBody = revision === 1 ? entry.updated : entry.body
  await page.getByText(activeBody, { exact: true }).waitFor()
  check(!(await page.locator('body').innerText()).includes(staleBody), 'STALE_BODY_STILL_VISIBLE')
  const source = page.getByRole('link', { name: entry.source, exact: true })
  check(await source.getAttribute('href') === `https://example.invalid/source/${locale}`, 'LOCALIZED_SOURCE_LINK_REQUIRED')
}

export async function verifyEditorial(browser, fixture, origins, exportRoot, setStage) {
  const locales = Object.keys(content)
  const sitemap = await readFile(exportRoot + '/sitemap.xml', 'utf8')
  check(!sitemap.includes('<loc>https://ilovedrrealty.com/local-guides/read/'), 'FIXED_READER_MUST_NOT_ENTER_SITEMAP')
  for (const route of [directory, reader]) {
    const page = await readFile(exportRoot + route + 'index.html', 'utf8')
    check(locales.every(locale => !page.includes(content[locale].body) && !page.includes(content[locale].updated)),
      'PUBLIC_FIXTURE_BODY_IN_STATIC_HTML')
  }
  const contexts = []
  try {
    const pages = new Map()
    for (const locale of locales) {
      const context = await browser.newContext({ locale: locale === 'es' ? 'es-DO' : 'en-US', serviceWorkers: 'block' })
      contexts.push(context)
      context.setDefaultTimeout(12_000)
      await context.route('**/*', route => [origins.landing, origins.api].includes(new URL(route.request().url()).origin)
        ? route.continue() : route.abort('blockedbyclient'))
      await context.addInitScript(lang => {
        if (!navigator.onLine) Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => true })
        if (window.location.protocol === 'http:') localStorage.setItem('ildr_lang', lang)
      }, locale)
      pages.set(locale, await context.newPage())
    }
    setStage('editorial_absent')
    for (const [locale, page] of pages) {
      await readDirectory(page, origins, locale, false)
      await readGuide(page, origins, locale, 0)
    }
    setStage('editorial_published')
    fixture.setPublishedRevision(1)
    for (const [locale, page] of pages) {
      await readDirectory(page, origins, locale, true)
      await readGuide(page, origins, locale, 1)
    }
    setStage('editorial_updated')
    fixture.setPublishedRevision(2)
    for (const [locale, page] of pages) await readGuide(page, origins, locale, 2, true)
    setStage('editorial_removed')
    fixture.setPublishedRevision(0)
    for (const [locale, page] of pages) {
      await readGuide(page, origins, locale, 0, true)
      await readDirectory(page, origins, locale, false)
      check(fixture.saw('list', 0, locale) && fixture.saw('list', 1, locale)
        && [0, 1, 2].every(revision => fixture.saw('detail', revision, locale)), 'LOOPBACK_PUBLIC_READ_EVIDENCE_REQUIRED')
    }
  } finally {
    await Promise.all(contexts.map(context => context.close()))
  }
  return { status: 'PASS', actual_export: true, both_languages: true, body_absent_in_static_html: true,
    runtime_publish: true, runtime_update: true, runtime_unpublish: true, no_stale_body_after_removal: true,
    noindex_reader: true, public_no_store: true, synthetic_public_endpoint: true, same_unrebuilt_export: true }
}
