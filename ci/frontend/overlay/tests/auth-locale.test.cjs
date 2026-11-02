// Source-author definitions only. No account, OAuth provider, message or network is used.
const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const React = require('react')
const root = path.resolve(__dirname, '../apps/landing/src')

function fixture({ language = 'en', saved = 'es', pathname = '/verify/', storageThrows = false } = {}) {
  const calls = [], effects = [], oauth = [], timers = [], cache = new Map()
  const browser = { localStorage: { getItem(key) { assert.equal(key, 'ildr_lang'); if (storageThrows) throw Error('Disabled'); return saved } }, location: { pathname, href: '' } }
  const client = { post: async (url, body) => { calls.push({ url, body }); return { data: { expires_in: 900 } } } }
  const hooks = { ...React, useState: initial => [typeof initial === 'function' ? initial() : initial, () => {}], useRef: initial => ({ current: initial }), useEffect: callback => effects.push(callback) }
  function load(relative) {
    const absolute = path.resolve(root, relative)
    if (cache.has(absolute)) return cache.get(absolute).exports
    const module = { exports: {} }; cache.set(absolute, module)
    const compiled = ts.transpileModule(fs.readFileSync(absolute, 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText
    vm.runInNewContext(compiled, { module, exports: module.exports, window: browser, URLSearchParams,
      process: { env: {} }, setTimeout: callback => timers.push(callback), require(name) {
        if (name === 'react') return hooks
        if (name === 'react/jsx-runtime') return require(name)
        if (name === 'axios') return { isAxiosError: () => false }
        if (name.endsWith('/axios') || name === './axios') return { __esModule: true, default: client }
        if (name === 'react-i18next') return { useTranslation: () => ({ t: key => key, i18n: { language } }) }
        if (name === 'react-hot-toast') return { __esModule: true, default: { success() {}, error() {} } }
        if (name === 'next/navigation') return { useSearchParams: () => new URLSearchParams('email=synthetic%40example.invalid&code=123456') }
        if (name.endsWith('/hooks/useNav')) return { useNav: () => () => {} }
        if (name === '@react-oauth/google') return { useGoogleLogin: options => { oauth.push(options); return () => {} } }
        if (name.endsWith('/lib/googleAuth')) return { GOOGLE_CLIENT_ID: 'synthetic-client' }
        if (name.endsWith('/lib/professionalJoin')) return { dashboardAfterAuth: value => value, professionalAuthParams: () => ({}), rememberProfessionalJoin() {} }
        assert.ok(name.startsWith('.'), `Unexpected dependency ${name}`)
        const base = path.resolve(path.dirname(absolute), name)
        const target = ['.tsx', '.ts'].map(ext => base + ext).find(file => fs.existsSync(file))
        assert.ok(target, `Missing ${name}`)
        return load(path.relative(root, target))
      } }, { filename: absolute })
    return module.exports
  }
  return { load, calls, effects, oauth, timers, browser }
}

test('locale resolves explicit Spanish, saved auto-verify preference, active language and storage refusal', () => {
  const f = fixture(), { resolveAuthLocale, currentAuthLocale } = f.load('lib/authLocale.ts')
  assert.equal(resolveAuthLocale('en', 'en', '/es/verify/'), 'es')
  assert.equal(resolveAuthLocale('en', 'es', '/verify/'), 'es')
  assert.equal(resolveAuthLocale('es-DO', null), 'es')
  assert.equal(resolveAuthLocale('unsupported', 'unsupported'), 'en')
  assert.equal(currentAuthLocale('en'), 'es')
  assert.equal(fixture({ storageThrows: true, language: 'es' }).load('lib/authLocale.ts').currentAuthLocale('es'), 'es')
})

test('actual auth client sends only explicit supported locale and required verification/Google fields', async () => {
  const f = fixture(), api = f.load('api/auth.ts')
  await api.verify({ email: 'synthetic@example.invalid', code: '123456', locale: 'es', consent: true })
  await api.googleAuth('synthetic-access-token', 'en')
  assert.deepEqual(JSON.parse(JSON.stringify(f.calls)), [
    { url: '/auth/verify', body: { email: 'synthetic@example.invalid', code: '123456', locale: 'es' } },
    { url: '/auth/google', body: { access_token: 'synthetic-access-token', locale: 'en' } },
  ])
  await assert.rejects(api.verify({ email: 'synthetic@example.invalid', code: '123456', locale: 'fr' }), /Unsupported language/)
  await assert.rejects(api.googleAuth('synthetic-access-token', undefined), /Unsupported language/)
  assert.equal(f.calls.length, 2)
})

test('actual Verify initial email-link effect carries saved Spanish before i18n restoration', async () => {
  const f = fixture({ language: 'en', saved: 'es' })
  f.load('views/Verify.tsx').default()
  for (const effect of f.effects) effect()
  for (let tick = 0; tick < 8; tick++) await Promise.resolve()
  assert.equal(f.calls.length, 1)
  assert.equal(f.calls[0].url, '/auth/verify')
  assert.equal(f.calls[0].body.locale, 'es')
  assert.equal(f.browser.location.href, '') // Redirect timer not executed.
})

test('actual shared Google callback preserves locale for either first-account entry point', async () => {
  for (const mode of ['login', 'signup']) {
    const f = fixture({ language: 'en', saved: 'es', pathname: `/${mode}/` })
    const child = f.load('components/GoogleAuthSection.tsx').default({ mode })
    child.type(child.props)
    assert.equal(f.oauth.length, 1)
    await f.oauth[0].onSuccess({ access_token: 'synthetic-access-token' })
    assert.equal(f.calls.length, 1)
    assert.equal(f.calls[0].url, '/auth/google')
    assert.equal(f.calls[0].body.locale, 'es')
    assert.equal(f.browser.location.href, '')
  }
  for (const [file, mode] of [['views/Login.tsx', 'login'], ['views/Signup.tsx', 'signup']]) {
    const source = ts.createSourceFile(file, fs.readFileSync(path.join(root, file), 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
    const bindings = []
    function visit(node) { if ((ts.isJsxSelfClosingElement(node) || ts.isJsxOpeningElement(node)) && node.tagName.getText(source) === 'GoogleAuthSection') bindings.push(node.attributes.properties.find(prop => prop.name?.getText(source) === 'mode')?.initializer?.text); ts.forEachChild(node, visit) }
    visit(source)
    assert.deepEqual(bindings, [mode], `${file} must use the tested shared entry point`)
  }
})
