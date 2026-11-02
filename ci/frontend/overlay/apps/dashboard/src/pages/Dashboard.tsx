import { useState } from 'react'
import { Lock } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { Role } from '../App'
import type { UserInfo } from '../lib/auth'
import { BuyerHome } from '../components/dashboard/BuyerHome'
import { OwnerHome } from '../components/dashboard/OwnerHome'
import { RealtorHome } from '../components/dashboard/RealtorHome'
import { LockedView } from '../components/dashboard/LockedView'
import { SetupBanners } from '../components/dashboard/SetupBanners'
import { SavedHomes } from './buyer/SavedHomes'
import { Inquiries } from './buyer/Inquiries'
import { ROICalculator } from './buyer/ROICalculator'
import { Resources } from './buyer/Resources'
import { BuyerBookings } from './buyer/Bookings'
import { OwnerListings } from './owner/Listings'
import { OwnerCalendar } from './owner/Calendar'
import { OwnerBookings } from './owner/Bookings'
import { OwnerLeads } from './owner/Leads'
import { Earnings } from './owner/Earnings'
import { RealtorListings } from './realtor/Listings'
import { SubmitListing } from './realtor/SubmitListing'
import { OwnerSubmitListing } from './owner/SubmitListing'
import { RealtorCalendar } from './realtor/Calendar'
import { RealtorLeads } from './realtor/Leads'
import { Pipeline } from './realtor/Pipeline'
import { RealtorBookings } from './realtor/Bookings'
import { AdminBookings } from './admin/Bookings'
import { UserSettings } from './Settings'
import { Upgrade } from './buyer/Upgrade'
import { ResearchTools } from './shared/ResearchTools'
import { ProfessionalProfile } from './shared/ProfessionalProfile'
import ProfessionalDirectory from './shared/ProfessionalDirectory'
import { ToolHub } from './shared/ToolHub'
import { PropertyManagement } from './shared/PropertyManagement'
import { PropertyNetwork } from './shared/PropertyNetwork'
import { TOOL_WORKSPACE_VIEW, type ToolWorkspace } from '../data/toolHubs'

const ROLE_ORDER: Role[] = ['Buyer', 'Owner', 'Realtor']
const ROLE_ACCESS: Record<string, Role[]> = {
  Buyer:   ['Buyer'],
  Owner:   ['Buyer', 'Owner'],
  Realtor: ['Buyer', 'Realtor'],
  Admin:   ['Buyer', 'Owner', 'Realtor', 'Admin'],
}
const ROLE_TONE: Record<Role, string> = {
  Buyer: '#e10f1f', Owner: '#f0a800', Realtor: '#1f7a3d', Admin: '#0d9488',
}
// Owner/Realtor screens that show the "connect Calendly / PayPal to start adding listings" reminder.
// (Home renders it itself; Settings is where the accounts get connected.)
const SETUP_BANNER_VIEWS = new Set([
  'listings', 'submit-listing', 'calendar', 'owner-bookings', 'realtor-bookings', 'leads', 'earnings', 'pipeline',
])

interface Props {
  go: (v: string, openId?: string, workspace?: Role) => void
  view?: string
  role: Role
  user: UserInfo
  onUserUpdate: (updates: Partial<UserInfo>) => void
  onWorkspaceChange?: (role: Role) => void
}

export default function Dashboard({ go, view = 'home', role, user, onUserUpdate, onWorkspaceChange }: Props) {
  const { t: tBuyer }   = useTranslation('buyer')
  const { t: tOwner }   = useTranslation('owner')
  const { t: tRealtor } = useTranslation('realtor')
  const { t: tNav }     = useTranslation('nav')

  const [activeTab, setActiveTab] = useState<Role>(role)

  const isLocked = (tab: Role) => user.role !== 'admin' && !ROLE_ACCESS[role]?.includes(tab)
  const tone = ROLE_TONE[role]
  const tabTone = ROLE_TONE[activeTab]
  const goToTool = (target: string, workspace?: ToolWorkspace) => go(target, undefined, workspace)

  function getPageTitle(v: string): string {
    if (['resources', 'research', 'professional-profile', 'professional-directory', 'buyer-tools', 'owner-tools', 'realtor-tools', 'property-management', 'property-network'].includes(v)) return tNav(v)
    const key = `pages.${v}`
    if (role === 'Buyer')   return tBuyer(key,   { defaultValue: tBuyer('pages.home') })
    if (role === 'Owner')   return tOwner(key,   { defaultValue: tOwner('pages.home') })
    if (role === 'Realtor') return tRealtor(key, { defaultValue: tRealtor('pages.home') })
    return 'Dashboard'
  }

  function renderView() {
    if (view === 'home') {
      return (
        <div>
          <div className="flex flex-wrap gap-1.5 mb-6 p-1 bg-paper border border-line rounded-xl w-fit" role="group" aria-label={tNav('workspace_label')}>
            {ROLE_ORDER.map((tab: Role) => {
              const isActive = activeTab === tab
              const locked = isLocked(tab)
              const tTone = ROLE_TONE[tab]
              return (
                <button
                  key={tab}
                  onClick={() => user.role === 'admin' && onWorkspaceChange ? onWorkspaceChange(tab) : setActiveTab(tab)}
                  aria-pressed={isActive}
                  className="flex items-center gap-1.5 px-4 py-1.75 rounded-lg text-[12.5px] font-semibold cursor-pointer transition-all duration-150 border-0"
                  style={{
                    background: isActive ? tTone : 'transparent',
                    color: isActive ? '#fff' : locked ? 'rgba(0,0,0,.3)' : 'rgba(0,0,0,.45)',
                  }}
                >
                  {locked && <Lock size={11} />}
                  {tNav(`workspace_options.${tab}`)}
                </button>
              )
            })}
          </div>
          {!isLocked(activeTab) && <ToolHub workspace={activeTab as ToolWorkspace} user={user} go={goToTool} compact />}
          {activeTab === 'Buyer'   && <BuyerHome go={go} />}
          {activeTab === 'Owner'   && (isLocked('Owner')   ? <LockedView tab="Owner"   tone={tabTone} go={go} user={user} /> : <OwnerHome   go={go} tone={tabTone} user={user} />)}
          {activeTab === 'Realtor' && (isLocked('Realtor') ? <LockedView tab="Realtor" tone={tabTone} go={go} user={user} /> : <RealtorHome go={go} tone={tabTone} user={user} />)}
        </div>
      )
    }

    switch (view) {
      // Buyer
      case 'saved':      return <SavedHomes />
      case 'inquiries':  return <Inquiries />
      case 'calculator': return <ROICalculator />
      case 'resources':  return <Resources go={go} />
      case 'buyer-tools': return <ToolHub workspace="Buyer" user={user} go={goToTool} />
      case 'owner-tools': return <ToolHub workspace="Owner" user={user} go={goToTool} />
      case 'realtor-tools': return <ToolHub workspace="Realtor" user={user} go={goToTool} />
      case 'tools': return <ToolHub workspace={role === 'Owner' || role === 'Realtor' ? role : 'Buyer'} user={user} go={goToTool} />
      case 'property-management': return <PropertyManagement user={user} />
      case 'property-network': return <PropertyNetwork user={user} />
      // Owner
      case 'earnings':   return <Earnings tone={tone} go={go} />
      // Realtor
      case 'pipeline':   return <Pipeline user={user} />
      case 'research':   return <ResearchTools user={user} />
      case 'professional-profile': return <ProfessionalProfile />
      case 'professional-directory': return <ProfessionalDirectory />
      // Shared by role
      case 'listings':        return role === 'Owner' ? <OwnerListings tone={tone} go={go} user={user} /> : <RealtorListings tone={tone} go={go} user={user} />
      case 'submit-listing':  return role === 'Owner' ? <OwnerSubmitListing go={go} tone={tone} isAdmin={user.role === 'admin'} /> : <SubmitListing go={go} tone={tone} isAdmin={user.role === 'admin'} />
      case 'calendar':   return role === 'Owner' ? <OwnerCalendar user={user} go={go} /> : <RealtorCalendar user={user} go={go} />
      case 'bookings':        return role === 'Admin' ? <AdminBookings /> : <BuyerBookings />
      case 'owner-bookings':    return <OwnerBookings go={go} />
      case 'realtor-bookings':  return <RealtorBookings />
      case 'leads':      return role === 'Owner' ? <OwnerLeads tone={tone} go={go} isAdmin={user.role === 'admin'} /> : <RealtorLeads go={go} user={user} />
      case 'settings':
      case 'settings:connections':
        return <UserSettings user={user} role={role} tone={tone} onUserUpdate={onUserUpdate} initialTab={view === 'settings:connections' ? 'connections' : undefined} />
      case 'upgrade':    return <Upgrade />
      default: return <section className="rounded-xl border border-line bg-paper p-5"><p className="text-sm text-ink2">{tNav('section_unavailable')}</p><button type="button" onClick={() => go(TOOL_WORKSPACE_VIEW[role === 'Owner' || role === 'Realtor' ? role : 'Buyer'])} className="text-sm text-sea font-semibold underline mt-3">{tNav('open_tools')}</button></section>
    }
  }

  return (
    <div>
      <div className="mb-6">
        <div className="text-[11px] font-bold tracking-[.14em] uppercase mb-1.5" style={{ color: tone }}>
          {tNav('portal_eyebrow', { role: tNav(`roles.${role}`), name: user.display_name })}
        </div>
        <h1 className="font-sans text-[22px] sm:text-[28px] font-extrabold text-ink tracking-[-0.02em]">
          {getPageTitle(view)}
        </h1>
      </div>
      {(role === 'Owner' || role === 'Realtor') && SETUP_BANNER_VIEWS.has(view) && (
        <SetupBanners
          kind={role === 'Owner' ? 'owner' : 'realtor'}
          user={user}
          go={go}
          hideCalendly={view === 'calendar'}   // the Calendar page already shows its own full-page calendar prompt
        />
      )}
      {renderView()}
    </div>
  )
}
