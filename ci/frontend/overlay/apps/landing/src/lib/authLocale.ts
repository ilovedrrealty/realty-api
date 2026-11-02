export type AuthLocale = 'en' | 'es'

function supported(value: string | null | undefined): AuthLocale | undefined {
  const language = value?.toLowerCase().split('-')[0]
  return language === 'en' || language === 'es' ? language : undefined
}

// Verification links can auto-submit before I18nProvider's effect restores the
// saved language. Read that same preference at the action, without writing it.
export function resolveAuthLocale(active: string, saved?: string | null, pathname = ''): AuthLocale {
  if (/^\/es(?:\/|$)/i.test(pathname)) return 'es'
  return supported(saved) ?? supported(active) ?? 'en'
}

export function currentAuthLocale(active: string): AuthLocale {
  if (typeof window === 'undefined') return resolveAuthLocale(active)
  let saved: string | null = null
  try { saved = window.localStorage.getItem('ildr_lang') } catch { /* Storage may be disabled. */ }
  return resolveAuthLocale(active, saved, window.location.pathname)
}
