'use client'
import { useNav } from '../hooks/useNav'
import { useState, useRef, useEffect } from 'react'
import { useSearchParams } from 'next/navigation'
import { useTranslation } from 'react-i18next'
import toast from 'react-hot-toast'
import { verify, resendCode } from '../api/auth'
import { currentAuthLocale } from '../lib/authLocale'
import { dashboardAfterAuth, professionalAuthParams, rememberProfessionalJoin } from '../lib/professionalJoin'

const _dashRaw = process.env.NEXT_PUBLIC_DASHBOARD_URL ?? 'https://app.ilovedrrealty.com'
const DASHBOARD_URL = _dashRaw.startsWith('http') ? _dashRaw : `https://${_dashRaw}`

export default function Verify() {
  const go = useNav()
  const params = useSearchParams()

  const [email, setEmail] = useState(params.get('email') ?? '')
  const [editingEmail, setEditingEmail] = useState(false)
  const [emailDraft, setEmailDraft] = useState('')
  const emailInputRef = useRef<HTMLInputElement>(null)

  const codeFromQuery = (params.get('code') ?? '').replace(/\D/g, '').slice(0, 6)
  const [digits, setDigits] = useState(
    codeFromQuery.length === 6 ? codeFromQuery.split('') : ['', '', '', '', '', '']
  )
  const [verifyError, setVerifyError] = useState('')
  const [loading, setLoading] = useState(false)
  const [resendSent, setResendSent] = useState(false)
  const digitRefs = useRef<(HTMLInputElement | null)[]>([])
  const { t, i18n } = useTranslation('verify')

  useEffect(() => {
    rememberProfessionalJoin()
    if (codeFromQuery.length === 6 && params.get('email')) {
      // Auto-submit when arriving from the email link
      handleVerify(codeFromQuery)
    } else {
      digitRefs.current[0]?.focus()
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (editingEmail) emailInputRef.current?.focus()
  }, [editingEmail])

  function startEditEmail() {
    setEmailDraft(email)
    setEditingEmail(true)
    setResendSent(false)
      }

  async function confirmEmailEdit() {
    const trimmed = emailDraft.trim()
    if (!trimmed || !trimmed.includes('@')) return
    setEmail(trimmed)
    setEditingEmail(false)
    setDigits(['', '', '', '', '', ''])
    setVerifyError('')
    setResendSent(false)
        try {
      await resendCode({ email: trimmed })
      setResendSent(true)
    } catch { /* fail silently */ }
    digitRefs.current[0]?.focus()
  }

  function cancelEmailEdit() {
    setEditingEmail(false)
  }

  function handleDigitChange(index: number, value: string) {
    const char = value.replace(/\D/g, '').slice(-1)
    const next = [...digits]
    next[index] = char
    setDigits(next)
    setVerifyError('')
    if (char && index < 5) digitRefs.current[index + 1]?.focus()
  }

  function handleDigitKeyDown(index: number, e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Backspace' && !digits[index] && index > 0) {
      digitRefs.current[index - 1]?.focus()
    }
  }

  function handleDigitPaste(e: React.ClipboardEvent<HTMLInputElement>) {
    const pasted = e.clipboardData.getData('text').replace(/\D/g, '').slice(0, 6)
    if (!pasted) return
    e.preventDefault()
    const next = [...digits]
    pasted.split('').forEach((ch, i) => { next[i] = ch })
    setDigits(next)
    digitRefs.current[Math.min(pasted.length, 5)]?.focus()
  }

  async function handleVerify(overrideCode?: string) {
    const code = overrideCode ?? digits.join('')
    if (code.length < 6) { setVerifyError(t('err_digits')); return }
    if (!email) { setVerifyError(t('err_email')); return }
    setVerifyError('')
    setLoading(true)
    try {
      await verify({ email, code, locale: currentAuthLocale(i18n.language) })
      toast.success(t('toast_success'))
      setTimeout(() => { window.location.href = dashboardAfterAuth(DASHBOARD_URL) }, 1500)
    } catch (e: unknown) {
      setVerifyError(e instanceof Error ? e.message : 'Verification failed')
      setDigits(['', '', '', '', '', ''])
      digitRefs.current[0]?.focus()
    } finally {
      setLoading(false)
    }
  }

  async function handleResend() {
    setResendSent(false)
        try {
      await resendCode({ email })
      setResendSent(true)
    } catch { /* fail silently */ }
  }

  return (
    <div className="min-h-[calc(100vh-74px)] flex items-center justify-center bg-paper2 font-sans px-4">
      <div className="w-full max-w-md">
        <div className="bg-paper border border-line rounded-2xl p-8 text-center">

          <div className="w-14 h-14 rounded-2xl bg-coral/10 grid place-items-center mx-auto mb-5">
            <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#e10f1f" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect width="20" height="16" x="2" y="4" rx="2"/>
              <path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/>
            </svg>
          </div>

          <h1 className="font-sans text-6.5 font-bold text-ink mb-2 tracking-[-.02em]">{t('heading')}</h1>
          <p className="text-[13.5px] text-ink2 leading-[1.6] mb-2">{t('sent_to')}</p>

          {/* Email display / edit */}
          {editingEmail ? (
            <div className="flex items-center gap-2 mb-7">
              <input
                ref={emailInputRef}
                type="email"
                value={emailDraft}
                onChange={e => setEmailDraft(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter') confirmEmailEdit()
                  if (e.key === 'Escape') cancelEmailEdit()
                }}
                className="flex-1 py-2 px-3 rounded-xl border border-coral text-[13.5px] font-sans text-ink outline-none bg-paper text-center"
              />
              <button
                onClick={confirmEmailEdit}
                className="px-3 py-2 rounded-xl bg-coral text-white text-xs font-bold font-sans border-0 cursor-pointer"
              >
                {t('send')}
              </button>
              <button
                onClick={cancelEmailEdit}
                className="px-3 py-2 rounded-xl bg-paper border border-line text-ink2 text-xs font-bold font-sans cursor-pointer"
              >
                {t('cancel')}
              </button>
            </div>
          ) : (
            <div className="flex items-center justify-center gap-2 mb-7">
              <span className="text-[13.5px] font-semibold text-ink">{email || t('email_fallback')}</span>
              <button
                onClick={startEditEmail}
                title="Edit email"
                className="bg-transparent border-0 cursor-pointer p-0 flex items-center gap-1 text-coral font-semibold text-[13px] font-sans hover:underline transition-colors duration-150"
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
                  <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
                </svg>
                {t('wrong_email')}
              </button>
            </div>
          )}

          <div className="flex gap-2.5 justify-center mb-5">
            {digits.map((d, i) => (
              <input
                key={i}
                ref={el => { digitRefs.current[i] = el }}
                type="text"
                inputMode="numeric"
                maxLength={1}
                value={d}
                onChange={e => handleDigitChange(i, e.target.value)}
                onKeyDown={e => handleDigitKeyDown(i, e)}
                onPaste={handleDigitPaste}
                className={`w-11 h-13 text-center text-6 font-bold rounded-xl border font-sans text-ink outline-none transition-colors duration-150 bg-paper ${verifyError ? 'border-coral' : d ? 'border-brand' : 'border-line'}`}
              />
            ))}
          </div>

          {verifyError && <p className="text-xs text-coral mb-4 font-medium">{verifyError}</p>}

          <button
            onClick={() => handleVerify()}
            disabled={loading || digits.join('').length < 6}
            className="w-full py-3.25 rounded-full border-none font-sans text-3.75 font-bold bg-coral text-white cursor-pointer disabled:opacity-60 disabled:cursor-not-allowed transition-colors duration-150 mb-5"
          >
            {loading ? t('verifying') : t('verify_btn')}
          </button>

          <div className="border-t border-line pt-5 flex flex-col gap-2.5">
            <p className="text-xs text-ink2">
              {t('no_code')}{' '}
              {email ? (
                <button
                  onClick={handleResend}
                  className="bg-transparent border-0 cursor-pointer text-sea font-semibold font-sans text-xs p-0"
                >
                  {t('resend')}
                </button>
              ) : (
                <button
                  onClick={() => go('signup', undefined, professionalAuthParams())}
                  className="bg-transparent border-0 cursor-pointer text-sea font-semibold font-sans text-xs p-0"
                >
                  {t('back_signup')}
                </button>
              )}
            </p>
            {resendSent && <p className="text-xs text-brand font-medium">{t('resent')}</p>}
          </div>

        </div>
      </div>
    </div>
  )
}
