import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'

import enNav      from './locale/en/nav.json'
import esNav      from './locale/es/nav.json'
import enCommon   from './locale/en/common.json'
import esCommon   from './locale/es/common.json'
import enAdmin    from './locale/en/admin.json'
import esAdmin    from './locale/es/admin.json'
import enBuyer    from './locale/en/buyer.json'
import esBuyer    from './locale/es/buyer.json'
import enOwner    from './locale/en/owner.json'
import esOwner    from './locale/es/owner.json'
import enRealtor  from './locale/en/realtor.json'
import esRealtor  from './locale/es/realtor.json'
import enSettings from './locale/en/settings.json'
import esSettings from './locale/es/settings.json'

import enWebsiteEditor from './locale/en/website_editor.json'
import esWebsiteEditor from './locale/es/website_editor.json'
import enResearchTools from './locale/en/research_tools.json'
import esResearchTools from './locale/es/research_tools.json'
import enProfessionals from './locale/en/professionals.json'
import esProfessionals from './locale/es/professionals.json'
import enToolHubs from './locale/en/tool_hubs.json'
import esToolHubs from './locale/es/tool_hubs.json'
import enLeadDelivery from './locale/en/lead_delivery.json'
import esLeadDelivery from './locale/es/lead_delivery.json'
import enLocationReview from './locale/en/location_review.json'
import esLocationReview from './locale/es/location_review.json'
import enManagement from './locale/en/management.json'
import esManagement from './locale/es/management.json'
import enInquiryFeeds from './locale/en/inquiry_feeds.json'
import esInquiryFeeds from './locale/es/inquiry_feeds.json'
import enContactShare from './locale/en/contact_share.json'
import esContactShare from './locale/es/contact_share.json'
import enPropertyNetwork from './locale/en/property_network.json'
import esPropertyNetwork from './locale/es/property_network.json'
import enCommunications from './locale/en/communications.json'
import esCommunications from './locale/es/communications.json'

const savedLang = (localStorage.getItem('ildr_lang') as 'en' | 'es') ?? 'en'

i18n.use(initReactI18next).init({
  resources: {
    en: {
      nav:      enNav,
      common:   enCommon,
      admin:    enAdmin,
      buyer:    enBuyer,
      owner:    enOwner,
      realtor:  enRealtor,
      settings: enSettings,
      website_editor: enWebsiteEditor,
      research_tools: enResearchTools,
      professionals: enProfessionals,
      tool_hubs: enToolHubs,
      lead_delivery: enLeadDelivery,
      location_review: enLocationReview,
      management: enManagement,
      inquiry_feeds: enInquiryFeeds,
      contact_share: enContactShare,
      property_network: enPropertyNetwork,
      communications: enCommunications,
    },
    es: {
      nav:      esNav,
      common:   esCommon,
      admin:    esAdmin,
      buyer:    esBuyer,
      owner:    esOwner,
      realtor:  esRealtor,
      settings: esSettings,
      website_editor: esWebsiteEditor,
      research_tools: esResearchTools,
      professionals: esProfessionals,
      tool_hubs: esToolHubs,
      lead_delivery: esLeadDelivery,
      location_review: esLocationReview,
      management: esManagement,
      inquiry_feeds: esInquiryFeeds,
      contact_share: esContactShare,
      property_network: esPropertyNetwork,
      communications: esCommunications,
    },
  },
  lng: savedLang,
  fallbackLng: 'en',
  ns: ['nav', 'common', 'admin', 'buyer', 'owner', 'realtor', 'settings', 'website_editor', 'research_tools', 'professionals', 'tool_hubs', 'lead_delivery', 'location_review', 'management', 'inquiry_feeds', 'contact_share', 'property_network', 'communications'],
  defaultNS: 'common',
  interpolation: { escapeValue: false },
})

export default i18n
