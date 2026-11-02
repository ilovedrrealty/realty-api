import { ExternalLink } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { managementNavigation } from '../../lib/managementNavigation'

export function ManagementNavigation({ accountRole, workspace, onNavigate }: { accountRole: string; workspace: string; onNavigate?: () => void }) {
  const { t } = useTranslation('nav')
  const href = managementNavigation(accountRole, workspace)
  if (!href) return null
  return <div className="mt-4 border-t border-white/15 pt-3"><a href={href} target="_blank" rel="noopener noreferrer" onClick={event => { if (!window.dispatchEvent(new Event('ildr:before-dashboard-navigation', { cancelable: true }))) event.preventDefault(); else onNavigate?.() }} className="flex min-h-11 items-center gap-2.5 rounded-lg px-3 py-2 text-[13px] font-semibold text-white/85 hover:bg-white/10"><ExternalLink size={15} aria-hidden="true" /><span>{t('management_portal')}</span></a><p className="px-3 text-[11px] leading-relaxed text-white/60">{t('management_navigation_note')}</p></div>
}
