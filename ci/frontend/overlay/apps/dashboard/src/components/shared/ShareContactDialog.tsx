import { useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Share2, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { contactComposeLinks, contactPreview, minimalContactSelection, shareableContact, type ShareContact } from '../../lib/contactShare'

export function ContactSharePreview({ text, empty }: { text: string; empty: string }) {
  return <pre className="min-h-24 whitespace-pre-wrap break-words rounded-xl border border-line bg-white p-4 font-sans text-sm text-ink">{text || empty}</pre>
}

function ShareContactDialog({ contact, privateContact, onClose }: { contact: ShareContact; privateContact: boolean; onClose: () => void }) {
  const { t } = useTranslation('contact_share')
  const [selected, setSelected] = useState(minimalContactSelection)
  const [permission, setPermission] = useState(!privateContact)
  const [copyResult, setCopyResult] = useState('')
  const panel = useRef<HTMLDivElement>(null)
  const titleId = useId(), noteId = useId(), previewId = useId()
  const close = useRef(onClose); close.current = onClose
  const safe = shareableContact(contact)
  const preview = contactPreview(safe, selected, { name: t('name'), email: t('email'), phone: t('phone'), listing: '' })
  const links = permission ? contactComposeLinks(preview, t('subject')) : null
  const readyLinks = links
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'; panel.current?.focus()
    // Capture prevents an underlying profile dialog from also handling this dialog's keys.
    const keydown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' && event.key !== 'Tab') return
      event.stopImmediatePropagation()
      if (event.key === 'Escape') { event.preventDefault(); close.current(); return }
      const controls = [...(panel.current?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input:not(:disabled), textarea:not(:disabled)') ?? [])].filter(element => element.getClientRects().length > 0)
      const first = controls[0], last = controls[controls.length - 1]
      if (!first) { event.preventDefault(); panel.current?.focus() }
      else if (!panel.current?.contains(document.activeElement)) { event.preventDefault(); (event.shiftKey ? last : first).focus() }
      else if (event.shiftKey && (document.activeElement === first || document.activeElement === panel.current)) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', keydown, true)
    return () => { document.removeEventListener('keydown', keydown, true); document.body.style.overflow = overflow; previous?.focus() }
  }, [])
  const change = (key: keyof typeof selected, checked: boolean) => { setSelected(current => ({ ...current, [key]: checked })); setCopyResult('') }
  async function copy() {
    if (!readyLinks) return
    try { await navigator.clipboard.writeText(preview); setCopyResult(t('copied')) }
    catch { setCopyResult(t('copy_failed')) }
  }
  return createPortal(<div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/70 p-2 sm:p-6" onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}>
    <div ref={panel} role="dialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={noteId} tabIndex={-1} className="max-h-[94dvh] w-full min-w-0 max-w-xl overflow-y-auto rounded-2xl bg-paper p-4 sm:p-6 outline-none">
      <div className="flex items-start justify-between gap-4"><h2 id={titleId} className="text-xl font-bold text-ink">{t('title')}</h2><button type="button" onClick={onClose} aria-label={t('close')} className="min-h-11 min-w-11 shrink-0 rounded-lg border border-line grid place-items-center"><X size={20} /></button></div>
      <p id={noteId} className="mt-3 text-sm leading-relaxed text-ink2">{t('explanation')}</p>
      <fieldset className="mt-5 space-y-2"><legend className="mb-2 text-sm font-bold text-ink">{t('choose_fields')}</legend>{(['name', 'email', 'phone'] as const).map(key => <label key={key} className="flex min-h-11 items-center gap-3 text-sm text-ink"><input type="checkbox" checked={selected[key]} disabled={!safe[key]} onChange={event => change(key, event.target.checked)} /><span className="min-w-0 break-words">{t(key)}: {safe[key] || t('unavailable')}</span></label>)}</fieldset>
      {privateContact ? <label className="mt-3 flex min-h-11 items-start gap-3 rounded-xl bg-amber-50 p-3 text-sm text-amber-950"><input className="mt-1" type="checkbox" checked={permission} onChange={event => { setPermission(event.target.checked); setCopyResult('') }} />{t('permission')}</label> : null}
      <section className="mt-5" aria-labelledby={previewId}><h3 id={previewId} className="mb-2 text-sm font-bold text-ink">{t('preview')}</h3><ContactSharePreview text={preview} empty={t('empty')} /></section>
      <div className="mt-4 flex flex-wrap gap-3">{readyLinks ? <><a href={readyLinks.email} className="inline-flex min-h-11 items-center rounded-xl bg-sea px-4 text-sm font-bold text-white">{t('compose_email')}</a><a href={readyLinks.whatsapp} target="_blank" rel="noopener noreferrer" className="inline-flex min-h-11 items-center rounded-xl border border-line px-4 text-sm font-bold text-ink">{t('compose_whatsapp')}</a></> : <p className="text-sm text-dim">{t(privateContact && !permission ? 'permission_needed' : 'empty')}</p>}<button type="button" disabled={!readyLinks} onClick={() => void copy()} className="min-h-11 rounded-xl border border-line px-4 text-sm font-semibold text-ink disabled:opacity-50">{t('copy')}</button></div>
      {copyResult ? <p role="status" className="mt-3 text-sm text-ink2">{copyResult}</p> : null}<p className="mt-4 text-xs leading-relaxed text-dim">{t('no_delivery_claim')}</p>
    </div>
  </div>, document.body)
}

export function ShareContactButton({ contact, privateContact = true }: { contact: ShareContact; privateContact?: boolean }) {
  const { t } = useTranslation('contact_share')
  const [open, setOpen] = useState(false)
  return <><button type="button" onClick={() => setOpen(true)} className="inline-flex min-h-11 items-center gap-2 rounded-xl border border-line bg-white px-3 py-2 text-sm font-semibold text-ink"><Share2 size={16} aria-hidden="true" />{t('open')}</button>{open ? <ShareContactDialog contact={contact} privateContact={privateContact} onClose={() => setOpen(false)} /> : null}</>
}
