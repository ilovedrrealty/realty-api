const selection = {
  polygon: 'polygon,19,-70.5,19,-70.4,19.1,-70.4',
  radius: 'radius,19,-70.4,12.5',
}
const insideTitle = 'Synthetic property inside the map area'
const outsideTitle = 'Synthetic property outside the map area'
const privateTitle = 'Synthetic property without a public location'
const inside = { id: '10000000-0000-0000-0000-000000000002', title: insideTitle, latitude: 19.02, longitude: -70.42 }
const outside = { id: '10000000-0000-0000-0000-000000000003', title: outsideTitle, latitude: 18.1, longitude: -69.9 }
const all = [inside, outside, { id: '10000000-0000-0000-0000-000000000004', title: privateTitle,
  latitude: null, longitude: null }]
const properties = new Set(['/listings', '/listings/map'])

function check(condition, reason) {
  if (!condition) throw new Error(reason)
}

export function createSpatialFixture(api) {
  let mismatch = false
  const requests = []
  return {
    enableLegacyMismatch() { mismatch = true },
    disableLegacyMismatch() { mismatch = false },
    checkpoint() { return requests.length },
    since(checkpoint) { return requests.slice(checkpoint) },
    responseFor(request) {
      if (request.method !== 'GET') return null
      const url = new URL(request.url, api)
      if (!properties.has(url.pathname)) return null
      const spatial = url.searchParams.get('spatial')
      requests.push({ path: url.pathname, spatial, filters: url.searchParams })
      if (spatial && !Object.values(selection).includes(spatial)) {
        return { status: 422, data: { detail: 'Unexpected synthetic map area' } }
      }
      const matched = spatial && !mismatch ? [inside] : all
      const coverage = spatial && !mismatch ? { spatial_excluded_unlocated: 1 } : {}
      const tagged = rows => rows.map(item => ({ ...item, location: 'Cabarete', currency: 'USD', transaction: 'sale',
        type: 'villa', images: [], features: [], price: 200000, bedrooms: 3, bathrooms: 2, price_period: null }))
      if (url.pathname === '/listings') {
        return { status: 200, data: { items: tagged(matched), total: matched.length, page: 1, page_size: 24,
          median_price: 200000, avg_roi: 3, ...coverage } }
      }
      const located = matched.filter(item => item.latitude !== null)
      return { status: 200, data: { items: tagged(located), total: matched.length, mapped_total: located.length,
        unmapped_total: matched.length - located.length, ungrouped_total: matched.length - located.length,
        areas: [], viewport_total: located.length, returned: located.length, limit: 5000, truncated: false,
        bounds: null, ...coverage } }
    },
  }
}

function installSyntheticMaps() {
  // Only the renderer is substituted. Map toolbar, application callbacks, result views and URL state are real.
  const listeners = new WeakMap()
  const event = {
    addListener(instance, kind, handler) {
      if (!listeners.has(instance)) listeners.set(instance, new Set())
      const record = { kind, handler, remove: () => listeners.get(instance)?.delete(record) }
      listeners.get(instance).add(record)
      return record
    },
    removeListener(listener) { listener?.remove() },
    clearInstanceListeners(instance) { listeners.delete(instance) },
    trigger(instance, kind, ...args) {
      for (const record of [...(listeners.get(instance) ?? [])]) if (record.kind === kind) record.handler(...args)
    },
  }
  class LatLng {
    constructor(lat, lng) {
      this.latitude = typeof lat === 'object' ? (typeof lat.lat === 'function' ? lat.lat() : lat.lat) : lat
      this.longitude = typeof lat === 'object' ? (typeof lat.lng === 'function' ? lat.lng() : lat.lng) : lng
    }
    lat() { return this.latitude }
    lng() { return this.longitude }
  }
  class LatLngBounds {
    constructor(sw, ne) { this.points = [sw, ne].filter(Boolean).map(point => new LatLng(point)) }
    extend(point) { this.points.push(new LatLng(point)); return this }
    getSouthWest() { return new LatLng(Math.min(...this.points.map(x => x.lat())), Math.min(...this.points.map(x => x.lng()))) }
    getNorthEast() { return new LatLng(Math.max(...this.points.map(x => x.lat())), Math.max(...this.points.map(x => x.lng()))) }
    getCenter() {
      const sw = this.getSouthWest(), ne = this.getNorthEast()
      return new LatLng((sw.lat() + ne.lat()) / 2, (sw.lng() + ne.lng()) / 2)
    }
  }
  class Shape {
    constructor(options = {}) { this.options = { ...options } }
    addListener(kind, handler) { return event.addListener(this, kind, handler) }
    setOptions(options) { Object.assign(this.options, options) }
    setMap(map) { this.options.map = map }
  }
  for (const prop of ['center', 'radius', 'path', 'paths', 'editable', 'draggable', 'visible', 'position',
    'icon', 'label', 'title', 'clickable', 'opacity', 'zIndex', 'animation', 'cursor', 'shape']) {
    Shape.prototype['set' + prop[0].toUpperCase() + prop.slice(1)] = function (value) { this.options[prop] = value }
  }
  class Circle extends Shape {
    getBounds() {
      const center = new LatLng(this.options.center), offset = this.options.radius / 111000
      return new LatLngBounds(new LatLng(center.lat() - offset, center.lng() - offset),
        new LatLng(center.lat() + offset, center.lng() + offset))
    }
  }
  class PathShape extends Shape {
    constructor(options = {}) {
      super(options)
      this.path = []
      this.setPaths(options.paths ?? options.path ?? [])
    }
    // PolygonF registers events against this object, so keep its identity when the shape updates.
    getPath() { return this.path }
    setPath(points) { this.path.splice(0, this.path.length, ...points) }
    setPaths(points) { this.setPath(Array.isArray(points[0]) ? points[0] : points) }
  }
  class MapSurface extends Shape {
    constructor(element, options) {
      super(options)
      this.element = element
      element.dataset.syntheticMap = 'true'
      element.addEventListener('click', click => {
        const box = element.getBoundingClientRect()
        const lat = click.clientY < box.top + box.height / 2 ? 19 : 19.1
        const lng = click.clientX < box.left + box.width / 2 ? -70.5 : -70.4
        event.trigger(this, 'click', { latLng: new LatLng(lat, lng) })
      })
    }
    getDiv() { return this.element }
    getCenter() { return new LatLng(this.options.center ?? { lat: 18.73, lng: -70.16 }) }
    getZoom() { return this.options.zoom ?? 8 }
    setZoom(zoom) { this.options.zoom = zoom }
    panTo(point) { this.setCenter(point) }
    fitBounds(bounds) { this.setCenter(bounds.getCenter()) }
    getBounds() { return new LatLngBounds({ lat: 17, lng: -73 }, { lat: 21, lng: -68 }) }
  }
  class Point { constructor(x, y) { this.x = x; this.y = y } }
  class Size { constructor(width, height) { this.width = width; this.height = height } }
  class OverlayView extends Shape {}
  const maps = { version: '3.64', event, Map: MapSurface, Circle, LatLng, LatLngBounds, Point, Size,
    Polygon: PathShape, Polyline: PathShape, Marker: Shape, OverlayView, SymbolPath: { CIRCLE: 0 },
    importLibrary: async () => maps }
  window.google = { maps }
}

function apiReads(page, api, spatial) {
  // Attach rejection handlers before any further navigation or map operations.
  return Promise.allSettled([...properties].map(path => page.waitForResponse(response => {
    const url = new URL(response.url())
    return url.origin === api && url.pathname === path && response.request().method() === 'GET'
      && url.searchParams.get('spatial') === spatial && response.ok()
      && (path !== '/listings' || !url.searchParams.has('exclude_ids'))
  })))
}

async function checkedReads(pending, excluded = null) {
  const responses = await pending
  check(responses[0].status === 'fulfilled', 'LIST_RESPONSE_MISSING')
  check(responses[1].status === 'fulfilled', 'MAP_RESPONSE_MISSING')
  for (const result of responses) {
    const response = result.value
    const url = new URL(response.url())
    const body = await response.json()
    check(url.searchParams.get('purpose') === 'sale' && url.searchParams.get('beds') === '2',
      'SEARCH_FILTERS_MUST_SURVIVE_SHAPE_CHANGES')
    check((body.spatial_excluded_unlocated ?? null) === excluded, 'SAME_PUBLIC_EXCLUDED_COUNT_REQUIRED')
  }
}

export async function verifySpatial(browser, fixture, { landing, api }, setStage) {
  const context = await browser.newContext({ locale: 'en-US', viewport: { width: 390, height: 844 },
    isMobile: true, hasTouch: true, serviceWorkers: 'block' })
  let pageFailure = null
  try {
    context.setDefaultTimeout(12_000)
    await context.route('**/*', route => [landing, api].includes(new URL(route.request().url()).origin)
      ? route.continue() : route.abort('blockedbyclient'))
    await context.addInitScript(() => {
      if (!navigator.onLine) Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => true })
      if (location.protocol === 'http:') {
        localStorage.setItem('ildr_lang', 'en')
        sessionStorage.setItem('lead_modal_dismissed', '1')
      }
    })
    await context.addInitScript(installSyntheticMaps)
    const page = await context.newPage()
    page.on('pageerror', error => {
      const message = error.message
      if (/\b(?:getProjection|getPanes|getPath|setPaths|setMap|getBounds|getCenter|getPosition|addListener)\b.*not a function/i.test(message)) {
        pageFailure = 'MAP_SHIM_METHOD_MISSING'
      } else if (/Google Maps|google\.maps|google is not defined/i.test(message)) {
        pageFailure = 'MAP_INITIALIZATION_FAILED'
      } else {
        pageFailure ??= 'BROWSER_PAGE_EXCEPTION'
      }
    })
    const dialog = page.getByRole('dialog', { name: 'Property search map' })
    const surface = dialog.locator('[data-synthetic-map=true]')
    const button = (name, scope = dialog) => scope.getByRole('button', { name, exact: true })
    const openMap = async () => { await button('View map', page).tap(); await dialog.waitFor(); await surface.waitFor() }
    const closeMap = async () => { await button('Back to results').tap(); await dialog.waitFor({ state: 'hidden' }) }
    const waitScope = spatial => page.waitForURL(url => url.pathname === '/search/'
      && url.searchParams.get('spatial') === spatial && (url.searchParams.get('purpose') ?? 'sale') === 'sale'
      && url.searchParams.get('beds') === '2')
    const tapMap = async (x, y) => {
      const box = await surface.boundingBox()
      check(box && box.width > 0 && box.height > 0, 'FULLSCREEN_MAP_SURFACE_REQUIRED')
      await surface.tap({ position: { x: Math.round(box.width * x), y: Math.round(box.height * y) } })
    }
    const expectOnlyMatching = async () => {
      await page.getByText(insideTitle, { exact: true }).first().waitFor()
      await page.getByText('1 property matching your other filters was left out because it has no precise public location.',
        { exact: true }).first().waitFor()
      check(await page.getByText(outsideTitle, { exact: true }).count() === 0
        && await page.getByText(privateTitle, { exact: true }).count() === 0, 'BROAD_RESULTS_IN_ACTIVE_SHAPE')
    }

    setStage('spatial_initial')
    const initial = apiReads(page, api, null)
    const route = await page.goto(landing + '/search/?purpose=sale&beds=2', { waitUntil: 'domcontentloaded' })
    check(route?.ok(), 'ACTUAL_SEARCH_EXPORT_REQUIRED')
    setStage('spatial_initial_http')
    await checkedReads(initial)
    setStage('spatial_initial_cards')
    await page.getByText(outsideTitle, { exact: true }).first().waitFor()
    setStage('spatial_open_map')
    await openMap()

    setStage('spatial_polygon')
    await button('Draw an area').tap()
    await tapMap(0.2, 0.2)
    await tapMap(0.8, 0.2)
    await tapMap(0.8, 0.8)
    setStage('spatial_polygon_apply')
    const shapeReads = apiReads(page, api, selection.polygon)
    await button('Apply area').tap()
    await waitScope(selection.polygon)
    setStage('spatial_polygon_http')
    await checkedReads(shapeReads, 1)
    setStage('spatial_polygon_cards')
    await closeMap()
    await expectOnlyMatching()

    setStage('spatial_radius')
    await openMap()
    await button('Radius search').tap()
    await button('Use map center').tap()
    await tapMap(0.8, 0.2)
    await dialog.getByRole('spinbutton', { name: 'Radius (km)' }).fill('12.5')
    setStage('spatial_radius_apply')
    const radiusReads = apiReads(page, api, selection.radius)
    await button('Apply radius').tap()
    await waitScope(selection.radius)
    setStage('spatial_radius_http')
    await checkedReads(radiusReads, 1)
    await closeMap()
    await expectOnlyMatching()

    setStage('spatial_reload')
    const reloadReads = apiReads(page, api, selection.radius)
    await page.reload({ waitUntil: 'domcontentloaded' })
    await checkedReads(reloadReads, 1)
    await waitScope(selection.radius)
    await expectOnlyMatching()

    setStage('spatial_legacy_mismatch')
    fixture.enableLegacyMismatch()
    const checkpoint = fixture.checkpoint()
    const legacyReads = apiReads(page, api, selection.radius)
    await page.reload({ waitUntil: 'domcontentloaded' })
    await checkedReads(legacyReads)
    await page.getByText('The map area could not be searched. Try again or clear the map area.', { exact: true }).first().waitFor()
    check(await page.getByText(outsideTitle, { exact: true }).count() === 0
      && await page.getByText(privateTitle, { exact: true }).count() === 0
      && fixture.since(checkpoint).every(request => request.spatial === selection.radius), 'SPA_MUST_NOT_BROADEN_MISSING_SCOPE')

    setStage('spatial_clear')
    fixture.disableLegacyMismatch()
    await openMap()
    const cleared = apiReads(page, api, null)
    await button('Clear map area').tap()
    await waitScope(null)
    await checkedReads(cleared)
    await closeMap()
    await page.getByText(outsideTitle, { exact: true }).first().waitFor()
  } catch (error) {
    if (pageFailure && error.name === 'TimeoutError') throw new Error(pageFailure)
    throw error
  } finally {
    await context.close()
  }
  return { status: 'PASS', actual_search_export: true, width_390_touch: true, local_map_shim: true,
    polygon_map_taps: true, radius_controls: true, scope_url_reload_and_clear: true,
    exact_list_map_requests: true, excluded_unlocated_visible: true, legacy_missing_scope_fails_closed: true,
    external_maps_network_used: false, synthetic_public_endpoint: true }
}
