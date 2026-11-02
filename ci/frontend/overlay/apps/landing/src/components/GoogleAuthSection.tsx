'use client'
import { useState } from 'react'
import { useGoogleLogin } from '@react-oauth/google'
import { useTranslation } from 'react-i18next'
import toast from 'react-hot-toast'
import { googleAuth } from '../api/auth'
import { GOOGLE_CLIENT_ID } from '../lib/googleAuth'
import { dashboardAfterAuth } from '../lib/professionalJoin'
import { currentAuthLocale } from '../lib/authLocale'

type Props = { mode: 'login' | 'signup' }
const dashboardRaw = process.env.NEXT_PUBLIC_DASHBOARD_URL ?? 'https://app.ilovedrrealty.com'
const DASHBOARD_URL = dashboardRaw.startsWith('http') ? dashboardRaw : `https://${dashboardRaw}`

export default function GoogleAuthSection({ mode }: Props) {
  const { t } = useTranslation('auth')
  if (!GOOGLE_CLIENT_ID) {
    return <p className="mt-4 text-center text-xs leading-relaxed text-dim">{t('google_unavailable')}</p>
  }
  // Keep the OAuth hook in a child that only mounts with a configured provider.
  return <ConfiguredGoogleAuth mode={mode} />
}

function ConfiguredGoogleAuth({ mode }: Props) {
  const { t, i18n } = useTranslation('auth')
  const [loading, setLoading] = useState(false)
  const startGoogleAuth = useGoogleLogin({
    prompt: 'select_account',
    onSuccess: async tokenResponse => {
      setLoading(true)
      try {
        await googleAuth(tokenResponse.access_token, currentAuthLocale(i18n.language))
        toast.success(t(`${mode}.toast.success`))
        setTimeout(() => { window.location.href = dashboardAfterAuth(DASHBOARD_URL) }, 1500)
      } catch (error: unknown) {
        toast.error(error instanceof Error ? error.message : t(`${mode}.toast.google_error`))
      } finally {
        setLoading(false)
      }
    },
    onError: () => toast.error(t(`${mode}.toast.google_error`)),
  })

  return <>
    <div className={`flex items-center gap-3 ${mode === 'login' ? 'my-5.5' : 'mt-4.5 mb-3.5'}`}>
      <div className="flex-1 h-px bg-line" />
      <span className="text-xs text-dim font-medium">{t(`${mode}.form.or`)}</span>
      <div className="flex-1 h-px bg-line" />
    </div>
    <button
      type="button"
      onClick={() => startGoogleAuth()}
      disabled={loading}
      className="w-full py-2.75 rounded-full font-sans text-sm font-semibold cursor-pointer bg-white border border-[#dadce0] text-[#3c4043] flex items-center justify-center gap-3 hover:bg-[#f8f9fa] transition-colors duration-150 disabled:opacity-60 disabled:cursor-not-allowed"
    >
      {loading ? (
        <svg className="animate-spin" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10" strokeOpacity=".25"/><path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round"/></svg>
      ) : (
        <svg width="18" height="18" viewBox="0 0 18 18" xmlns="http://www.w3.org/2000/svg">
          <path d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844c-.209 1.125-.843 2.078-1.796 2.717v2.258h2.908c1.702-1.567 2.684-3.874 2.684-6.615z" fill="#4285F4"/>
          <path d="M9 18c2.43 0 4.467-.806 5.956-2.184l-2.908-2.258c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 0 0 9 18z" fill="#34A853"/>
          <path d="M3.964 10.707A5.41 5.41 0 0 1 3.682 9c0-.593.102-1.17.282-1.707V4.961H.957A8.996 8.996 0 0 0 0 9c0 1.452.348 2.827.957 4.039l3.007-2.332z" fill="#FBBC05"/>
          <path d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 0 0 .957 4.961L3.964 7.293C4.672 5.163 6.656 3.58 9 3.58z" fill="#EA4335"/>
        </svg>
      )}
      {loading ? t(`${mode}.form.google_loading`) : t(`${mode}.form.google`)}
    </button>
  </>
}
