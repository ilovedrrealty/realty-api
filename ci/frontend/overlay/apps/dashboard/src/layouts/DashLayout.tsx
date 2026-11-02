import React, { useState } from 'react'
import {
  LayoutDashboard, Heart, Search, MessageCircle, CalendarDays, Calculator,
  BookOpen, ClipboardList, Home, Bell, DollarSign,
  Building2, GitBranch, Users, Settings, Inbox,
  Key, Shield, LogOut, Menu, X, Lock, ChevronRight, Globe, Upload, type LucideIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import i18n from '../i18n'
import type { Role } from '../App'
import type { UserInfo } from '../lib/auth'
import logo from '../assets/iLoveDRRealty_White.png'
import { TOOL_WORKSPACE_VIEW, type ToolWorkspace } from '../data/toolHubs'
import { landingBaseUrl } from '../lib/siteUrls'
import { ManagementNavigation } from '../components/shared/ManagementNavigation'

const LANDING_URL = landingBaseUrl(import.meta.env.VITE_LANDING_URL)

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
const ROLE_ICON: Record<Role, LucideIcon> = {
  Buyer: Search, Owner: Key, Realtor: Building2, Admin: Shield,
}

const GENERAL_NAV: { Icon: LucideIcon; view: string }[] = [
  { Icon: LayoutDashboard, view: 'home'     },
  { Icon: Users,           view: 'professional-profile' },
  { Icon: BookOpen,        view: 'resources' },
  { Icon: Users,           view: 'professional-directory' },
  { Icon: GitBranch,       view: 'property-network' },
  { Icon: Settings,        view: 'settings' },
]

const NAV: Record<Exclude<Role, 'Admin'>, { Icon: LucideIcon; view: string }[]> = {
  Buyer: [
    { Icon: Heart,         view: 'saved'      },
    { Icon: MessageCircle, view: 'inquiries'  },
    { Icon: CalendarDays,  view: 'bookings'   },
    { Icon: Calculator,    view: 'calculator' },
  ],
  Owner: [
    { Icon: Home,          view: 'listings'        },
    { Icon: ClipboardList, view: 'property-management' },
    { Icon: CalendarDays,  view: 'calendar'        },
    { Icon: Bell,          view: 'owner-bookings'  },
    { Icon: MessageCircle, view: 'leads'           },
    { Icon: DollarSign,    view: 'earnings'        },
  ],
  Realtor: [
    { Icon: Building2,     view: 'listings'          },
    { Icon: ClipboardList, view: 'property-management' },
    { Icon: ClipboardList, view: 'leads'             },
    { Icon: Bell,          view: 'realtor-bookings'  },
    { Icon: GitBranch,     view: 'pipeline'          },
    { Icon: CalendarDays,  view: 'calendar'          },
    { Icon: BookOpen,      view: 'research'          },
  ],
}

const ADMIN_NAV: { Icon: LucideIcon; view: string }[] = [
  { Icon: LayoutDashboard, view: 'home'      },
  { Icon: Users,           view: 'users'     },
  { Icon: Users,           view: 'professionals' },
  { Icon: Users,           view: 'professional-profile' },
  { Icon: Building2,       view: 'listings'  },
  { Icon: Search,          view: 'location-review' },
  { Icon: ClipboardList,   view: 'property-management' },
  { Icon: GitBranch,       view: 'property-network' },
  { Icon: Inbox,           view: 'leads'     },
  { Icon: Inbox,           view: 'lead-delivery' },
  { Icon: Inbox,           view: 'communications' },
  { Icon: LayoutDashboard, view: 'analytics' },
  { Icon: Upload,          view: 'bulk-import' },
  { Icon: Bell,            view: 'bookings'  },
  { Icon: BookOpen,        view: 'resources' },
  { Icon: Search,          view: 'buyer-tools' },
  { Icon: Key,             view: 'owner-tools' },
  { Icon: Building2,       view: 'realtor-tools' },
  { Icon: Users,           view: 'professional-directory' },
  { Icon: BookOpen,        view: 'research' },
  { Icon: Globe,           view: 'website-editor' },
  { Icon: Settings,        view: 'settings'  },
]

interface Props {
  role: Role
  view: string
  go: (v: string, openId?: string, workspace?: Role) => void
  onLogout: () => void
  user: UserInfo
  onWorkspaceChange?: (role: Role) => void
  children: React.ReactNode
}

export default function DashLayout({ role, view, go, onLogout, user, children, onWorkspaceChange }: Props) {
  const { t } = useTranslation('nav')
  const tone = ROLE_TONE[role]
  const ownNavItems = role === 'Admin'
    ? ADMIN_NAV
    : [...GENERAL_NAV, ...NAV[role as Exclude<Role, 'Admin'>]]
  const RoleIcon = ROLE_ICON[role]
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [expandedLocked, setExpandedLocked] = useState<Record<string, boolean>>({})
  const [lang, setLang] = useState<'en' | 'es'>(() =>
    (localStorage.getItem('ildr_lang') as 'en' | 'es') ?? 'en'
  )
  const closeNav = () => setSidebarOpen(false)
  const toggleLocked = (r: string) => setExpandedLocked(prev => ({ ...prev, [r]: !prev[r] }))

  const isSectionLocked = (r: Role) => user.role !== 'admin' && !ROLE_ACCESS[role]?.includes(r)

  function toggleLang() {
    const next = lang === 'en' ? 'es' : 'en'
    setLang(next)
    localStorage.setItem('ildr_lang', next)
    i18n.changeLanguage(next)
  }

  const topbarLabel = t(view as string, { defaultValue: t('home') })

  const navContent = role === 'Admin' ? (
    ADMIN_NAV.map(item => {
      const active = view === item.view
      return (
        <button
          key={item.view}
          onClick={() => { go(item.view); closeNav() }}
          className="flex items-center gap-2.5 w-full px-3 py-1.75 rounded-lg border-0 cursor-pointer text-[13px] text-left transition-all duration-120"
          style={{
            background: active ? `${tone}22` : 'transparent',
            color: active ? tone : 'rgba(255,255,255,.82)',
            fontWeight: active ? 700 : 500,
          }}
        >
          <item.Icon size={14} className="shrink-0" />
          <span className="flex-1">{t(item.view)}</span>
          {active && <div className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: tone }} />}
        </button>
      )
    })
  ) : (
    <>
      {/* General — always accessible */}
      <div className="mb-3">
        <div className="flex items-center gap-1.5 px-3 mb-0.5 mt-1">
          <LayoutDashboard size={10} style={{ color: 'rgba(255,255,255,.55)', flexShrink: 0 }} />
          <span className="text-[10px] font-bold uppercase tracking-widest" style={{ color: 'rgba(255,255,255,.55)' }}>
            {t('general')}
          </span>
        </div>
        {GENERAL_NAV.map(item => {
          const active = view === item.view
          return (
            <button
              key={item.view}
              onClick={() => { go(item.view); closeNav() }}
              className="flex items-center gap-2.5 w-full px-3 py-1.75 rounded-lg border-0 cursor-pointer text-[13px] text-left transition-all duration-120"
              style={{
                background: active ? `${tone}22` : 'transparent',
                color: active ? tone : 'rgba(255,255,255,.82)',
                fontWeight: active ? 700 : 500,
              }}
            >
              <item.Icon size={14} className="shrink-0" />
              <span className="flex-1">{t(item.view)}</span>
              {active && <div className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: tone }} />}
            </button>
          )
        })}
      </div>

      {/* Role sections with lock enforcement */}
      {ROLE_ORDER.map((r) => {
        const sectionLocked = isSectionLocked(r)
        const sectionTone = ROLE_TONE[r]
        const SectionIcon = ROLE_ICON[r]
        const isExpanded = !sectionLocked || !!expandedLocked[r]
        return (
          <div key={r} className="mb-3">
            <button
              onClick={() => { if (sectionLocked) toggleLocked(r); else { go(TOOL_WORKSPACE_VIEW[r as ToolWorkspace], undefined, r); closeNav() } }}
              aria-expanded={sectionLocked ? isExpanded : undefined}
              aria-current={!sectionLocked && view === TOOL_WORKSPACE_VIEW[r as ToolWorkspace] ? 'page' : undefined}
              className="flex items-center gap-1.5 px-3 py-2 rounded-lg mb-0.5 mt-1 w-full border-0 cursor-pointer text-left"
              style={{ cursor: 'pointer', background: !sectionLocked && view === TOOL_WORKSPACE_VIEW[r as ToolWorkspace] ? `${sectionTone}22` : 'transparent' }}
            >
              <SectionIcon size={10} style={{ color: sectionLocked ? 'rgba(255,255,255,.38)' : sectionTone, flexShrink: 0 }} />
              <span className="text-[10px] font-bold uppercase tracking-widest flex-1" style={{ color: sectionLocked ? 'rgba(255,255,255,.38)' : sectionTone }}>
                {t(`workspace_options.${r}`)}
              </span>
              {sectionLocked && (
                <ChevronRight
                  size={11}
                  style={{
                    color: 'rgba(255,255,255,.35)',
                    transform: isExpanded ? 'rotate(90deg)' : 'rotate(0deg)',
                    transition: 'transform 150ms',
                  }}
                />
              )}
            </button>

            {isExpanded && NAV[r as Exclude<Role, 'Admin'>].map(item => {
              const active = !sectionLocked && view === item.view
              return (
                <button
                  key={`${r}-${item.view}`}
                  onClick={() => { if (sectionLocked) { go('home'); closeNav() } else { go(item.view, undefined, r); closeNav() } }}
                  className="flex items-center gap-2.5 w-full px-3 py-1.75 rounded-lg border-0 cursor-pointer text-[13px] text-left transition-all duration-120"
                  style={{
                    background: active ? `${sectionTone}22` : 'transparent',
                    color: active ? sectionTone : sectionLocked ? 'rgba(255,255,255,.38)' : 'rgba(255,255,255,.82)',
                    fontWeight: active ? 700 : 500,
                  }}
                >
                  <item.Icon size={14} className="shrink-0" />
                  <span className="flex-1">{t(item.view)}</span>
                  {sectionLocked
                    ? <Lock size={9} style={{ color: 'rgba(255,255,255,.32)' }} />
                    : active && <div className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: sectionTone }} />
                  }
                </button>
              )
            })}
          </div>
        )
      })}
    </>
  )

  return (
    <div className="flex min-h-screen font-sans">

      {/* ── Mobile backdrop ── */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-30 md:hidden"
          onClick={closeNav}
        />
      )}

      {/* ── Sidebar ── */}
      <aside className={`w-58 shrink-0 bg-nav flex flex-col fixed inset-y-0 left-0 z-40 transition-transform duration-200 md:translate-x-0 ${sidebarOpen ? 'translate-x-0' : '-translate-x-full'}`}>

        {/* Logo */}
        <div className="px-5 pt-5.5 pb-4.5 flex items-center justify-between shrink-0">
          <a href={LANDING_URL} className="block leading-none">
            <img src={logo} alt="I Love DR Realty" className="h-14 w-auto block" />
          </a>
          <button
            onClick={closeNav}
            className="md:hidden border-0 bg-transparent cursor-pointer p-1 text-white/50 hover:text-white/80"
          >
            <X size={18} />
          </button>
        </div>

        {user.role === 'admin' && onWorkspaceChange && (
          <div className="px-3 pb-4 shrink-0">
            <label htmlFor="dashboard-workspace" className="block text-[11px] font-semibold text-white/70 mb-1.5">
              {t('workspace_label')}
            </label>
            <select
              id="dashboard-workspace"
              value={role}
              onChange={event => { onWorkspaceChange(event.target.value as Role); closeNav() }}
              className="w-full rounded-lg border border-white/20 bg-nav px-2.5 py-2 text-[13px] text-white cursor-pointer"
            >
              {(['Admin', 'Buyer', 'Owner', 'Realtor'] as Role[]).map(workspace => (
                <option key={workspace} value={workspace}>{t(`workspace_options.${workspace}`)}</option>
              ))}
            </select>
            <p className="text-[10.5px] text-white/55 leading-relaxed mt-2 mb-0">{t('workspace_identity')}</p>
            {role !== 'Admin' && (
              <button onClick={() => { onWorkspaceChange('Admin'); closeNav() }} className="mt-2 text-[11.5px] text-white underline border-0 bg-transparent cursor-pointer p-0">
                {t('return_admin')}
              </button>
            )}
          </div>
        )}

        {/* Nav — scrollable */}
        <nav className="flex-1 px-2 flex flex-col overflow-y-auto min-h-0 pb-2">
          {navContent}
          <ManagementNavigation accountRole={user.role} workspace={role} onNavigate={closeNav} />
        </nav>

        {/* Bottom — language + user + logout */}
        <div className="px-3 pb-5 pt-3 border-t border-white/8 shrink-0">
          <div className="flex items-center gap-2.5 px-2 py-2 mb-2">
            {user.avatar_url ? (
              <img src={user.avatar_url} alt="" className="w-8 h-8 rounded-full object-cover shrink-0" />
            ) : (
              <div
                className="w-8 h-8 rounded-full grid place-items-center text-white text-[13px] font-bold shrink-0"
                style={{ background: tone }}
              >
                {user.email[0].toUpperCase()}
              </div>
            )}
            <div className="flex-1 min-w-0">
              <div className="text-[12.5px] font-semibold text-white truncate">{user.display_name || user.email.split('@')[0]}</div>
              <div className="text-[10.5px] font-mono truncate" style={{ color: 'rgba(255,255,255,.4)' }}>
                {user.user_code != null ? `#${String(user.user_code).padStart(7, '0')}` : user.email}
              </div>
            </div>
          </div>

          {/* Language toggle */}
          <button
            onClick={toggleLang}
            className="w-full px-3 py-2 rounded-lg border border-white/12 bg-transparent cursor-pointer text-[12.5px] font-medium text-left flex items-center justify-between transition-colors duration-120 hover:bg-white/6 mb-2"
            style={{ color: 'rgba(255,255,255,.65)' }}
          >
            <span className="flex items-center gap-1.5">
              <Globe size={13} />
              {t('lang_toggle')}
            </span>
            <div className="flex items-center gap-0.5 text-[12px] font-bold">
              <span style={{ color: lang === 'en' ? '#fff' : 'rgba(255,255,255,.38)' }}>EN</span>
              <span style={{ color: 'rgba(255,255,255,.25)' }}>/</span>
              <span style={{ color: lang === 'es' ? '#fff' : 'rgba(255,255,255,.38)' }}>ES</span>
            </div>
          </button>

          <button
            onClick={onLogout}
            className="w-full px-3 py-2 rounded-lg border border-white/12 bg-transparent cursor-pointer text-[12.5px] font-medium text-left flex items-center gap-2 transition-colors duration-120 hover:bg-white/6"
            style={{ color: 'rgba(255,255,255,.65)' }}
          >
            <LogOut size={13} />
            {t('logout')}
          </button>
        </div>
      </aside>

      {/* ── Main content ── */}
      <main className="md:ml-58 min-w-0 flex-1 flex flex-col min-h-screen">

        {/* Topbar */}
        <div className="h-15 bg-paper border-b border-line flex items-center px-4 md:px-7 gap-3 sticky top-0 z-30">
          <button
            onClick={() => setSidebarOpen(true)}
            className="md:hidden border-0 bg-transparent cursor-pointer p-1.5 -ml-1 text-ink2"
          >
            <Menu size={20} />
          </button>
          <span className="flex-1 text-[13.5px] font-semibold text-ink2 truncate">
            {topbarLabel}
          </span>
          <div className="flex items-center gap-2.5">
            <div
              className="text-xs px-2.5 py-1 rounded-full font-semibold flex items-center gap-1.5"
              style={{ background: `${tone}15`, color: tone }}
            >
              {user.role === 'admin' ? <Shield size={12} /> : <RoleIcon size={12} />}
              {t(`roles.${user.role === 'admin' ? 'Admin' : role}`)}
            </div>
            {user.avatar_url ? (
              <img src={user.avatar_url} alt="" className="w-8 h-8 rounded-full object-cover" />
            ) : (
              <div
                className="w-8 h-8 rounded-full grid place-items-center text-white text-[13px] font-bold"
                style={{ background: tone }}
              >
                {user.email[0].toUpperCase()}
              </div>
            )}
          </div>
        </div>

        {/* Page content */}
        <div className="min-w-0 flex-1 p-4 pb-10 sm:p-7 bg-[#F8F9FC]">
          {children}
        </div>
      </main>

    </div>
  )
}
