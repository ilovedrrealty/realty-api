const dashboard = 'http://127.0.0.1:4175'
const api = 'http://127.0.0.1:8000'
const timeout = 10_000

const labels = {
  en: {
    trigger: 'Share contact', title: 'Review contact before sharing', close: 'Close contact sharing',
    preview: 'Exact message preview', name: 'Name / business', email: 'Email', phone: 'Phone',
    permission: /^I have permission to share/, empty: 'Select an available detail to share.',
    copy: 'Copy preview', copied: 'Preview copied. Nothing has been sent.',
    emailDraft: 'Open email draft', whatsappDraft: 'Open WhatsApp draft',
    subject: 'Contact shared from iLoveDRRealty',
  },
  es: {
    trigger: 'Compartir contacto', title: 'Revisa el contacto antes de compartir', close: 'Cerrar compartir contacto',
    preview: 'Vista previa exacta del mensaje', name: 'Nombre / empresa', email: 'Correo electrónico', phone: 'Teléfono',
    permission: /^Tengo permiso para compartir/, empty: 'Selecciona un dato disponible para compartir.',
    copy: 'Copiar vista previa', copied: 'Vista previa copiada. No se ha enviado nada.',
    emailDraft: 'Abrir borrador de correo', whatsappDraft: 'Abrir borrador de WhatsApp',
    subject: 'Contacto compartido desde iLoveDRRealty',
  },
}

function check(condition, code) {
  if (!condition) throw new Error(code)
}

async function assertComposerGated(copy, emailLink, whatsappLink) {
  check(await copy.isDisabled() && await emailLink.count() === 0 && await whatsappLink.count() === 0,
    'SHARE_CONTACT_COMPOSER_GATE')
}

function expectGet(page, path) {
  return page.waitForResponse(response => response.url() === `${api}${path}`
    && response.request().method() === 'GET', { timeout })
}

async function installBrowserBoundary(dialog, expected) {
  // Limit the clipboard proof to the actual UI's writeText call in this local document.
  // Cancel the two anchor defaults before the browser can launch a composer or a tab.
  return dialog.evaluateHandle((panel, expected) => {
    if (location.origin !== expected.origin || !navigator.clipboard) return null
    const clipboard = navigator.clipboard
    const original = Object.getOwnPropertyDescriptor(clipboard, 'writeText')
    let writes = 0
    let exactCopy = false
    const clicks = { email: 0, whatsapp: 0, unexpected: 0 }
    Object.defineProperty(clipboard, 'writeText', {
      configurable: true,
      value: async text => { writes += 1; exactCopy = text === expected.preview },
    })
    function cancelComposer(event) {
      const link = event.target instanceof Element ? event.target.closest('a[href]') : null
      if (!link || !panel.contains(link)) return
      event.preventDefault()
      event.stopImmediatePropagation()
      const href = link.getAttribute('href')
      if (event.isTrusted && href === expected.email) clicks.email += 1
      else if (event.isTrusted && href === expected.whatsapp) clicks.whatsapp += 1
      else clicks.unexpected += 1
    }
    document.addEventListener('click', cancelComposer, true)
    return {
      evidence: () => ({ writes, exactCopy, ...clicks }),
      dispose() {
        document.removeEventListener('click', cancelComposer, true)
        if (original) Object.defineProperty(clipboard, 'writeText', original)
        else delete clipboard.writeText
      },
    }
  }, expected)
}

/** Requires the v4 harness's authenticated, loopback-only Admin page. Set its app language first for ES. */
export async function verifyShareContact(page, { id, name, email, phone, message }, { language = 'en', mobile = false } = {}) {
  const ui = Object.hasOwn(labels, language) ? labels[language] : null
  check(ui && new URL(page.url()).origin === dashboard, 'SHARE_CONTACT_LOCAL_ADMIN_PAGE_REQUIRED')
  check(/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(id)
    && /^Browser [^\r\n]{1,120}$/.test(name) && /^[\w.+-]+@example\.com$/.test(email)
    && /^\+120255501\d{2}$/.test(phone) && /^Synthetic [^\r\n]{1,180}$/.test(message),
  'SHARE_CONTACT_SYNTHETIC_FIXTURE_REQUIRED')
  const context = page.context()
  let writes = 0
  let externalNavigations = 0
  let newPages = 0
  let boundary
  const observeRequest = request => {
    const origin = new URL(request.url()).origin
    if (origin === api && !['GET', 'HEAD', 'OPTIONS'].includes(request.method())) writes += 1
    if (request.isNavigationRequest() && origin !== dashboard) externalNavigations += 1
  }
  const observePage = () => { newPages += 1 }
  context.on('request', observeRequest)
  context.on('page', observePage)

  try {
    await page.bringToFront()
    await page.waitForFunction(() => document.visibilityState === 'visible', undefined, { timeout })
    const [identity, leads, agents] = await Promise.all([
      expectGet(page, '/auth/me'), expectGet(page, '/admin/leads'), expectGet(page, '/admin/agents'),
      page.goto(`${dashboard}/leads?workspace=admin&openId=${id}`, { waitUntil: 'domcontentloaded', timeout }),
    ])
    check(identity.status() === 200 && leads.status() === 200 && agents.status() === 200,
      'SHARE_CONTACT_REAL_ADMIN_READS_REQUIRED')
    const user = await identity.json()
    check(user.role === 'admin' && user.email === 'browser-admin@example.com', 'SHARE_CONTACT_FIXTURE_ADMIN_REQUIRED')
    const lead = (await leads.json()).find(lead => lead.id === id)
    check(lead?.name === name && lead.email === email && lead.phone === phone && lead.message === message
      && lead.property_id && lead.property_title, 'SHARE_CONTACT_FIXTURE_LEAD_MISMATCH')

    const trigger = page.getByRole('button', { name: ui.trigger, exact: true })
    const dialog = page.getByRole('dialog', { name: ui.title, exact: true })
    const preview = dialog.getByRole('region', { name: ui.preview, exact: true }).locator('pre')
    const nameOption = dialog.getByRole('checkbox', { name: `${ui.name}: ${name}`, exact: true })
    const emailOption = dialog.getByRole('checkbox', { name: `${ui.email}: ${email}`, exact: true })
    const phoneOption = dialog.getByRole('checkbox', { name: `${ui.phone}: ${phone}`, exact: true })
    const permission = dialog.getByRole('checkbox', { name: ui.permission })
    const copy = dialog.getByRole('button', { name: ui.copy, exact: true })
    const emailLink = dialog.getByRole('link', { name: ui.emailDraft, exact: true })
    const whatsappLink = dialog.getByRole('link', { name: ui.whatsappDraft, exact: true })
    const status = dialog.getByRole('status')
    const nameLine = `${ui.name}: ${name}`
    const exactPreview = [nameLine, `${ui.email}: ${email}`, `${ui.phone}: ${phone}`].join('\n')
    const initialOverflow = await page.evaluate(() => document.body.style.overflow)
    await trigger.click()
    await dialog.waitFor({ state: 'visible' })
    const leadPageUrl = page.url()
    check(await nameOption.isChecked() && !await emailOption.isChecked() && !await phoneOption.isChecked()
      && !await permission.isChecked() && await preview.textContent() === nameLine, 'SHARE_CONTACT_DEFAULT_FIELDS')
    await assertComposerGated(copy, emailLink, whatsappLink)

    await emailOption.check()
    await phoneOption.check()
    check(await preview.textContent() === exactPreview, 'SHARE_CONTACT_EXACT_SELECTED_PREVIEW')
    await assertComposerGated(copy, emailLink, whatsappLink)
    await permission.check()
    await emailLink.waitFor({ state: 'visible' })
    await whatsappLink.waitFor({ state: 'visible' })
    check(await copy.isEnabled(), 'SHARE_CONTACT_COPY_UNAVAILABLE')

    const emailHref = await emailLink.getAttribute('href')
    const whatsappHref = await whatsappLink.getAttribute('href')
    check(emailHref?.startsWith('mailto:?') && whatsappHref?.startsWith('https://wa.me/?'),
      'SHARE_CONTACT_UNEXPECTED_COMPOSER')
    const mailto = new URL(emailHref)
    const whatsapp = new URL(whatsappHref)
    check(mailto.protocol === 'mailto:' && mailto.pathname === '' && !mailto.hash && mailto.searchParams.size === 2
      && mailto.searchParams.get('subject') === ui.subject && mailto.searchParams.get('body') === exactPreview,
    'SHARE_CONTACT_EMAIL_DRAFT_CONTENT')
    check(whatsapp.origin === 'https://wa.me' && whatsapp.pathname === '/' && !whatsapp.hash
      && !whatsapp.username && !whatsapp.password && whatsapp.searchParams.size === 1
      && whatsapp.searchParams.get('text') === exactPreview
      && await whatsappLink.getAttribute('target') === '_blank'
      && (await whatsappLink.getAttribute('rel')).split(/\s+/).includes('noopener')
      && (await whatsappLink.getAttribute('rel')).split(/\s+/).includes('noreferrer'), 'SHARE_CONTACT_WHATSAPP_DRAFT_CONTENT')

    boundary = await installBrowserBoundary(dialog, { origin: dashboard, preview: exactPreview, email: emailHref, whatsapp: whatsappHref })
    check(await boundary.evaluate(value => value !== null), 'SHARE_CONTACT_BROWSER_BOUNDARY_REQUIRED')
    await emailLink.click()
    await whatsappLink.click()
    await copy.click()
    await status.waitFor({ state: 'visible' })
    check(await status.textContent() === ui.copied, 'SHARE_CONTACT_COPY_STATUS')
    const evidence = await boundary.evaluate(value => value.evidence())
    check(evidence.email === 1 && evidence.whatsapp === 1 && evidence.unexpected === 0
      && evidence.writes === 1 && evidence.exactCopy, 'SHARE_CONTACT_BROWSER_BOUNDARY_MISMATCH')

    if (mobile) {
      const fits = await copy.evaluate(button => {
        const frame = button.closest('[role="dialog"]').getBoundingClientRect()
        const bounds = button.getBoundingClientRect()
        return innerWidth <= 430 && frame.left >= -1 && frame.right <= innerWidth + 1
          && bounds.left >= frame.left - 1 && bounds.right <= frame.right + 1
          && bounds.top >= frame.top - 1 && bounds.bottom <= frame.bottom + 1
          && document.documentElement.scrollWidth <= innerWidth + 1
      })
      check(fits, 'SHARE_CONTACT_MOBILE_CONTROLS_OVERFLOW')
    }

    await permission.uncheck()
    await assertComposerGated(copy, emailLink, whatsappLink)
    check(await status.count() === 0, 'SHARE_CONTACT_STALE_COPY_STATUS')
    await permission.check()
    await nameOption.uncheck()
    await emailOption.uncheck()
    await phoneOption.uncheck()
    await assertComposerGated(copy, emailLink, whatsappLink)
    check(await preview.textContent() === ui.empty, 'SHARE_CONTACT_EMPTY_PREVIEW')

    await page.keyboard.press('Escape')
    await dialog.waitFor({ state: 'detached' })
    await trigger.waitFor({ state: 'visible' })
    check(await trigger.evaluate(element => document.activeElement === element)
      && await page.evaluate(() => document.body.style.overflow) === initialOverflow, 'SHARE_CONTACT_FOCUS_AND_SCROLL_RESTORE')
    await trigger.click()
    await dialog.waitFor({ state: 'visible' })
    check(await nameOption.isChecked() && !await emailOption.isChecked() && !await phoneOption.isChecked()
      && !await permission.isChecked() && await status.count() === 0 && await preview.textContent() === nameLine,
    'SHARE_CONTACT_REOPEN_STATE')
    await assertComposerGated(copy, emailLink, whatsappLink)
    await dialog.getByRole('button', { name: ui.close, exact: true }).click()
    await dialog.waitFor({ state: 'detached' })
    check(page.url() === leadPageUrl && writes === 0 && externalNavigations === 0 && newPages === 0,
      'SHARE_CONTACT_EXTERNAL_ACTION_OR_API_MUTATION')

    return {
      language, mobile, consent_gate: true, exact_drafts: true, composer_clicks_canceled: true,
      clipboard_scope: 'isolated_browser_writeText_call', focus_restored: true, no_api_mutations: true,
    }
  } finally {
    if (boundary) {
      await boundary.evaluate(value => value?.dispose()).catch(() => {})
      await boundary.dispose()
    }
    context.off('request', observeRequest)
    context.off('page', observePage)
  }
}
