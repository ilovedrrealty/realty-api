const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const React = require('react')
const jsxRuntime = require('react/jsx-runtime')
const { renderToStaticMarkup } = require('react-dom/server')

const root = path.resolve(__dirname, '..')
const sourceRoot = 'apps/dashboard/src/'
const fixtureId = number => `00000000-0000-4000-8000-${String(number).padStart(12, '0')}`
const ID = fixtureId(1), LISTING = fixtureId(2), PHOTO1 = fixtureId(3), PHOTO2 = fixtureId(4), ENTRY = fixtureId(5)
const page = items => ({ items, total: items.length, page: 1, page_size: 30 })
const author = { user_code: 71, display_name: 'Synthetic member' }
const profile = changes => ({ user_code: 71, display_name: 'Synthetic member', bio: 'Synthetic biography', status_text: '', visibility: 'private', wall_policy: 'own_only', comments_enabled: true, version: 0, can_edit: true, ...changes })
const project = changes => ({ id: ID, listing_id: LISTING, listing_title: 'Synthetic listing', title: 'Synthetic repair', summary: 'Synthetic project summary', property_class: 'house_villa', service_category_ids: ['air_conditioning'], status: 'open', version: 1, my_role: 'viewer', can_manage: false, can_assign: false, can_update: false, progress: { total: 0, completed: 0, percent: null }, created_at: '2026-01-01T12:00:00Z', updated_at: '2026-01-01T12:00:00Z', ...changes })

/**
 * Actual TSX + React's real JSX/SSR renderer. This bounded hook adapter stores
 * component-local state and runs effects against explicit mock API functions;
 * event handlers are the actual component handlers. It is not DOM/browser proof.
 * No production client, fetch, provider, storage URL or customer fixture is loaded.
 */
function ui(relative, exported, props, responses = {}, language = 'en') {
  const frames = new Map(), moduleCache = new Map(), componentTypes = new WeakMap(), effects = [], calls = [], revoked = [], listeners = new Map()
  const translations = JSON.parse(fs.readFileSync(path.join(root, sourceRoot, `locale/${language}/property_network.json`), 'utf8'))
  let current, seen, latestRoot, nextFunction = 0, nextUUID = 100, nextBlob = 0, html = '', dirty = false, allowLeave = true, confirmations = 0
  let clock = Date.parse('2026-01-02T12:00:00Z'), nextTimer = 0
  const timers = new Map(), fakeNavigator = { onLine: true }
  const lookup = (key, values = {}) => {
    let result = key.split('.').reduce((value, part) => value?.[part], translations)
    assert.equal(typeof result, 'string', `Missing ${language} translation: ${key}`)
    return result.replace(/\{\{(\w+)\}\}/g, (_, name) => String(values[name] ?? ''))
  }
  const slot = kind => {
    assert.ok(current, 'Hooks must belong to an actual component render')
    const index = current.cursor++
    if (!current.slots[index]) current.slots[index] = { kind }
    assert.equal(current.slots[index].kind, kind, 'Hook order changed')
    return current.slots[index]
  }
  const hooks = { ...React,
    useState(initial) { const state = slot('state'); if (!Object.hasOwn(state, 'value')) state.value = typeof initial === 'function' ? initial() : initial; return [state.value, next => { state.value = typeof next === 'function' ? next(state.value) : next; dirty = true }] },
    useRef(initial) { const state = slot('ref'); if (!state.value) state.value = { current: initial }; return state.value },
    useEffect(effect, deps) { const state = slot('effect'); if (!state.deps || deps.some((value, index) => value !== state.deps[index])) { state.deps = [...deps]; effects.push({ state, effect }) } },
  }
  function resolveChildren(children, parentPath) {
    // Match the child-list boundary used by React: a top-level unkeyed Fragment
    // is transparent. Children.toArray retains nested-array and explicit-key scope.
    if (React.isValidElement(children) && children.type === React.Fragment && children.key === null) children = children.props.children
    return React.Children.toArray(children).map((value, index) => {
      if (!React.isValidElement(value)) return value
      const type = value.type
      let identity
      if (typeof type === 'function') {
        if (!componentTypes.has(type)) componentTypes.set(type, ++nextFunction)
        identity = ['component', componentTypes.get(type)]
      } else {
        assert.ok(typeof type === 'string' || type === React.Fragment, 'Unsupported element type in bounded mock renderer')
        identity = [type === React.Fragment ? 'fragment' : 'host', type === React.Fragment ? '' : type]
      }
      // Resolve after the parent tree exists, so keyed host ancestors (such as
      // timeline li elements) scope an unkeyed descendant's hook state too.
      const nodePath = [...parentPath, [identity, value.key ?? index]]
      if (typeof type !== 'function') {
        // Preserve absent/null children, especially on void input/img elements.
        if (value.props.children == null) return value
        return React.cloneElement(value, undefined, resolveChildren(value.props.children, nodePath))
      }
      const frameId = JSON.stringify(nodePath), previous = current
      assert.ok(!seen.has(frameId), 'Duplicate component identity in one mock render')
      if (!frames.has(frameId)) frames.set(frameId, { id: frameId, slots: [], cursor: 0 })
      current = frames.get(frameId); current.cursor = 0; seen.add(frameId)
      let tree
      try { tree = type(value.props) } finally { current = previous }
      return resolveChildren(tree, nodePath)
    })
  }
  const runtime = jsxRuntime
  const api = new Proxy({}, { get: (_, name) => {
    // Preserve real exported data (including the property-class catalog); only
    // API operations use explicit responses. The real client stays unreachable.
    const actual = load(sourceRoot + 'api/propertyNetwork.ts')
    if (Object.hasOwn(actual, name) && typeof actual[name] !== 'function') return actual[name]
    return (...args) => {
      calls.push({ name, args })
      assert.equal(typeof responses[name], 'function', `Unexpected API call: ${String(name)}`)
      return responses[name](...args)
    }
  } })
  const serviceApi = new Proxy({}, { get: (_, name) => {
    const actual = load(sourceRoot + 'api/serviceWorkflow.ts')
    // Data, pure input preparation/error and decimal helpers stay real. Every
    // authenticated operation still requires a declared synthetic response.
    if (Object.hasOwn(actual, name) && (typeof actual[name] !== 'function' || ['serviceAmount', 'serviceAmountMinor', 'prepareServiceAction', 'ServiceInputError'].includes(name))) return actual[name]
    return (...args) => {
      calls.push({ name, args })
      assert.equal(typeof responses[name], 'function', 'Unexpected service API call: ' + String(name))
      return responses[name](...args)
    }
  } })
  const fakeWindow = {
    addEventListener(name, callback) { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(callback) },
    removeEventListener(name, callback) { listeners.get(name)?.delete(callback) },
    dispatchEvent(event) { for (const callback of listeners.get(event.type) ?? []) callback(event); return !event.defaultPrevented },
    confirm() { confirmations++; return allowLeave },
    setTimeout(callback, delay) { const id = ++nextTimer; timers.set(id, { callback, due: clock + delay }); return id },
    clearTimeout(id) { timers.delete(id) },
  }
  const fakeDocument = { visibilityState: 'visible', addEventListener: fakeWindow.addEventListener, removeEventListener: fakeWindow.removeEventListener }
  function load(file) {
    const absolute = path.resolve(root, file)
    assert.ok(absolute.startsWith(root + path.sep), 'Only this worktree source may be loaded')
    if (moduleCache.has(absolute)) return moduleCache.get(absolute).exports
    const module = { exports: {} }; moduleCache.set(absolute, module)
    const source = fs.readFileSync(absolute, 'utf8')
    const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText
    vm.runInNewContext(compiled, { module, exports: module.exports, AbortController, URLSearchParams, Date: class extends Date { static now() { return clock } }, Intl, Blob, Event,
      document: fakeDocument, navigator: fakeNavigator,
      crypto: { randomUUID: () => fixtureId(++nextUUID) }, window: fakeWindow,
      URL: { createObjectURL: () => `blob:synthetic-${++nextBlob}`, revokeObjectURL: value => revoked.push(value) },
      require(name) {
        if (['api/propertyNetwork.ts', 'api/serviceWorkflow.ts'].some(file => absolute === path.resolve(root, sourceRoot + file)) && name === './axios') {
          const refuse = () => assert.fail('UI adapter must use explicit API responses, never the authenticated client')
          return { __esModule: true, default: { get: refuse, post: refuse, put: refuse } }
        }
        if (name === 'react') return hooks
        if (name === 'react/jsx-runtime') return runtime
        if (name === 'react-i18next') return { useTranslation: () => ({ t: lookup, i18n: { language } }) }
        if (name === 'axios') return { isAxiosError: error => error?.isAxiosError === true }
        if (name === 'react-router-dom') return { useSearchParams: () => { const [value, setValue] = hooks.useState(''); return [new URLSearchParams(value), next => setValue(next.toString())] } }
        if (name.endsWith('/api/propertyNetwork')) return api
        if (name.endsWith('/api/serviceWorkflow')) return serviceApi
        if (name.endsWith('/api/professionals')) return { getProfessionalOptions: api.getProfessionalOptions }
        if (name.endsWith('/api/propertyManagement')) return { todayInDR: () => '2026-01-02' }
        assert.ok(name.startsWith('.'), `Unapproved module dependency: ${name}`)
        const base = path.resolve(path.dirname(absolute), name)
        const target = ['.tsx', '.ts', '/index.ts'].map(suffix => base + suffix).find(candidate => fs.existsSync(candidate))
        assert.ok(target, `Missing source module ${name}`)
        return load(path.relative(root, target))
      },
    }, { filename: absolute })
    return module.exports
  }
  const Component = load(sourceRoot + relative)[exported]
  assert.equal(typeof Component, 'function')
  function render() {
    dirty = false; seen = new Set(); latestRoot = resolveChildren(jsxRuntime.jsx(Component, props), [])
    html = renderToStaticMarkup(latestRoot)
    for (const [id, frame] of frames) if (!seen.has(id)) { for (const entry of frame.slots) entry.cleanup?.(); frames.delete(id) }
    return html
  }
  async function settle() {
    for (let attempt = 0; attempt < 15; attempt++) {
      render()
      const batch = effects.splice(0)
      for (const entry of batch) { entry.state.cleanup?.(); entry.state.cleanup = entry.effect() }
      for (let tick = 0; tick < 5; tick++) await Promise.resolve()
      if (!dirty && !effects.length) return html
    }
    assert.fail('UI did not settle within the bounded mock render loop')
  }
  function nodes(rootNode = latestRoot) {
    const result = []
    function visit(value) {
      if (Array.isArray(value)) { value.forEach(visit); return }
      if (!React.isValidElement(value)) return
      result.push(value); visit(value.props.children)
    }
    visit(rootNode); return result
  }
  function find(type, predicate = () => true) { const value = nodes().find(node => node.type === type && predicate(node)); assert.ok(value, `Missing rendered ${type}`); return value }
  const text = value => Array.isArray(value) ? value.map(text).join('') : React.isValidElement(value)
    ? text(value.props.children)
    : value == null || value === false ? '' : String(value)
  return { calls, revoked, t: lookup, render, settle, nodes, find, text, get html() { return html },
    serviceContractError() { return new (load(sourceRoot + 'api/serviceWorkflow.ts').ServiceContractError)() },
    get visibleText() { return text(latestRoot) },
    get confirmations() { return confirmations }, allowLeave(value) { allowLeave = value },
    async advance(amount) {
      const end = clock + amount
      for (let count = 0; count < 100; count++) {
        const next = [...timers.entries()].filter(([, timer]) => timer.due <= end).sort((a, b) => a[1].due - b[1].due)[0]
        if (!next) { clock = end; return settle() }
        clock = next[1].due; timers.delete(next[0]); next[1].callback(); await settle()
      }
      assert.fail('Unbounded mock timers')
    },
    async presence(visible, online) { fakeDocument.visibilityState = visible ? 'visible' : 'hidden'; fakeNavigator.onLine = online; fakeWindow.dispatchEvent(new Event('visibilitychange')); fakeWindow.dispatchEvent(new Event(online ? 'online' : 'offline')); return settle() },
    async fire(node, handler, event = {}) { node.props[handler](event); for (let tick = 0; tick < 5; tick++) await Promise.resolve(); return settle() },
    dispose() { for (const frame of frames.values()) for (const entry of frame.slots) entry.cleanup?.(); frames.clear(); effects.length = 0 },
  }
}

test('actual member editor starts private, creates nothing on read, and saves only deliberate visibility changes', async () => {
  let saved = profile()
  const h = ui('components/network/MemberCommunity.tsx', 'MemberProfile', { own: true }, {
    getMyNetworkProfile: async () => saved,
    saveNetworkProfile: async input => { saved = { ...saved, ...input, version: 1 }; return saved },
    getProfilePosts: async () => page([]),
  })
  try {
    await h.settle()
    assert.ok(h.html.includes(h.t('profile_private')))
    assert.ok(h.html.includes(h.t('save_profile_first')))
    assert.deepEqual(h.calls.map(call => call.name), ['getMyNetworkProfile'])
    const visibility = h.find('select', node => node.props.value === 'private')
    await h.fire(visibility, 'onChange', { target: { value: 'members' } })
    assert.equal(h.calls.filter(call => call.name === 'saveNetworkProfile').length, 0)
    await h.fire(h.find('form'), 'onSubmit', { preventDefault() {} })
    const write = h.calls.find(call => call.name === 'saveNetworkProfile').args[0]
    assert.equal(write.visibility, 'members'); assert.equal(write.expected_version, 0)
    for (const forbidden of ['email', 'phone', 'professional_profile_id', 'published_data', 'user_id']) assert.ok(!(forbidden in write))
    assert.ok(h.html.includes(h.t('profile_members')))
  } finally { h.dispose() }
})

test('actual project controls follow server capabilities rather than a selected workspace', async () => {
  for (const [role, capabilities] of [
    ['viewer', { can_manage: false, can_assign: false, can_update: false }],
    ['manager', { can_manage: true, can_assign: false, can_update: true }],
    ['owner', { can_manage: true, can_assign: true, can_update: true }],
  ]) {
    const h = ui('components/network/ProjectWorkspace.tsx', 'ProjectDetail', { id: ID, onBack() {} }, {
      getProject: async () => project({ my_role: role, ...capabilities }), getProjectUpdates: async () => page([]),
      getProjectMembers: async () => [{ ...author, role: 'professional', active: true }],
    })
    try {
      await h.settle()
      assert.equal(h.html.includes(h.t('add_progress')), capabilities.can_update)
      assert.ok(h.html.includes(h.t('no_milestones')))
      assert.ok(!h.html.includes('0%'), 'No milestones is not measured zero progress')
      await h.fire(h.find('button', node => h.text(node) === h.t('tabs.members')), 'onClick')
      assert.equal(h.html.includes(h.t('add_member')), capabilities.can_assign)
      assert.equal(h.html.includes(h.t('revoke_access')), capabilities.can_assign)
      await h.fire(h.find('button', node => h.text(node) === h.t('tabs.details')), 'onClick')
      assert.equal(h.html.includes(h.t('save_changes')), capabilities.can_manage)
      assert.ok(h.calls.every(call => call.name.startsWith('get')), 'Rendering controls cannot write')
    } finally { h.dispose() }
  }
})

test('actual service history displays authorized work and server progress without leaking unrelated fixture fields', async () => {
  const h = ui('components/network/ProjectWorkspace.tsx', 'ProjectDetail', { id: ID, onBack() {} }, {
    getProject: async () => project({ progress: { total: 4, completed: 1, percent: 25 } }), getProjectUpdates: async () => page([]),
    getPropertyServiceHistory: async listingId => { assert.equal(listingId, LISTING); return page([{ id: ENTRY, project_title: 'Synthetic service', occurred_on: '2026-01-01', body: 'Synthetic work completed', media_ids: [], tenant_email: 'DO-NOT-RENDER@example.invalid', ledger: 'DO-NOT-RENDER-LEDGER', raw_snapshot: 'DO-NOT-RENDER-SNAPSHOT' }]) },
  })
  try {
    await h.settle(); assert.ok(h.html.includes('25%'))
    await h.fire(h.find('button', node => h.text(node) === h.t('tabs.history')), 'onClick')
    assert.ok(h.html.includes('Synthetic work completed')); assert.ok(h.html.includes(h.t('service_history_note')))
    assert.ok(!h.html.includes('DO-NOT-RENDER'))
    assert.ok(h.calls.every(call => !/statement|ledger|lease|tenant/i.test(String(call.name))))
  } finally { h.dispose() }
})

test('actual composer preserves exact pending submission on uncertainty and never reports message delivery', async () => {
  const writes = []; let successful = 0
  const h = ui('components/network/NetworkShared.tsx', 'NetworkComposer', { dated: true, submit: async input => { writes.push(input); if (writes.length === 1) throw new Error('Synthetic unknown outcome'); return {} }, onSaved: () => successful++ })
  try {
    await h.settle()
    await h.fire(h.find('textarea'), 'onChange', { target: { value: '<script>synthetic & text</script>' } })
    await h.fire(h.find('form'), 'onSubmit', { preventDefault() {} })
    assert.equal(successful, 0); assert.equal(writes.length, 1)
    assert.ok(h.visibleText.includes(h.t('uncertain_submission'))); assert.ok(h.visibleText.includes(h.t('retry_same')))
    assert.equal(h.find('textarea').props.disabled, true)
    assert.ok(h.html.includes('&lt;script&gt;')); assert.ok(!h.html.includes('<script>'))
    await h.fire(h.find('form'), 'onSubmit', { preventDefault() {} })
    assert.equal(successful, 1); assert.equal(writes.length, 2)
    assert.deepEqual(JSON.parse(JSON.stringify(writes[1])), JSON.parse(JSON.stringify(writes[0])))
    assert.ok(!h.visibleText.includes(h.t('uncertain_submission')))
    assert.ok(!/message sent|delivered successfully|email sent/i.test(h.html))
  } finally { h.dispose() }
})

test('actual comments preserve parent-photo context, guard context changes and refuse foreign photo ids', async () => {
  const writes = []
  const h = ui('components/network/NetworkShared.tsx', 'NetworkComments', { recordId: ENTRY, kind: 'update', canWrite: true, mediaIds: [PHOTO1, PHOTO2] }, {
    getUpdateComments: async () => page([{ id: fixtureId(8), body: 'Synthetic photo remark', media_id: PHOTO2, author, hidden: false, can_hide: false, created_at: '2026-01-01T12:00:00Z' }]),
    getNetworkMediaBlob: async () => new Blob(['synthetic'], { type: 'image/webp' }),
    createUpdateComment: async (id, input) => { assert.equal(id, ENTRY); writes.push(input); return {} },
  })
  try {
    await h.settle()
    await h.fire(h.find('button', node => h.text(node) === h.t('comments')), 'onClick')
    assert.ok(h.html.includes(h.t('comment_photo', { number: 2 })))
    await h.fire(h.find('select'), 'onChange', { target: { value: PHOTO1 } })
    await h.fire(h.find('textarea'), 'onChange', { target: { value: 'Synthetic draft for photo one' } })
    h.allowLeave(false)
    await h.fire(h.find('select'), 'onChange', { target: { value: PHOTO2 } })
    assert.equal(h.find('select').props.value, PHOTO1); assert.ok(h.confirmations > 0)
    h.allowLeave(true)
    await h.fire(h.find('select'), 'onChange', { target: { value: PHOTO2 } })
    assert.equal(h.find('textarea').props.value, '', 'New context gets a new composer')
    await h.fire(h.find('select'), 'onChange', { target: { value: fixtureId(999) } })
    assert.equal(h.find('select').props.value, PHOTO2)
    await h.fire(h.find('textarea'), 'onChange', { target: { value: 'Synthetic comment for photo two' } })
    await h.fire(h.find('form'), 'onSubmit', { preventDefault() {} })
    assert.equal(writes.length, 1); assert.equal(writes[0].media_id, PHOTO2)
    assert.equal(writes[0].body, 'Synthetic comment for photo two')
    assert.ok(!('project_id' in writes[0]) && !('author_id' in writes[0]))
  } finally { h.dispose() }
})

test('actual private photo consumer uses authorized blob bytes and revokes its object URL', async () => {
  const h = ui('components/network/NetworkShared.tsx', 'PrivatePhotos', { ids: [PHOTO1] }, {
    getNetworkMediaBlob: async (id, signal) => { assert.equal(id, PHOTO1); assert.ok(signal); return new Blob(['synthetic'], { type: 'image/webp' }) },
  })
  await h.settle()
  assert.equal(h.calls.length, 1)
  assert.equal(h.find('img').props.src, 'blob:synthetic-1')
  assert.ok(h.find('img').props.className.includes('object-contain'))
  assert.ok(!/https?:\/\/|signed|storage\/v1|token=/.test(h.html))
  h.dispose(); assert.deepEqual(h.revoked, ['blob:synthetic-1'])
})

test('actual wall owner sees pending approval status and only a deliberate approve action changes it', async () => {
  let entry = { id: ENTRY, profile_user_code: 71, author, body: 'Synthetic submitted wall post', media_ids: [], status: 'pending', can_moderate: true, can_comment: false, created_at: '2026-01-01T12:00:00Z' }
  const h = ui('components/network/MemberCommunity.tsx', 'MemberProfile', { own: true }, {
    getMyNetworkProfile: async () => profile({ version: 1, visibility: 'members', wall_policy: 'approval_required' }),
    getProfilePosts: async () => page([entry]),
    moderatePost: async (id, action) => { assert.equal(id, ENTRY); assert.equal(action, 'approve'); entry = { ...entry, status: 'visible' }; return entry },
  })
  try {
    await h.settle()
    assert.ok(h.visibleText.includes(h.t('post_statuses.pending')))
    assert.equal(h.calls.filter(call => call.name === 'moderatePost').length, 0)
    await h.fire(h.find('button', node => h.text(node) === h.t('approve_post')), 'onClick')
    assert.equal(h.calls.filter(call => call.name === 'moderatePost').length, 1)
    assert.ok(h.visibleText.includes(h.t('post_statuses.visible')))
    assert.ok(!h.visibleText.includes(h.t('post_statuses.pending')))
  } finally { h.dispose() }
})

test('actual read-only comment consumer exposes no composer and Spanish UI resolves its actual copy', async () => {
  const h = ui('components/network/NetworkShared.tsx', 'NetworkComments', { recordId: ENTRY, kind: 'post', canWrite: false, mediaIds: [] }, { getPostComments: async () => page([]) }, 'es')
  try {
    await h.settle(); await h.fire(h.find('button'), 'onClick')
    assert.ok(h.html.includes('Actualmente') || h.html.includes('actualmente'))
    assert.equal(h.nodes().filter(node => node.type === 'form').length, 0)
    assert.equal(h.nodes().filter(node => node.type === 'select').length, 0)
    assert.deepEqual(h.calls.map(call => call.name), ['getPostComments'])
  } finally { h.dispose() }
})

test('another participant timeline/comment and milestone ratio refresh without losing open drafts', async () => {
  const entry = { id: ENTRY, project_id: ID, kind: 'progress', body: 'Original work update', occurred_on: '2026-01-02', correction_of: null, author, media_ids: [], created_at: '2026-01-02T12:00:00Z' }
  let updates = [entry], comments = [], progress = { total: 2, completed: 0, percent: 0 }
  const h = ui('components/network/ProjectWorkspace.tsx', 'ProjectDetail', { id: ID, onBack() {} }, {
    getProject: async () => project({ progress, my_role: 'professional', can_update: true }), getProjectUpdates: async () => page(updates), getUpdateComments: async () => page(comments),
  })
  try {
    await h.settle()
    await h.fire(h.find('textarea', node => node.props.maxLength === 4000), 'onChange', { target: { value: 'Unsent project draft' } })
    await h.fire(h.find('button', node => h.text(node) === h.t('comments')), 'onClick')
    await h.fire(h.find('textarea', node => node.props.maxLength === 2000), 'onChange', { target: { value: 'Unsent comment draft' } })
    updates = [{ ...entry, id: fixtureId(80), body: 'Another participant completed work' }, entry]
    comments = [{ id: fixtureId(81), body: 'Another participant comment', media_id: null, author, hidden: false, can_hide: false, created_at: '2026-01-02T12:00:20Z' }]
    progress = { total: 2, completed: 1, percent: 50 }
    await h.advance(30000)
    assert.ok(h.visibleText.includes('Another participant completed work'))
    assert.ok(h.visibleText.includes('Another participant comment'))
    assert.ok(h.visibleText.includes(h.t('progress', { completed: 1, total: 2, percent: '50' })))
    assert.equal(h.find('textarea', node => node.props.maxLength === 4000).props.value, 'Unsent project draft')
    assert.equal(h.find('textarea', node => node.props.maxLength === 2000).props.value, 'Unsent comment draft')
    assert.ok(h.calls.every(call => call.name.startsWith('get')))
  } finally { h.dispose() }
})

test('mounted English and Spanish comments show last-check/stale copy and pause offline or hidden', async () => {
  for (const language of ['en', 'es']) {
    let comments = []
    const h = ui('components/network/NetworkShared.tsx', 'NetworkComments', { recordId: ENTRY, kind: 'post', canWrite: false, mediaIds: [] }, { getPostComments: async () => page(comments) }, language)
    try {
      await h.settle(); assert.equal(h.calls.length, 0)
      await h.fire(h.find('button'), 'onClick')
      assert.ok(h.visibleText.includes(h.t('freshness_checked', { time: '' }).trim()))
      await h.presence(false, false); assert.ok(h.visibleText.includes(h.t('freshness_stale')))
      const count = h.calls.length; await h.advance(120000); assert.equal(h.calls.length, count)
      comments = [{ id: fixtureId(82), body: 'New permitted member comment', media_id: null, author, hidden: false, can_hide: false, created_at: '2026-01-02T12:00:20Z' }]
      await h.presence(true, true)
      assert.ok(h.visibleText.includes('New permitted member comment'))
      assert.ok(!h.visibleText.includes(h.t('freshness_stale')))
    } finally { h.dispose() }
  }
})

test('fresh permission denial clears project content and releases private photo object URLs', async () => {
  let denied = false
  const fail = () => { throw { isAxiosError: true, response: { status: 404 } } }
  const h = ui('components/network/ProjectWorkspace.tsx', 'ProjectDetail', { id: ID, onBack() {} }, {
    getProject: async () => denied ? fail() : project(),
    getProjectUpdates: async () => denied ? fail() : page([{ id: ENTRY, project_id: ID, kind: 'progress', body: 'Private project work', occurred_on: '2026-01-02', correction_of: null, author, media_ids: [PHOTO1], created_at: '2026-01-02T12:00:00Z' }]),
    getNetworkMediaBlob: async () => new Blob(['synthetic'], { type: 'image/webp' }),
  })
  try {
    await h.settle(); assert.equal(h.nodes().filter(node => node.type === 'img').length, 1)
    denied = true; await h.advance(30000)
    assert.ok(h.visibleText.includes(h.t('error_access')))
    assert.ok(!h.visibleText.includes('Private project work'))
    assert.equal(h.nodes().filter(node => node.type === 'img').length, 0)
    assert.deepEqual(h.revoked, ['blob:synthetic-1'])
  } finally { h.dispose() }
})

test('transient background timeline errors retain entries and unsent nested comment drafts', async () => {
  for (const failure of ['server', 'network']) {
    let failed = false
    const h = ui('components/network/ProjectWorkspace.tsx', 'ProjectDetail', { id: ID, onBack() {} }, {
      getProject: async () => project({ my_role: 'professional', can_update: true }),
      getProjectUpdates: async () => {
        if (failed) {
          if (failure === 'server') throw { isAxiosError: true, response: { status: 500 } }
          throw new Error('Synthetic network failure')
        }
        return page([{ id: ENTRY, project_id: ID, kind: 'progress', body: 'Retained private timeline entry', occurred_on: '2026-01-02', correction_of: null, author, media_ids: [], created_at: '2026-01-02T12:00:00Z' }])
      },
      getUpdateComments: async () => page([]),
    })
    try {
      await h.settle()
      await h.fire(h.find('button', node => h.text(node) === h.t('comments')), 'onClick')
      await h.fire(h.find('textarea', node => node.props.maxLength === 2000), 'onChange', { target: { value: 'Keep this unsent comment' } })
      failed = true; await h.advance(30000)
      assert.ok(h.nodes().some(node => node.props.role === 'alert' && h.text(node).includes(h.t('error_unknown'))))
      assert.ok(h.visibleText.includes('Retained private timeline entry'))
      assert.ok(h.visibleText.includes(h.t('freshness_stale')))
      assert.equal(h.find('textarea', node => node.props.maxLength === 2000).props.value, 'Keep this unsent comment')
      assert.ok(h.calls.every(call => call.name.startsWith('get')), 'No draft is submitted by refresh or failure')
    } finally { h.dispose() }
  }
})

test('separate keyed timeline entries keep independent comment drafts when reordered and another entry arrives', async () => {
  const entry = (id, body) => ({ id, project_id: ID, kind: 'progress', body, occurred_on: '2026-01-02', correction_of: null, author, media_ids: [], created_at: '2026-01-02T12:00:00Z' })
  const first = entry(ENTRY, 'First keyed entry'), second = entry(fixtureId(90), 'Second keyed entry')
  let entries = [first, second]
  const h = ui('components/network/ProjectWorkspace.tsx', 'ProjectDetail', { id: ID, onBack() {} }, {
    getProject: async () => project({ my_role: 'professional', can_update: true }),
    getProjectUpdates: async () => page(entries), getUpdateComments: async () => page([]),
  })
  const entryNode = body => h.find('li', node => h.text(node).includes(body))
  const commentsButton = body => h.nodes(entryNode(body)).find(node => node.type === 'button' && h.text(node) === h.t('comments'))
  const draft = body => h.nodes(entryNode(body)).find(node => node.type === 'textarea' && node.props.maxLength === 2000)
  try {
    await h.settle()
    await h.fire(commentsButton('First keyed entry'), 'onClick')
    await h.fire(draft('First keyed entry'), 'onChange', { target: { value: 'Draft only for first entry' } })
    assert.ok(commentsButton('Second keyed entry'), 'Opening the first thread must not open the second')
    assert.equal(draft('Second keyed entry'), undefined)
    await h.fire(commentsButton('Second keyed entry'), 'onClick')
    await h.fire(draft('Second keyed entry'), 'onChange', { target: { value: 'Different draft only for second entry' } })
    assert.equal(draft('First keyed entry').props.value, 'Draft only for first entry')
    assert.equal(draft('Second keyed entry').props.value, 'Different draft only for second entry')
    entries = [second, entry(fixtureId(91), 'New third keyed entry'), first]
    await h.advance(30000)
    assert.ok(h.visibleText.includes('New third keyed entry'))
    assert.equal(draft('First keyed entry').props.value, 'Draft only for first entry')
    assert.equal(draft('Second keyed entry').props.value, 'Different draft only for second entry')
    assert.equal(draft('New third keyed entry'), undefined)
    assert.ok(h.calls.every(call => call.name.startsWith('get')), 'Polling and reorder must not submit a draft')
  } finally { h.dispose() }
})

const registryCatalog = { categories: [{ id: 'air_conditioning', label: { en: 'Air conditioning', es: 'Aire acondicionado' } }], areas: [], languages: [], property_classes: [{ id: 'house_villa', label: { en: 'House or villa', es: 'Casa o villa' } }] }
const privateRecord = changes => ({ id: LISTING, title: 'Private synthetic property', location: 'Synthetic town', address: 'Private address kept out of project views', property_class: 'house_villa', relationship: 'manager', notes: 'Private notes kept out of project views', status: 'active', version: 1, can_manage: true, can_create_project: true, created_at: '2026-01-01T12:00:00Z', updated_at: '2026-01-01T12:00:00Z', ...changes })
const registryPage = (items, canCreate = true) => ({ ...page(items), can_create: canCreate })
function registryField(h, key, type) {
  const label = h.find('label', node => h.text(node).startsWith(h.t(key)))
  const field = h.nodes(label).find(node => node.type === type)
  assert.ok(field, `Missing accessible ${key} field`)
  return field
}
async function fillPrivateProperty(h) {
  for (const [key, type, value] of [['registry.title', 'input', 'My private property draft'], ['registry.location', 'input', 'My private town'], ['registry.address', 'textarea', 'My private address'], ['registry.notes', 'textarea', 'My private notes'], ['property_class', 'select', 'house_villa'], ['registry.relationship', 'select', 'seller']]) await h.fire(registryField(h, key, type), 'onChange', { target: { value } })
  await h.fire(registryField(h, 'registry.authority', 'input'), 'onChange', { target: { checked: true } })
}

test('actual bilingual private registration keeps uncertain fields and UUID and resolves only by an explicit read', async () => {
  for (const language of ['en', 'es']) {
    const opened = []
    const h = ui('components/network/PropertyRegistry.tsx', 'PrivatePropertyCreate', { options: registryCatalog, onCreated: id => opened.push(id) }, {
      createPrivateProperty: async () => { throw new Error('Synthetic uncertain connection') },
      getPrivatePropertySubmission: async () => privateRecord(),
    }, language)
    try {
      await h.settle()
      assert.ok(h.visibleText.includes(h.t('registry.unverified')))
      assert.equal(h.calls.length, 0, 'Rendering an empty form cannot register anything')
      await fillPrivateProperty(h)
      await h.fire(h.find('form'), 'onSubmit', { preventDefault() {} })
      const write = h.calls.find(call => call.name === 'createPrivateProperty').args[0]
      assert.equal(write.title, 'My private property draft'); assert.equal(write.authority_acknowledged, true)
      assert.equal(write.relationship, 'seller'); assert.ok(write.submission_id)
      for (const key of ['custodian_id', 'owner_id', 'is_public', 'role']) assert.equal(Object.hasOwn(write, key), false)
      assert.equal(registryField(h, 'registry.title', 'input').props.value, write.title)
      assert.ok(h.visibleText.includes(h.t('registry.uncertain')))
      await h.advance(30000)
      assert.equal(h.calls.filter(call => call.name === 'createPrivateProperty').length, 1, 'No timer retries a mutation')
      await h.fire(h.find('button', node => h.text(node) === h.t('registry.check_result')), 'onClick')
      assert.equal(h.calls.find(call => call.name === 'getPrivatePropertySubmission').args[0], write.submission_id)
      assert.deepEqual(opened, [LISTING])
      assert.equal(h.calls.filter(call => call.name === 'createPrivateProperty').length, 1)
    } finally { h.dispose() }
  }
})

test('actual explicit private property retry reuses the original payload after a not-found result check', async () => {
  let attempts = 0
  const h = ui('components/network/PropertyRegistry.tsx', 'PrivatePropertyCreate', { options: registryCatalog, onCreated() {} }, {
    createPrivateProperty: async () => { if (++attempts === 1) throw new Error('Synthetic uncertain response'); return privateRecord() },
    getPrivatePropertySubmission: async () => { throw { isAxiosError: true, response: { status: 404 } } },
  })
  try {
    await h.settle(); await fillPrivateProperty(h)
    await h.fire(h.find('form'), 'onSubmit', { preventDefault() {} })
    const original = h.calls.find(call => call.name === 'createPrivateProperty').args[0]
    await h.fire(h.find('button', node => h.text(node) === h.t('registry.check_result')), 'onClick')
    assert.ok(h.visibleText.includes(h.t('registry.not_found_submission')))
    assert.equal(registryField(h, 'registry.notes', 'textarea').props.value, original.notes)
    assert.equal(attempts, 1, 'The result check must be read-only')
    await h.fire(h.find('form'), 'onSubmit', { preventDefault() {} })
    const writes = h.calls.filter(call => call.name === 'createPrivateProperty')
    assert.equal(writes.length, 2)
    assert.deepEqual(writes[1].args[0], original)
  } finally { h.dispose() }
})

test('actual registry conflict clears previous results but retains a locked draft until an explicit actual refresh', async () => {
  let current = privateRecord(), attempts = 0
  const history = { id: ENTRY, project_id: ID, project_title: 'Old authorized service', body: 'Old authorized history body', occurred_on: '2026-01-01', media_ids: [] }
  const h = ui('components/network/PropertyRegistry.tsx', 'PrivatePropertyDetail', { id: LISTING, options: registryCatalog, onBack() {}, onCreateProject() {} }, {
    getPrivateProperty: async () => current,
    getPrivatePropertyServiceHistory: async () => page([history]),
    updatePrivateProperty: async (id, input) => { if (++attempts === 1) throw { isAxiosError: true, response: { status: 409 } }; current = privateRecord({ ...input, id, version: 3 }); return current },
  })
  try {
    await h.settle()
    await h.fire(registryField(h, 'registry.title', 'input'), 'onChange', { target: { value: 'My unsaved conflict draft' } })
    await h.fire(h.find('form'), 'onSubmit', { preventDefault() {} })
    assert.ok(h.visibleText.includes(h.t('registry.refresh_required')))
    assert.ok(!h.visibleText.includes(history.body), 'Old history results must disappear after the conflict')
    assert.equal(registryField(h, 'registry.title', 'input').props.value, 'My unsaved conflict draft')
    current = privateRecord({ title: 'Changed remotely', version: 2 })
    await h.advance(30000)
    assert.equal(registryField(h, 'registry.title', 'input').props.value, 'My unsaved conflict draft')
    assert.equal(h.find('fieldset').props.disabled, true)
    assert.equal(attempts, 1, 'Polling cannot silently repeat or rebase the failed write')
    await h.fire(h.find('button', node => h.text(node) === h.t('registry.refresh_keep_draft')), 'onClick')
    assert.equal(registryField(h, 'registry.title', 'input').props.value, 'My unsaved conflict draft')
    await h.fire(h.find('form'), 'onSubmit', { preventDefault() {} })
    const writes = h.calls.filter(call => call.name === 'updatePrivateProperty')
    assert.equal(writes[0].args[1].expected_version, 1)
    assert.equal(writes[1].args[1].expected_version, 2)
    assert.equal(writes[1].args[1].title, 'My unsaved conflict draft')
  } finally { h.dispose() }
})

test('actual archive retains authorized history and removes new-project controls without claiming deletion', async () => {
  let current = privateRecord()
  const history = { id: ENTRY, project_id: ID, project_title: 'Retained service', body: 'History survives archive', occurred_on: '2026-01-01', media_ids: [] }
  const h = ui('components/network/PropertyRegistry.tsx', 'PrivatePropertyDetail', { id: LISTING, options: registryCatalog, onBack() {}, onCreateProject() {} }, {
    getPrivateProperty: async () => current,
    getPrivatePropertyServiceHistory: async () => page([history]),
    updatePrivateProperty: async (id, input) => { current = privateRecord({ ...input, id, version: 2, can_create_project: false }); return current },
  })
  try {
    await h.settle()
    assert.ok(h.visibleText.includes(h.t('registry.create_project')))
    await h.fire(h.find('button', node => h.text(node) === h.t('registry.archive')), 'onClick')
    const write = h.calls.find(call => call.name === 'updatePrivateProperty')
    assert.equal(write.args[1].status, 'archived'); assert.equal(write.args[1].expected_version, 1)
    assert.equal(h.confirmations, 1)
    assert.ok(h.visibleText.includes(history.body))
    assert.ok(h.visibleText.includes(h.t('registry.archived_note')))
    assert.ok(!h.visibleText.includes(h.t('registry.create_project')))
    assert.equal(Object.hasOwn(write.args[1], 'custodian_id'), false)
  } finally { h.dispose() }
})

test('actual registry permission loss removes private details, draft fields and history', async () => {
  let denied = false
  const h = ui('components/network/PropertyRegistry.tsx', 'PrivatePropertyDetail', { id: LISTING, options: registryCatalog, onBack() {}, onCreateProject() {} }, {
    getPrivateProperty: async () => { if (denied) throw { isAxiosError: true, response: { status: 403 } }; return privateRecord() },
    getPrivatePropertyServiceHistory: async () => page([]),
  })
  try {
    await h.settle()
    await h.fire(registryField(h, 'registry.notes', 'textarea'), 'onChange', { target: { value: 'A sensitive unsaved draft' } })
    denied = true; await h.advance(30000)
    assert.ok(h.visibleText.includes(h.t('error_access')))
    assert.ok(!h.html.includes('Private address kept out'))
    assert.ok(!h.html.includes('A sensitive unsaved draft'))
    assert.ok(!h.nodes().some(node => node.type === 'form'))
    assert.ok(h.calls.every(call => call.name.startsWith('get')))
  } finally { h.dispose() }
})

test('actual buyer navigation opens only its capability-authorized private property form', async () => {
  for (const canCreate of [true, false]) {
    const h = ui('pages/shared/PropertyNetwork.tsx', 'PropertyNetwork', { user: { id: ID, user_code: 71, role: 'buyer', display_name: 'Synthetic buyer', email: 'synthetic@example.invalid' } }, {
      getProfessionalOptions: async () => registryCatalog,
      getProjects: async () => page([]),
      getPrivateProperties: async () => registryPage([], canCreate),
    })
    try {
      await h.settle()
      await h.fire(h.find('button', node => h.text(node) === h.t('main.properties')), 'onClick')
      assert.ok(h.visibleText.includes(h.t('registry.heading')))
      assert.equal(h.nodes().some(node => node.type === 'button' && h.text(node) === h.t('registry.add')), canCreate)
      if (canCreate) {
        await h.fire(h.find('button', node => h.text(node) === h.t('registry.add')), 'onClick')
        assert.ok(h.nodes().some(node => node.type === 'form'))
        assert.ok(h.visibleText.includes(h.t('registry.authority')))
      }
      assert.ok(h.calls.every(call => call.name.startsWith('get')))
      assert.ok(!h.calls.some(call => call.name === 'getNetworkProperties'))
      assert.ok(!h.html.includes('AdminDash') && !h.html.includes('workspace=admin'))
    } finally { h.dispose() }
  }
})

test('actual buyer project form selects private property without managed-listing reads and resolves uncertainty explicitly', async () => {
  const opened = []
  const h = ui('components/network/ProjectWorkspace.tsx', 'ProjectCreate', { options: registryCatalog, canUseListings: false, onCreated: id => opened.push(id) }, {
    getPrivateProperties: async () => registryPage([privateRecord()]),
    createProject: async () => { throw new Error('Synthetic uncertain response') },
    getProjectSubmission: async () => project({ listing_id: null, property_id: LISTING, property_title: 'Private synthetic property' }),
  })
  try {
    await h.settle()
    await h.fire(registryField(h, 'registry.choose_private', 'select'), 'onChange', { target: { value: LISTING } })
    await h.fire(registryField(h, 'title', 'input'), 'onChange', { target: { value: 'Private service project' } })
    await h.fire(h.find('input', node => node.props.type === 'checkbox'), 'onChange', { target: { checked: true } })
    assert.equal(registryField(h, 'property_class', 'select').props.value, 'house_villa')
    assert.equal(registryField(h, 'property_class', 'select').props.disabled, true)
    await h.fire(h.find('form'), 'onSubmit', { preventDefault() {} })
    const write = h.calls.find(call => call.name === 'createProject').args[0]
    assert.equal(write.property_id, LISTING); assert.ok(write.submission_id)
    assert.equal(Object.hasOwn(write, 'listing_id'), false)
    await h.advance(30000)
    assert.equal(h.calls.filter(call => call.name === 'createProject').length, 1)
    await h.fire(h.find('button', node => h.text(node) === h.t('registry.check_result')), 'onClick')
    assert.equal(h.calls.find(call => call.name === 'getProjectSubmission').args[0], write.submission_id)
    assert.deepEqual(opened, [ID])
    assert.ok(!h.calls.some(call => call.name === 'getNetworkProperties' || call.name === 'getPrivateProperty'))
  } finally { h.dispose() }
})

test('actual project source switching drops the listing reference and submits only the selected private source', async () => {
  let created
  const h = ui('components/network/ProjectWorkspace.tsx', 'ProjectCreate', { options: registryCatalog, canUseListings: true, onCreated() {} }, {
    getNetworkProperties: async () => page([{ id: PHOTO1, title: 'Managed synthetic listing', location: 'Synthetic area', type: 'villa' }]),
    getPrivateProperties: async () => registryPage([privateRecord()]),
    createProject: async input => { created = input; return project({ listing_id: null, property_id: LISTING, property_title: 'Private synthetic property' }) },
  })
  try {
    await h.settle()
    await h.fire(registryField(h, 'property', 'select'), 'onChange', { target: { value: PHOTO1 } })
    await h.fire(registryField(h, 'registry.project_source', 'select'), 'onChange', { target: { value: 'private' } })
    await h.fire(registryField(h, 'registry.choose_private', 'select'), 'onChange', { target: { value: LISTING } })
    await h.fire(registryField(h, 'title', 'input'), 'onChange', { target: { value: 'Exactly one property' } })
    await h.fire(h.find('input', node => node.props.type === 'checkbox'), 'onChange', { target: { checked: true } })
    await h.fire(h.find('form'), 'onSubmit', { preventDefault() {} })
    assert.equal(created.property_id, LISTING)
    assert.equal(Object.hasOwn(created, 'listing_id'), false)
    assert.equal(created.property_class, 'house_villa')
  } finally { h.dispose() }
})

test('actual private-project participants read scoped history without fetching full registry records', async () => {
  const history = { id: ENTRY, project_id: ID, project_title: 'Assigned synthetic project', body: 'Only accessible service history', occurred_on: '2026-01-01', media_ids: [] }
  const h = ui('components/network/ProjectWorkspace.tsx', 'ProjectDetail', { id: ID, onBack() {} }, {
    getProject: async () => project({ listing_id: null, property_id: LISTING, property_title: 'Authorized private property title', listing_title: 'Authorized private property title', address: 'Forbidden full registry address', notes: 'Forbidden full registry notes' }),
    getProjectUpdates: async () => page([]),
    getPrivatePropertyServiceHistory: async () => page([history]),
  })
  try {
    await h.settle()
    assert.ok(h.visibleText.includes('Authorized private property title'))
    await h.fire(h.find('button', node => h.text(node) === h.t('tabs.history')), 'onClick')
    assert.ok(h.visibleText.includes(history.body))
    assert.equal(h.calls.find(call => call.name === 'getPrivatePropertyServiceHistory').args[0], LISTING)
    assert.ok(!h.calls.some(call => call.name === 'getPrivateProperty' || call.name === 'getPrivateProperties' || call.name === 'getPropertyServiceHistory'))
    assert.ok(!h.html.includes('Forbidden full registry'))
  } finally { h.dispose() }
})

test('actual property list refresh preserves an open draft, pauses hidden reads and clears it on permission loss', async () => {
  let denied = false
  const h = ui('components/network/PropertyRegistry.tsx', 'PrivatePropertyList', { options: registryCatalog, onOpen() {} }, {
    getPrivateProperties: async () => { if (denied) throw { isAxiosError: true, response: { status: 403 } }; return registryPage([]) },
  })
  try {
    await h.settle()
    await h.fire(h.find('button', node => h.text(node) === h.t('registry.add')), 'onClick')
    await h.fire(registryField(h, 'registry.title', 'input'), 'onChange', { target: { value: 'Open list draft retained' } })
    await h.fire(h.find('button', node => h.text(node) === h.t('refresh')), 'onClick')
    assert.equal(registryField(h, 'registry.title', 'input').props.value, 'Open list draft retained')
    await h.presence(false, true)
    const before = h.calls.length
    await h.advance(60000)
    assert.equal(h.calls.length, before, 'A hidden property list cannot poll')
    denied = true
    await h.presence(true, true)
    assert.ok(h.visibleText.includes(h.t('error_access')))
    assert.ok(!h.html.includes('Open list draft retained'))
    assert.ok(!h.nodes().some(node => node.type === 'form'))
    assert.ok(h.calls.every(call => call.name === 'getPrivateProperties'))
  } finally { h.dispose() }
})

test('actual late project-create result cannot navigate after registry permission was revoked', async () => {
  let denied = false, resolveCreate
  const opened = []
  const h = ui('components/network/ProjectWorkspace.tsx', 'ProjectCreate', { options: registryCatalog, canUseListings: false, onCreated: id => opened.push(id) }, {
    getPrivateProperties: async () => { if (denied) throw { isAxiosError: true, response: { status: 403 } }; return registryPage([privateRecord()]) },
    createProject: async () => new Promise(resolve => { resolveCreate = resolve }),
  })
  try {
    await h.settle()
    await h.fire(registryField(h, 'registry.choose_private', 'select'), 'onChange', { target: { value: LISTING } })
    await h.fire(registryField(h, 'title', 'input'), 'onChange', { target: { value: 'Pending authorized draft' } })
    await h.fire(h.find('input', node => node.props.type === 'checkbox'), 'onChange', { target: { checked: true } })
    await h.fire(h.find('form'), 'onSubmit', { preventDefault() {} })
    denied = true; await h.advance(30000)
    assert.ok(h.visibleText.includes(h.t('error_access')))
    assert.ok(!h.nodes().some(node => node.type === 'form'))
    resolveCreate(project({ listing_id: null, property_id: LISTING, property_title: 'Private synthetic property' }))
    await h.settle()
    assert.deepEqual(opened, [])
    assert.equal(h.calls.filter(call => call.name === 'createProject').length, 1)
    assert.equal(h.calls.find(call => call.name === 'createProject').args[1].aborted, true)
  } finally { h.dispose() }
})

const servicePreferences = changes => ({service_opt_in:false,member_alias:'',category_ids:[],property_class_ids:[],version:0,updated_at:null,can_manage:true,...changes})
const serviceProposal = changes => ({id:ENTRY,recipient_id:PHOTO1,revision_no:1,parent_proposal_id:null,kind:'provider_quote',status:'submitted',version:1,amount:'1234.50',currency:'USD',pricing_basis:'fixed',scope_text:'Synthetic quoted work',exclusions:'Synthetic exclusion',estimated_days:2,valid_until:'2026-01-30',is_expired:false,created_at:'2026-01-02T12:00:00Z',...changes})
const serviceRecipient = changes => ({id:PHOTO1,user_code:72,member_alias:'Synthetic provider alias',status:'accepted',version:2,current_proposal:serviceProposal(),capabilities:{can_revoke:true,can_counter:true,can_award:true,can_withdraw:false},...changes})
const serviceReceived = changes => ({id:ID,view:'received',category_id:'air_conditioning',property_class:'house_villa',title:'Shared request title',brief:'Shared request brief',area_label:'Shared coarse area',requester_alias:'Shared requester alias',desired_start_on:'2026-01-10',created_at:'2026-01-02T12:00:00Z',recipient:{id:PHOTO1,status:'accepted',version:2},current_proposal:serviceProposal(),project_id:null,can_open_project:false,capabilities:{can_accept:false,can_decline:true,can_quote:true,can_withdraw:true,can_accept_counter:false,can_decline_counter:false},...changes})
const serviceManaged = changes => ({id:ID,view:'managed',version:1,status:'open',source:{property_id:LISTING,listing_id:null,title:'Private source title',property_class:'house_villa'},category_id:'air_conditioning',title:'Shared request title',brief:'Shared request brief',area_label:'Shared coarse area',requester_alias:'Shared requester alias',desired_start_on:null,created_at:'2026-01-02T12:00:00Z',updated_at:'2026-01-02T12:00:00Z',recipients:[serviceRecipient()],project_id:null,can_open_project:false,capabilities:{can_cancel:true,can_compare:true,can_award:true},...changes})
function serviceReads(read) {
  return {getServiceRequest:async()=>read(),getServiceEvents:async()=>page([]),getServiceProposals:async()=>page([]),
    getServiceComparison:async()=>({request_id:ID,request_version:read().version,groups:[{currency:'USD',items:read().recipients}],unquoted:[]})}
}

test('actual service self-consent is default-off and saves only explicit own preferences in EN and ES',async()=>{
  for(const language of ['en','es']){
    let current=servicePreferences()
    const h=ui('components/network/ServicePreferences.tsx','ServicePreferences',{options:registryCatalog,actorId:ID},{
      getServicePreferences:async()=>current,
      runServiceAction:async action=>{assert.equal(action.operation,'preference_save');assert.equal(action.expected_actor_id,ID);const input=action.input;current=servicePreferences({service_opt_in:input.service_opt_in,member_alias:input.member_alias,category_ids:input.category_ids,property_class_ids:input.property_class_ids,version:1});return current},
    },language)
    try{
      await h.settle();assert.equal(h.calls.filter(c=>c.name==='runServiceAction').length,0)
      assert.equal(h.find('input',n=>n.props.type==='checkbox').props.checked,false)
      await h.fire(registryField(h,'service.alias','input'),'onChange',{target:{value:'My deliberate service alias'}})
      for(const labelKey of ['service.opt_in','service.categories','service.classes']){
        const input=labelKey==='service.opt_in'?h.find('input',n=>n.props.type==='checkbox'):h.nodes(h.find('fieldset',n=>h.text(n).startsWith(h.t(labelKey)))).find(n=>n.type==='input')
        await h.fire(input,'onChange',{target:{checked:true}})
      }
      await h.fire(h.find('form'),'onSubmit',{preventDefault(){}})
      const action=h.calls.find(c=>c.name==='runServiceAction').args[0]
      assert.equal(action.input.service_opt_in,true);assert.equal(action.input.member_alias,'My deliberate service alias')
      assert.deepEqual(Array.from(action.input.category_ids),['air_conditioning'])
      for(const key of ['user_id','actor_id','role','professional_profile_id','public_contact_consent','email','phone'])assert.equal(Object.hasOwn(action.input,key),false)
      assert.ok(h.visibleText.includes(h.t('service.unverified')))
    }finally{h.dispose()}
  }
})
test('actual preference polling keeps a dirty alias and requires explicit current-version review',async()=>{
  let current=servicePreferences(),saved
  const h=ui('components/network/ServicePreferences.tsx','ServicePreferences',{options:registryCatalog,actorId:ID},{
    getServicePreferences:async()=>current,
    runServiceAction:async action=>{saved=action;return servicePreferences({member_alias:action.input.member_alias,version:3})},
  })
  try{
    await h.settle();await h.fire(registryField(h,'service.alias','input'),'onChange',{target:{value:'My unsaved service alias'}})
    current=servicePreferences({member_alias:'Changed by another session',version:2});await h.advance(30000)
    assert.equal(registryField(h,'service.alias','input').props.value,'My unsaved service alias')
    assert.ok(h.visibleText.includes(h.t('service.changed_review')))
    assert.equal(h.calls.filter(c=>c.name==='runServiceAction').length,0)
    await h.fire(h.find('button',n=>h.text(n)===h.t('service.review_current')),'onClick')
    await h.fire(h.find('form'),'onSubmit',{preventDefault(){}})
    assert.equal(saved.input.expected_version,2);assert.equal(saved.input.member_alias,'My unsaved service alias')
  }finally{h.dispose()}
})
test('actual buyer request composer previews exact recipients and never shares its private source dossier',async()=>{
  const opened=[]
  const h=ui('components/network/ServiceRequestCreate.tsx','ServiceRequestCreate',{options:registryCatalog,role:'buyer',actorCode:71,onCreated:id=>opened.push(id)},{
    getPrivateProperties:async()=>registryPage([privateRecord()]),
    getServiceProviders:async()=>page([{user_code:71,member_alias:'My synthetic service alias',category_ids:['air_conditioning']},{user_code:72,member_alias:'Selected synthetic provider',category_ids:['air_conditioning']}]),
    runServiceAction:async action=>{assert.equal(action.operation,'request_create');return serviceManaged()},
  })
  try{
    await h.settle();await h.fire(registryField(h,'service.choose_source','select'),'onChange',{target:{value:LISTING}})
    await h.fire(registryField(h,'service.category','select'),'onChange',{target:{value:'air_conditioning'}})
    const ownInput=h.nodes(h.find('label',n=>h.text(n).includes('My synthetic service alias'))).find(n=>n.type==='input')
    assert.equal(ownInput.props.disabled,true)
    const providerInput=h.nodes(h.find('label',n=>h.text(n).includes('Selected synthetic provider'))).find(n=>n.type==='input')
    await h.fire(providerInput,'onChange',{target:{checked:true}})
    for(const [key,type,value]of [['service.request_title','input','Exact shared title'],['service.requester_alias','input','Explicit shared alias'],['service.brief','textarea','Exact deliberate brief'],['service.coarse_area','input','Only coarse area'],['service.desired_start','input','2026-01-10']])await h.fire(registryField(h,key,type),'onChange',{target:{value}})
    assert.equal(h.calls.filter(c=>c.name==='runServiceAction').length,0)
    await h.fire(h.find('button',n=>h.text(n)===h.t('service.preview')),'onClick')
    for(const value of ['Exact shared title','Explicit shared alias','Exact deliberate brief','Only coarse area','Selected synthetic provider'])assert.ok(h.visibleText.includes(value))
    assert.ok(!h.visibleText.includes(privateRecord().address));assert.ok(!h.visibleText.includes(privateRecord().notes))
    await h.fire(registryField(h,'service.share_ack','input'),'onChange',{target:{checked:true}})
    await h.fire(h.find('form'),'onSubmit',{preventDefault(){}})
    const input=h.calls.find(c=>c.name==='runServiceAction').args[0].input
    assert.equal(input.property_id,LISTING);assert.equal(input.listing_id,null);assert.equal(input.requester_alias,'Explicit shared alias');assert.equal(input.desired_start_on,'2026-01-10')
    assert.deepEqual(Array.from(input.recipient_user_codes),[72]);assert.ok(input.submission_id)
    for(const key of ['address','location','notes','relationship','custodian_id'])assert.equal(Object.hasOwn(input,key),false)
    assert.ok(!h.calls.some(c=>c.name==='getNetworkProperties'||c.name==='getPrivateProperty'))
    assert.deepEqual(opened,[ID])
  }finally{h.dispose()}
})
test('actual provider thread follows received capabilities and never fetches a registry or competitor record',async()=>{
  let current=serviceReceived({recipient:{id:PHOTO1,status:'pending',version:1},current_proposal:null,capabilities:{can_accept:true,can_decline:true,can_quote:false,can_withdraw:false,can_accept_counter:false,can_decline_counter:false},address:'FORBIDDEN DOSSIER',competitor:'FORBIDDEN COMPETITOR'})
  const h=ui('components/network/ServiceRequestDetail.tsx','ServiceRequestDetail',{id:ID,view:'received',options:registryCatalog,onBack(){},onProject(){}},{
    ...serviceReads(()=>current),runServiceAction:async action=>{assert.equal(action.operation,'recipient_accept');assert.equal(Object.hasOwn(action.input,'expected_request_version'),false);current=serviceReceived();return current},
  })
  try{
    await h.settle();assert.ok(!h.visibleText.includes('FORBIDDEN'));assert.ok(h.visibleText.includes('Shared requester alias'))
    assert.ok(h.nodes().some(n=>n.type==='button'&&h.text(n)===h.t('service.accept')))
    assert.ok(!h.nodes().some(n=>n.type==='button'&&h.text(n)===h.t('service.quote')))
    await h.fire(h.find('button',n=>h.text(n)===h.t('service.accept')),'onClick')
    assert.ok(h.nodes().some(n=>n.type==='button'&&h.text(n)===h.t('service.quote')))
    assert.ok(h.calls.every(c=>!['getPrivateProperty','getPrivateProperties','getNetworkProperties','getServiceComparison','getProject'].includes(c.name)))
  }finally{h.dispose()}
})
test('actual quote draft retains amount and original versions when newer terms arrive',async()=>{
  let current=serviceReceived()
  const h=ui('components/network/ServiceRequestDetail.tsx','ServiceRequestDetail',{id:ID,view:'received',options:registryCatalog,onBack(){},onProject(){}},serviceReads(()=>current))
  try{
    await h.settle();await h.fire(h.find('button',n=>h.text(n)===h.t('service.quote')),'onClick')
    await h.fire(registryField(h,'service.amount','input'),'onChange',{target:{value:'9999999999.99'}})
    current=serviceReceived({recipient:{id:PHOTO1,status:'accepted',version:3},current_proposal:serviceProposal({version:2})});await h.advance(30000)
    assert.equal(registryField(h,'service.amount','input').props.value,'9999999999.99')
    assert.ok(h.visibleText.includes(h.t('service.changed_review')))
    assert.equal(h.find('button',n=>h.text(n)===h.t('service.submit_quote')).props.disabled,true)
    assert.equal(h.calls.filter(c=>c.name==='runServiceAction').length,0)
    await h.fire(h.find('button',n=>h.text(n)===h.t('service.review_current')),'onClick')
    assert.equal(registryField(h,'service.amount','input').props.value,'9999999999.99')
    assert.equal(h.find('button',n=>h.text(n)===h.t('service.submit_quote')).props.disabled,false)
  }finally{h.dispose()}
})
test('actual owner-counter withdrawal is shown only by the caller-specific server capability',async()=>{
  for(const canWithdraw of [false,true]){
    const item=serviceRecipient({current_proposal:serviceProposal({kind:'owner_counter',is_expired:true}),capabilities:{can_revoke:true,can_counter:true,can_award:false,can_withdraw:canWithdraw}})
    const current=serviceManaged({recipients:[item]})
    const h=ui('components/network/ServiceRequestDetail.tsx','ServiceRequestDetail',{id:ID,view:'managed',options:registryCatalog,onBack(){},onProject(){}},serviceReads(()=>current))
    try{
      await h.settle()
      assert.equal(h.nodes().some(n=>n.type==='button'&&h.text(n)===h.t('service.withdraw_counter')),canWithdraw)
      assert.ok(h.calls.every(c=>c.name.startsWith('get')))
    }finally{h.dispose()}
  }
})
test('actual award previews deliberately shared project scope and creates nothing before explicit acknowledgement',async()=>{
  let current=serviceManaged();const opened=[]
  const h=ui('components/network/ServiceRequestDetail.tsx','ServiceRequestDetail',{id:ID,view:'managed',options:registryCatalog,onBack(){},onProject:id=>opened.push(id)},{
    ...serviceReads(()=>current),runServiceAction:async action=>{assert.equal(action.operation,'request_award');current=serviceManaged({status:'awarded',project_id:LISTING,can_open_project:true,capabilities:{can_cancel:false,can_compare:false,can_award:false}});return {request:current,project:{id:LISTING}}},
  })
  try{
    await h.settle();await h.fire(h.find('button',n=>h.text(n)===h.t('service.review_award')),'onClick')
    assert.equal(h.calls.filter(c=>c.name==='runServiceAction').length,0)
    assert.equal(h.find('button',n=>h.text(n)===h.t('service.award')).props.disabled,true)
    await h.fire(registryField(h,'service.project_title','input'),'onChange',{target:{value:'Approved project title'}})
    await h.fire(registryField(h,'service.project_scope','textarea'),'onChange',{target:{value:'Approved participant scope'}})
    await h.fire(registryField(h,'service.project_ack','input'),'onChange',{target:{checked:true}})
    await h.fire(h.find('form'),'onSubmit',{preventDefault(){}})
    const input=h.calls.find(c=>c.name==='runServiceAction').args[0].input
    assert.equal(input.project_title,'Approved project title');assert.equal(input.project_summary,'Approved participant scope');assert.equal(input.proposal_id,ENTRY);assert.equal(input.expected_proposal_version,1)
    for(const key of ['amount','currency','address','area_label','losing_providers','service_request_id'])assert.equal(Object.hasOwn(input,key),false)
    await h.fire(h.find('button',n=>h.text(n)===h.t('service.open_project')),'onClick')
    assert.deepEqual(opened,[LISTING]);assert.equal(h.calls.filter(c=>c.name==='runServiceAction').length,1)
  }finally{h.dispose()}
})
test('actual unknown service outcome requires a separate authorized read before explicit identical retry',async()=>{
  const attempts=[];let reads=0
  const h=ui('components/network/ServiceRequestDetail.tsx','ServiceRequestDetail',{id:ID,view:'received',options:registryCatalog,onBack(){},onProject(){}},{
    ...serviceReads(()=>serviceReceived()),getServiceRequest:async()=>{reads++;return serviceReceived()},
    runServiceAction:async action=>{attempts.push(action);if(attempts.length===1)throw new Error('Synthetic uncertain outcome');return serviceReceived()},
    getServiceSubmission:async()=>{throw {isAxiosError:true,response:{status:404}}},
  })
  try{
    await h.settle();await h.fire(h.find('button',n=>h.text(n)===h.t('service.decline')),'onClick')
    assert.equal(attempts.length,1);await h.advance(30000);assert.equal(attempts.length,1)
    assert.ok(!h.nodes().some(n=>n.type==='button'&&h.text(n)===h.t('service.retry_same')))
    const before=reads;await h.fire(h.find('button',n=>h.text(n)===h.t('service.check_result')),'onClick');assert.ok(reads>before)
    assert.equal(attempts.length,1);assert.ok(h.visibleText.includes(h.t('service.ambiguous_result')))
    await h.fire(h.find('button',n=>h.text(n)===h.t('service.retry_same')),'onClick')
    assert.equal(attempts.length,2);assert.deepEqual(attempts[1],attempts[0])
  }finally{h.dispose()}
})
test('actual access loss aborts a pending service action and ignores its late successful result',async()=>{
  let denied=false,resolveWrite
  const h=ui('components/network/ServiceRequestDetail.tsx','ServiceRequestDetail',{id:ID,view:'received',options:registryCatalog,onBack(){},onProject(){}},{
    ...serviceReads(()=>serviceReceived()),getServiceRequest:async()=>{if(denied)throw {isAxiosError:true,response:{status:403}};return serviceReceived()},
    runServiceAction:async()=>new Promise(resolve=>{resolveWrite=resolve}),
  })
  try{
    await h.settle();await h.fire(h.find('button',n=>h.text(n)===h.t('service.decline')),'onClick')
    assert.ok(h.visibleText.includes(h.t('service.saving_action')));assert.ok(!h.visibleText.includes(h.t('service.uncertain')))
    denied=true;await h.advance(30000);assert.ok(h.visibleText.includes(h.t('error_access')));assert.ok(!h.visibleText.includes('Shared request brief'))
    assert.equal(h.calls.find(c=>c.name==='runServiceAction').args[1].aborted,true)
    resolveWrite(serviceReceived({project_id:LISTING,can_open_project:true}));await h.settle()
    assert.ok(!h.visibleText.includes('Shared request brief'));assert.ok(!h.nodes().some(n=>n.type==='button'&&h.text(n)===h.t('service.open_project')))
  }finally{h.dispose()}
})
test('actual prior terms display real immutable revisions through only the selected own thread reader',async()=>{
  const prior=serviceProposal({id:PHOTO2,revision_no:1,status:'superseded',amount:'125.25',scope_text:'Earlier immutable scope'}),latest=serviceProposal({revision_no:2,parent_proposal_id:PHOTO2,amount:'150.75',scope_text:'Latest immutable scope'})
  const current=serviceReceived({current_proposal:latest})
  const h=ui('components/network/ServiceRequestDetail.tsx','ServiceRequestDetail',{id:ID,view:'received',options:registryCatalog,onBack(){},onProject(){}},{
    ...serviceReads(()=>current),getServiceProposals:async(requestId,recipientId)=>{assert.equal(requestId,ID);assert.equal(recipientId,PHOTO1);return page([latest,prior])},
  })
  try{
    await h.settle();assert.ok(h.visibleText.includes('Earlier immutable scope'));assert.ok(h.visibleText.includes('USD 125.25'));assert.ok(h.visibleText.includes(h.t('service.proposal_states.superseded')))
    assert.ok(h.calls.every(c=>!['getPrivateProperty','getServiceComparison','getNetworkProperties','getProject'].includes(c.name)))
  }finally{h.dispose()}
})

test('actual service409 hides old results while preserving the frozen attempt and quote draft for explicit review',async()=>{
  let current=serviceReceived();const attempts=[]
  const h=ui('components/network/ServiceRequestDetail.tsx','ServiceRequestDetail',{id:ID,view:'received',options:registryCatalog,onBack(){},onProject(){}},{
    ...serviceReads(()=>current),runServiceAction:async action=>{attempts.push(action);if(attempts.length===1)throw {isAxiosError:true,response:{status:409}};return current},
    getServiceSubmission:async()=>{throw {isAxiosError:true,response:{status:404}}},
  })
  try{
    await h.settle();await h.fire(h.find('button',n=>h.text(n)===h.t('service.quote')),'onClick')
    await h.fire(registryField(h,'service.amount','input'),'onChange',{target:{value:'222.25'}})
    await h.fire(h.find('form'),'onSubmit',{preventDefault(){}})
    assert.equal(registryField(h,'service.amount','input').props.value,'222.25')
    assert.ok(!h.visibleText.includes('USD 1234.50'));assert.ok(!h.visibleText.includes('Shared request brief'))
    current=serviceReceived({recipient:{id:PHOTO1,status:'accepted',version:3},current_proposal:serviceProposal({version:2})})
    await h.fire(h.find('button',n=>h.text(n)===h.t('service.check_result')),'onClick')
    assert.equal(attempts.length,1)
    await h.fire(h.find('button',n=>h.text(n)===h.t('service.review_conflict')),'onClick')
    assert.equal(registryField(h,'service.amount','input').props.value,'222.25')
    await h.fire(h.find('form'),'onSubmit',{preventDefault(){}})
    assert.equal(attempts.length,2);assert.equal(attempts[0].input.expected_recipient_version,2);assert.equal(attempts[1].input.expected_recipient_version,3)
    assert.equal(attempts[1].input.terms.amount,'222.25');assert.notEqual(attempts[1].input.submission_id,attempts[0].input.submission_id)
  }finally{h.dispose()}
})

test('actual pasted invalid service input stays editable without transport and can be corrected explicitly',async()=>{
  let current=servicePreferences()
  const h=ui('components/network/ServicePreferences.tsx','ServicePreferences',{options:registryCatalog,actorId:ID},{
    getServicePreferences:async()=>current,
    runServiceAction:async action=>{current=servicePreferences({member_alias:action.input.member_alias,version:1});return current},
  })
  try{
    await h.settle()
    const pasted='Pasted\u0001service alias'
    await h.fire(registryField(h,'service.alias','input'),'onChange',{target:{value:pasted}})
    await h.fire(h.find('form'),'onSubmit',{preventDefault(){}})
    assert.equal(h.calls.filter(c=>c.name==='runServiceAction').length,0,'Pure input rejection must precede the API boundary')
    assert.equal(registryField(h,'service.alias','input').props.value,pasted,'Retain the rejected draft so it can be corrected')
    assert.equal(h.find('fieldset',n=>h.nodes(n).some(child=>child.type==='input')).props.disabled,false)
    assert.ok(h.visibleText.includes(h.t('error_fields')))
    assert.ok(!h.nodes().some(n=>n.type==='button'&&h.text(n)===h.t('service.check_result')))
    await h.fire(registryField(h,'service.alias','input'),'onChange',{target:{value:'Corrected deliberate alias'}})
    await h.fire(h.find('form'),'onSubmit',{preventDefault(){}})
    const writes=h.calls.filter(c=>c.name==='runServiceAction')
    assert.equal(writes.length,1);assert.equal(writes[0].args[0].input.member_alias,'Corrected deliberate alias')
    assert.ok(writes[0].args[0].input.submission_id)
  }finally{h.dispose()}
})
test('actual post-transport service response-contract failure stays frozen with its exact original body and key',async()=>{
  const writes=[]
  const h=ui('components/network/ServicePreferences.tsx','ServicePreferences',{options:registryCatalog,actorId:ID},{
    getServicePreferences:async()=>servicePreferences(),
    runServiceAction:async action=>{writes.push(action);await Promise.resolve();throw h.serviceContractError()},
    getServiceSubmission:async()=>{throw {isAxiosError:true,response:{status:404}}},
  })
  try{
    await h.settle()
    await h.fire(registryField(h,'service.alias','input'),'onChange',{target:{value:'Sent alias must stay frozen'}})
    await h.fire(h.find('form'),'onSubmit',{preventDefault(){}})
    assert.equal(writes.length,1);assert.ok(h.visibleText.includes(h.t('service.uncertain')))
    assert.equal(h.find('fieldset',n=>h.nodes(n).some(child=>child.type==='input')).props.disabled,true)
    await h.advance(30000);assert.equal(writes.length,1,'No automatic retry on malformed response or polling')
    await h.fire(h.find('form'),'onSubmit',{preventDefault(){}});assert.equal(writes.length,1,'A sent attempt cannot be edited or replaced')
    await h.fire(h.find('button',n=>h.text(n)===h.t('service.check_result')),'onClick')
    assert.equal(writes.length,1);assert.ok(h.visibleText.includes(h.t('service.ambiguous_result')))
    await h.fire(h.find('button',n=>h.text(n)===h.t('service.retry_same')),'onClick')
    assert.equal(writes.length,2);assert.deepEqual(writes[1],writes[0])
    assert.equal(registryField(h,'service.alias','input').props.value,'Sent alias must stay frozen')
    assert.equal(h.find('fieldset',n=>h.nodes(n).some(child=>child.type==='input')).props.disabled,true)
    assert.ok(h.nodes().some(n=>n.type==='button'&&h.text(n)===h.t('service.check_result')))
  }finally{h.dispose()}
})
