'use client'
import Link from 'next/link'
import { useRef, useState, type ChangeEvent, type FormEvent } from 'react'
import { PhoneInput } from 'react-international-phone'
import 'react-international-phone/style.css'
import { submitContactLead, type LeadSubmissionAttempt } from '../api/inquiries'
import { trackWhatsAppConversion } from '../lib/gtag'
import { isValidPhoneNumber } from 'libphonenumber-js'

const WHATSAPP_URL = 'https://wa.me/18096108094?text=' + encodeURIComponent(
  'Hola I Love DR Realty, quisiera orientación sobre bienes raíces en República Dominicana.',
)

const INTENTS = ['Comprar', 'Vender', 'Invertir'] as const
type Intent = (typeof INTENTS)[number]

const BUDGET_OPTIONS = [
  'Menos de US$150,000',
  'US$150,000–US$300,000',
  'US$300,000–US$500,000',
  'US$500,000–US$1,000,000',
  'Más de US$1,000,000',
]

const TIMELINE_OPTIONS = ['0–3 meses', '3–6 meses', '6–12 meses', 'Más adelante']

const STEPS = [
  { title: 'Te escuchamos', body: 'Conocemos tus objetivos, zona de interés, presupuesto y plazo antes de recomendar un siguiente paso.' },
  { title: 'Aclaramos opciones', body: 'Te explicamos diferencias, posibles costos y preguntas importantes en un lenguaje fácil de entender.' },
  { title: 'Identificamos riesgos', body: 'Señalamos asuntos que merecen atención y cuándo conviene consultar a un profesional independiente.' },
  { title: 'Coordinamos', body: 'Te acompañamos en la búsqueda, presentación, negociación y próximos pasos con comunicación clara.' },
]

const FAQS = [
  { q: '¿Trabajan solamente en Cabarete y Puerto Plata?', a: 'No. Nuestra base está en Hotel Kaoba, Cabarete, pero atendemos clientes y oportunidades en toda la República Dominicana.' },
  { q: '¿Puedo comunicarme si vivo fuera del país?', a: 'Sí. Trabajamos con clientes internacionales y podemos comenzar la conversación por teléfono, WhatsApp o videollamada.' },
  { q: '¿Ayudan tanto a compradores como a vendedores?', a: 'Sí. Orientamos a personas que desean comprar, vender o evaluar una oportunidad inmobiliaria.' },
  { q: '¿Ofrecen asesoría legal, fiscal o financiera?', a: 'Nuestra orientación es inmobiliaria y no sustituye la opinión de un abogado, contador, inspector o asesor financiero. Te indicamos cuándo un asunto merece revisión por un profesional cualificado de tu elección.' },
]

function intentToLeadType(intent: Intent): 'buyer' | 'seller' {
  return intent === 'Vender' ? 'seller' : 'buyer'
}

export default function SpanishLandingPage() {
  const formRef = useRef<HTMLFormElement>(null)
  const submitting = useRef(false)
  const attempt = useRef<LeadSubmissionAttempt>({})
  const [dialCode, setDialCode] = useState('1809')

  const [form, setForm] = useState({
    name: '', phone: '', email: '', interest: '' as Intent | '',
    location: '', budget: '', timeline: '', contactMethod: 'WhatsApp', message: '',
  })
  const [consent, setConsent] = useState(false)
  const [sent, setSent] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function pickIntent(intent: Intent) {
    setForm(f => ({ ...f, interest: intent }))
    formRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  const set = (k: keyof typeof form) => (e: ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
    setForm(f => ({ ...f, [k]: e.target.value }))

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (!consent || submitting.current || !form.interest) return
    const digits = form.phone.replace(/\D/g, '')
    const phone = !digits || digits === dialCode ? undefined : form.phone
    if (!form.name.trim() || (!phone && !form.email.trim()) || (phone && !isValidPhoneNumber(phone))) {
      setError('Escribe tu nombre y un teléfono o correo válido.')
      return
    }
    submitting.current = true
    setLoading(true)
    setError(null)
    try {
      const parts = [
        `Zona de interés: ${form.location || 'Por definir'}`,
        `Presupuesto: ${form.budget || 'Por definir'}`,
        `Plazo: ${form.timeline || 'Por definir'}`,
        `Contacto preferido: ${form.contactMethod}`,
        form.message,
      ].filter(Boolean)

      await submitContactLead({
        tab: intentToLeadType(form.interest),
        name: form.name,
        email: form.email,
        phone,
        interest: form.interest,
        message: parts.join('\n'),
        form: 'spanish_landing', locale: 'es', contactConsent: consent, attempt: attempt.current,
        conversionIntent: form.interest === 'Comprar' || form.interest === 'Invertir' ? 'buy' : form.interest === 'Vender' ? 'sell' : 'general',
      })
      setSent(true)
    } catch {
      setError('Algo salió mal. Por favor intenta de nuevo.')
    } finally {
      submitting.current = false
      setLoading(false)
    }
  }

  const inputCls = 'w-full min-h-12.5 border border-line rounded-[11px] px-3.5 py-3 text-ink bg-white outline-none focus:border-coral focus:ring-3 focus:ring-coral/15 text-sm'
  const labelCls = 'text-[13.5px] font-bold text-ink2 block mb-1.5'

  return (
    <div className="font-sans text-ink bg-white pb-17 sm:pb-0">

      {/* Topbar */}
      <div className="bg-ink text-white/80 text-[13.5px]">
        <div className="max-w-290 mx-auto px-4 sm:px-6 min-h-9.5 flex items-center justify-center sm:justify-between gap-5 text-center">
          <span><strong className="text-white font-semibold">Hotel Kaoba · Cabarete–Puerto Plata</strong> · Servicio en toda República Dominicana</span>
          <span className="hidden sm:inline"><a href="tel:+18096108094" className="hover:text-white">+1 (809) 610-8094</a> · Atención 24/7</span>
        </div>
      </div>

      {/* Header */}
      <header className="h-17.5 sm:h-19.5 bg-white/97 backdrop-blur-md border-b border-ink/8 sticky top-0 z-50">
        <div className="max-w-290 mx-auto px-4 sm:px-6 h-full flex items-center justify-between gap-6">
          <Link href="/" aria-label="I Love DR Realty" className="inline-flex items-center">
            <img src="/iLoveDRRealty_Dark.png" alt="I Love DR Realty" className="w-31.5 sm:w-37.5 h-auto object-contain" />
          </Link>
          <nav aria-label="Navegación principal" className="flex items-center gap-6">
            <a href="#servicios" className="hidden md:inline text-[14.5px] font-semibold text-ink2 hover:text-coral no-underline">Servicios</a>
            <a href="#proceso" className="hidden md:inline text-[14.5px] font-semibold text-ink2 hover:text-coral no-underline">Nuestro enfoque</a>
            <a href="#preguntas" className="hidden md:inline text-[14.5px] font-semibold text-ink2 hover:text-coral no-underline">Preguntas</a>
            <a href="#contacto" className="hidden sm:inline-flex items-center gap-2 min-h-12 rounded-full bg-coral text-white font-bold px-5.5 shadow-[0_12px_30px_rgba(225,15,31,.25)] no-underline hover:bg-coral-deep transition-colors">
              Recibir orientación <span aria-hidden="true">→</span>
            </a>
          </nav>
        </div>
      </header>

      <main>
        {/* Hero */}
        <section
          className="relative overflow-hidden text-white"
          style={{
            backgroundImage: 'linear-gradient(90deg, rgba(0,16,46,.96) 0%, rgba(0,16,46,.88) 43%, rgba(0,16,46,.45) 72%, rgba(0,16,46,.58) 100%), url(https://images.unsplash.com/photo-1564013799919-ab600027ffc6?w=1920&q=86&auto=format&fit=crop)',
            backgroundSize: 'cover',
            backgroundPosition: 'center',
          }}
        >
          <div className="max-w-290 mx-auto px-4 sm:px-6 grid grid-cols-1 lg:grid-cols-[1.08fr_.74fr] gap-9 lg:gap-17.5 items-center py-14.5 sm:py-18">
            <div>
              <span className="inline-flex items-center gap-2.25 text-gold text-[13px] tracking-[.17em] uppercase font-extrabold before:content-[''] before:w-7.5 before:h-0.5 before:bg-gold">
                Entiende · Compara · Decide
              </span>
              <h1 className="max-w-190 mt-5 text-[clamp(2.2rem,7vw,4.5rem)] font-extrabold leading-[1.08] tracking-[-.03em]">
                Compra o vende con confianza en República Dominicana.
              </h1>
              <p className="max-w-167 mt-6 text-white/82 text-[17px] sm:text-[18.5px] leading-[1.65]">
                Te ayudamos a entender tus opciones, comparar oportunidades e identificar posibles riesgos antes de decidir. Desde Cabarete–Puerto Plata, atendemos clientes en toda la República Dominicana y en el exterior.
              </p>
              <div className="flex flex-wrap gap-3.5 mt-8">
                <a href="#contacto" className="inline-flex items-center justify-center gap-2 min-h-12 rounded-full bg-coral text-white font-bold px-6.5 shadow-[0_12px_30px_rgba(225,15,31,.25)] no-underline hover:bg-coral-deep transition-colors w-full sm:w-auto">
                  Recibir orientación <span aria-hidden="true">→</span>
                </a>
                <a
                  href={WHATSAPP_URL}
                  target="_blank"
                  rel="noopener"
                  onClick={trackWhatsAppConversion}
                  className="inline-flex items-center justify-center gap-2 min-h-12 rounded-full bg-white/10 text-white border border-white/28 backdrop-blur-sm font-bold px-6.5 no-underline hover:bg-white/15 transition-colors w-full sm:w-auto"
                >
                  Hablar por WhatsApp
                </a>
              </div>
              <div className="flex flex-wrap gap-x-6 gap-y-3 mt-8 text-white/75 text-[14.5px]">
                {['Orientación honesta', 'Comunicación clara', 'Atención personal'].map(item => (
                  <span key={item} className="inline-flex items-center gap-2">
                    <span aria-hidden="true" className="w-5 h-5 rounded-full bg-gold text-ink grid place-items-center text-[11px] font-black">✓</span>
                    {item}
                  </span>
                ))}
              </div>
            </div>

            <aside aria-label="Selecciona tu objetivo" className="p-7 border border-white/23 rounded-3xl bg-white/96 text-ink shadow-[0_24px_60px_rgba(4,18,43,.16)]">
              <span className="text-coral text-[13px] font-extrabold uppercase tracking-[.11em]">Comienza aquí</span>
              <h2 className="text-[1.7rem] font-extrabold mt-2 mb-3 tracking-[-.02em]">¿Qué quieres lograr?</h2>
              <p className="text-dim text-[14.5px] mb-5">Selecciona una opción y te llevaremos al formulario con tu interés ya marcado.</p>
              <div className="grid gap-2.5">
                {[
                  { intent: 'Comprar' as Intent, label: 'Comprar una propiedad', note: 'Hogar, segunda residencia o terreno' },
                  { intent: 'Vender' as Intent, label: 'Vender una propiedad', note: 'Plan claro y alcance internacional' },
                  { intent: 'Invertir' as Intent, label: 'Evaluar una inversión', note: 'Objetivos, costos y contexto' },
                ].map(opt => (
                  <button
                    key={opt.intent}
                    type="button"
                    onClick={() => pickIntent(opt.intent)}
                    className="w-full min-h-14.5 px-4 py-3.5 border border-line rounded-2xl bg-white flex items-center justify-between gap-3 font-bold text-ink hover:border-coral/55 hover:translate-x-0.5 transition-all text-left cursor-pointer"
                  >
                    <span>
                      {opt.label}
                      <small className="block text-dim font-medium text-[12.5px] mt-0.5">{opt.note}</small>
                    </span>
                    <span aria-hidden="true" className="text-coral text-lg">→</span>
                  </button>
                ))}
              </div>
              <div className="flex items-center gap-2 mt-4.5 text-brand text-[13px] font-bold">
                <span aria-hidden="true">●</span> Conversación inicial sin presión
              </div>
            </aside>
          </div>
        </section>

        {/* Intro */}
        <section className="py-16 sm:py-24">
          <div className="max-w-290 mx-auto px-4 sm:px-6 grid grid-cols-1 lg:grid-cols-[.88fr_1.12fr] gap-10 lg:gap-20 items-center">
            <div className="relative min-h-90 lg:min-h-135">
              <div
                className="absolute inset-0 right-17.5 bottom-11 rounded-[6px_70px_6px_6px] shadow-[0_24px_60px_rgba(4,18,43,.16)] bg-cover bg-center"
                style={{ backgroundImage: 'url(https://pznmfxejanwxtsopxxrp.supabase.co/storage/v1/object/public/listing-images/c1fe5e8c-1471-40cf-adc2-ef990f2fa806/a00f46fc-bd43-46b9-b9f0-a29d725638b0.jpg)' }}
              />
              <div
                className="absolute w-40 sm:w-55 h-36 sm:h-52.5 right-0 bottom-0 border-8 border-white rounded-[30px_8px_30px_8px] shadow-[0_24px_60px_rgba(4,18,43,.16)] bg-cover bg-center"
                style={{ backgroundImage: 'url(https://images.unsplash.com/photo-1507525428034-b723cf961d3e?w=700&q=82&auto=format&fit=crop)' }}
              />
              <div className="absolute left-5.5 bottom-4 z-3 px-4 py-3 rounded-xl bg-ink text-white text-[14px] shadow-[0_12px_30px_rgba(4,18,43,.25)]">
                <strong className="block text-gold text-[11px] tracking-[.1em] uppercase">Nuestra base</strong>
                Hotel Kaoba · Cabarete
              </div>
            </div>
            <div>
              <span className="text-sea text-[13px] font-extrabold uppercase tracking-[.17em]">Educación antes de la decisión</span>
              <h2 className="text-[clamp(2rem,4vw,3.2rem)] font-extrabold text-ink mt-3 tracking-[-.03em] leading-[1.1]">Antes de avanzar, entiende cada paso.</h2>
              <p className="text-dim text-[16.5px] leading-[1.65] my-5.5">
                Comprar, vender o invertir en bienes raíces puede parecer complicado. Nuestro enfoque comienza con educación: te explicamos el proceso, las opciones, los posibles costos y las preguntas que conviene hacer.
              </p>
              <p className="text-dim text-[16.5px] leading-[1.65] mb-5.5">
                Después, te ayudamos a comparar alternativas y coordinar los próximos pasos para que puedas tomar decisiones informadas, con mayor claridad y sin presión.
              </p>
              <div className="mt-7 pl-6.5 py-6 border-l-4 border-coral rounded-r-2xl bg-paper2 text-ink2">
                <strong className="text-ink">Nuestro propósito:</strong> ayudarte a proteger tus intereses señalando posibles riesgos y recomendando revisiones profesionales cuando sean necesarias.
              </div>
            </div>
          </div>
        </section>

        {/* Servicios / intents */}
        <section id="servicios" className="py-16 sm:py-24 bg-paper2">
          <div className="max-w-290 mx-auto px-4 sm:px-6">
            <div className="max-w-190 mb-11">
              <span className="text-sea text-[13px] font-extrabold uppercase tracking-[.17em]">Una conversación para cada objetivo</span>
              <h2 className="text-[clamp(2rem,4vw,3.2rem)] font-extrabold text-ink mt-3.5 tracking-[-.03em] leading-[1.1]">Tu próximo paso comienza con una meta clara.</h2>
              <p className="text-dim text-[16.5px] leading-[1.65] mt-4.5">Elige el camino que mejor describe lo que deseas lograr. Adaptaremos la conversación a tu situación, zona, presupuesto y plazo.</p>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
              {[
                { num: '01', intent: 'Comprar' as Intent, title: 'Comprar una propiedad', body: 'Define prioridades, compara opciones y entiende documentos, costos y próximos pasos antes de comprometerte.', cta: 'Quiero comprar', img: 'https://images.unsplash.com/photo-1560518883-ce09059eeffa?w=900&q=82&auto=format&fit=crop' },
                { num: '02', intent: 'Vender' as Intent, title: 'Vender una propiedad', body: 'Prepara, presenta y promociona tu propiedad con un plan para llegar a compradores locales e internacionales.', cta: 'Quiero vender', img: 'https://images.unsplash.com/photo-1486325212027-8081e485255e?w=900&q=82&auto=format&fit=crop' },
                { num: '03', intent: 'Invertir' as Intent, title: 'Evaluar una inversión', body: 'Compara ubicación, costos, uso previsto y objetivos para evaluar oportunidades con mayor contexto.', cta: 'Quiero invertir', img: 'https://images.unsplash.com/photo-1560185127-6ed189bf02f4?w=900&q=82&auto=format&fit=crop', span: true },
              ].map(card => (
                <article
                  key={card.num}
                  className={`relative min-h-92.5 p-7 rounded-[22px] text-white flex flex-col justify-end shadow-[0_20px_44px_rgba(4,18,43,.15)] bg-cover bg-center overflow-hidden ${card.span ? 'sm:col-span-2 lg:col-span-1' : ''}`}
                  style={{ backgroundImage: `linear-gradient(180deg, rgba(4,18,43,.08), rgba(4,18,43,.92) 82%), url(${card.img})` }}
                >
                  <span className="absolute right-6 top-5 text-white/40 text-4xl font-extrabold">{card.num}</span>
                  <h3 className="text-[1.7rem] font-extrabold tracking-[-.02em]">{card.title}</h3>
                  <p className="text-white/74 my-3">{card.body}</p>
                  <button type="button" onClick={() => pickIntent(card.intent)} className="text-gold font-extrabold no-underline bg-transparent border-none p-0 text-left cursor-pointer w-fit">
                    {card.cta} <span aria-hidden="true">→</span>
                  </button>
                </article>
              ))}
            </div>
          </div>
        </section>

        {/* Proceso */}
        <section id="proceso" className="py-16 sm:py-24">
          <div className="max-w-290 mx-auto px-4 sm:px-6">
            <div className="max-w-190 mb-11">
              <span className="text-sea text-[13px] font-extrabold uppercase tracking-[.17em]">Así te acompañamos</span>
              <h2 className="text-[clamp(2rem,4vw,3.2rem)] font-extrabold text-ink mt-3.5 tracking-[-.03em] leading-[1.1]">Claridad en cada etapa.</h2>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3.5">
              {STEPS.map((step, i) => (
                <article key={step.title} className="p-7 border border-line rounded-2xl bg-white">
                  <span className="inline-grid place-items-center w-11.5 h-11.5 rounded-full text-coral bg-coral/8 text-[13.5px] font-extrabold">{String(i + 1).padStart(2, '0')}</span>
                  <h3 className="mt-5.5 mb-2.5 text-ink text-[1.2rem] font-extrabold tracking-[-.015em]">{step.title}</h3>
                  <p className="text-dim text-[14.5px]">{step.body}</p>
                </article>
              ))}
            </div>
          </div>
        </section>

        {/* Local coverage */}
        <section className="py-16 sm:py-24 bg-ink text-white relative overflow-hidden">
          <div className="max-w-290 mx-auto px-4 sm:px-6 grid grid-cols-1 lg:grid-cols-2 gap-10 lg:gap-20 items-center relative z-2">
            <div>
              <span className="text-gold text-[13px] font-extrabold uppercase tracking-[.17em]">Conocimiento local, alcance amplio</span>
              <h2 className="text-[clamp(2rem,4vw,3.4rem)] font-extrabold mt-3.5 tracking-[-.03em] leading-[1.1]">Desde la costa norte hacia toda la República Dominicana.</h2>
              <p className="text-white/72 text-[16.5px] leading-[1.65] mt-5">Estamos ubicados en Hotel Kaoba, Cabarete, en la provincia de Puerto Plata. Atendemos oportunidades en todo el país y trabajamos con clientes que viven dentro y fuera de República Dominicana.</p>
              <p className="text-white/72 text-[16.5px] leading-[1.65] mt-4">Podemos comenzar a distancia y organizar el proceso según tu ubicación, objetivos y disponibilidad.</p>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {[
                { title: 'Cabarete', body: 'Base local y conocimiento de la costa norte.' },
                { title: 'Puerto Plata', body: 'Propiedades urbanas, residenciales y de inversión.' },
                { title: 'Toda República Dominicana', body: 'Apoyo según la zona y el tipo de oportunidad.' },
                { title: 'Clientes internacionales', body: 'Conversaciones y coordinación a distancia.' },
              ].map(item => (
                <div key={item.title} className="p-4.5 border border-white/14 rounded-2xl bg-white/5">
                  <strong className="block text-gold mb-1">{item.title}</strong>
                  <span className="text-white/72 text-[14.5px]">{item.body}</span>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* Contact form */}
        <section id="contacto" ref={formRef} className="py-16 sm:py-24 bg-paper2">
          <div className="max-w-290 mx-auto px-4 sm:px-6">
            <div className="grid grid-cols-1 lg:grid-cols-[.65fr_1.35fr] rounded-[28px] overflow-hidden shadow-[0_24px_60px_rgba(4,18,43,.16)] bg-white">
              <aside className="p-8 sm:p-11 bg-ink text-white">
                <span className="text-gold text-[13px] font-extrabold uppercase tracking-[.17em]">Hablemos</span>
                <h2 className="text-[2.1rem] font-extrabold mt-3 tracking-[-.02em]">Cuéntanos qué quieres lograr.</h2>
                <p className="text-white/70 mt-3">Comparte algunos detalles y un miembro de nuestro equipo se pondrá en contacto contigo.</p>
                <ul className="list-none p-0 mt-8 grid gap-4.5">
                  <li>
                    <small className="block text-white/54 text-[11px] uppercase tracking-[.1em]">Teléfono</small>
                    <a href="tel:+18096108094" className="text-white text-[15.5px] font-bold no-underline">+1 (809) 610-8094</a>
                  </li>
                  <li>
                    <small className="block text-white/54 text-[11px] uppercase tracking-[.1em]">WhatsApp</small>
                    <a href={WHATSAPP_URL} target="_blank" rel="noopener" onClick={trackWhatsAppConversion} className="text-white text-[15.5px] font-bold no-underline">Escríbenos por WhatsApp</a>
                  </li>
                  <li>
                    <small className="block text-white/54 text-[11px] uppercase tracking-[.1em]">Ubicación</small>
                    <strong className="text-white text-[15.5px] font-bold">Hotel Kaoba, Cabarete, Puerto Plata, RD</strong>
                  </li>
                  <li>
                    <small className="block text-white/54 text-[11px] uppercase tracking-[.1em]">Horario</small>
                    <strong className="text-white text-[15.5px] font-bold">Atención 24 horas, 7 días</strong>
                  </li>
                </ul>
                <p className="mt-8 pt-5 border-t border-white/13 text-white/60 text-[12.5px] leading-[1.6]">
                  La orientación de I Love DR Realty es inmobiliaria y no sustituye asesoría legal, fiscal, financiera ni técnica. Toda inversión implica riesgos.
                </p>
              </aside>

              <div className="p-6 sm:p-11">
                {sent ? (
                  <div className="text-center py-14 px-5">
                    <div className="text-5xl mb-4">✅</div>
                    <h3 className="text-2xl font-extrabold text-ink mb-2.5">Solicitud recibida</h3>
                    <p className="text-dim text-[14.5px] leading-[1.6]">Gracias por contactarnos. Un miembro de nuestro equipo revisará tu solicitud y se comunicará contigo pronto.</p>
                  </div>
                ) : (
                  <form onSubmit={handleSubmit}>
                    <h3 className="text-2xl font-extrabold text-ink mb-1.5">Recibir orientación</h3>
                    <p className="text-dim text-[14.5px] mb-6.5">Los campos con * son necesarios para preparar tu solicitud.</p>

                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4.5">
                      <div className="sm:col-span-2">
                        <label className={labelCls} htmlFor="name">Nombre completo *</label>
                        <input id="name" required autoComplete="name" value={form.name} onChange={set('name')} className={inputCls} />
                      </div>
                      <div>
                        <label className={labelCls} htmlFor="phone">WhatsApp o teléfono (preferido)</label>
                        <PhoneInput
                          defaultCountry="do"
                          value={form.phone}
                          onChange={(phone, metadata) => { setDialCode(metadata.country.dialCode); setForm(f => ({ ...f, phone })) }}
                          inputProps={{ id: 'phone', autoComplete: 'tel', maxLength: 50 }}
                          inputStyle={{ flex: 1, width: '100%', minHeight: '50px', border: '1px solid #e4ddcf', borderLeft: 'none', borderRadius: '0 0.6875rem 0.6875rem 0', fontFamily: 'inherit', fontSize: '14px', color: '#00102e', outline: 'none' }}
                          countrySelectorStyleProps={{ buttonStyle: { border: '1px solid #e4ddcf', borderRight: 'none', borderRadius: '0.6875rem 0 0 0.6875rem', background: '#f3f1ea', height: '100%' } }}
                          style={{ width: '100%', display: 'flex' }}
                        />
                      </div>
                      <div>
                        <label className={labelCls} htmlFor="email">Correo electrónico</label>
                        <input id="email" type="email" maxLength={254} autoComplete="email" value={form.email} onChange={set('email')} className={inputCls} />
                        <p className="mt-2 text-xs text-dim">Comparte teléfono o correo; si puedes, ambos.</p>
                      </div>
                      <div>
                        <label className={labelCls} htmlFor="interest">¿Qué deseas hacer? *</label>
                        <select id="interest" required value={form.interest} onChange={set('interest')} className={`${inputCls} cursor-pointer`}>
                          <option value="">Selecciona una opción</option>
                          {INTENTS.map(i => <option key={i} value={i}>{i}</option>)}
                          <option value="Aún estoy explorando">Aún estoy explorando</option>
                        </select>
                      </div>
                      <div>
                        <label className={labelCls} htmlFor="location">Zona de interés</label>
                        <input id="location" placeholder="Ej. Cabarete, Puerto Plata" value={form.location} onChange={set('location')} className={inputCls} />
                      </div>
                      <div>
                        <label className={labelCls} htmlFor="budget">Rango de precio</label>
                        <select id="budget" value={form.budget} onChange={set('budget')} className={`${inputCls} cursor-pointer`}>
                          <option value="">Prefiero conversarlo</option>
                          {BUDGET_OPTIONS.map(b => <option key={b} value={b}>{b}</option>)}
                        </select>
                      </div>
                      <div>
                        <label className={labelCls} htmlFor="timeline">¿Cuándo deseas avanzar?</label>
                        <select id="timeline" value={form.timeline} onChange={set('timeline')} className={`${inputCls} cursor-pointer`}>
                          <option value="">Selecciona un plazo</option>
                          {TIMELINE_OPTIONS.map(t => <option key={t} value={t}>{t}</option>)}
                        </select>
                      </div>
                      <div className="sm:col-span-2">
                        <label className={labelCls} htmlFor="message">Mensaje adicional</label>
                        <textarea id="message" rows={4} placeholder="Cuéntanos qué tipo de propiedad o ayuda necesitas." value={form.message} onChange={set('message')} className={`${inputCls} resize-y`} />
                      </div>
                    </div>

                    <label className="flex items-start gap-2.5 my-5 text-dim text-[13px] leading-[1.55] cursor-pointer">
                      <input type="checkbox" required checked={consent} onChange={e => setConsent(e.target.checked)} className="w-4.5 h-4.5 mt-0.5 accent-coral shrink-0" />
                      <span>
                        He leído la <Link href="/privacy-policy/" target="_blank" className="text-ink font-bold">Política de Privacidad</Link> y autorizo a I Love DR Realty a contactarme sobre esta solicitud.
                      </span>
                    </label>

                    {error && <p className="text-coral text-[13px] mb-3">{error}</p>}

                    <div className="flex flex-wrap items-center gap-4">
                      <button
                        type="submit"
                        disabled={!consent || loading}
                        className={`inline-flex items-center justify-center gap-2 min-h-12.5 rounded-full font-extrabold px-7 border-none transition-colors ${consent && !loading ? 'bg-coral text-white cursor-pointer hover:bg-coral-deep' : 'bg-line-soft text-dim cursor-not-allowed'}`}
                      >
                        {loading ? 'Enviando…' : <>Enviar solicitud <span aria-hidden="true">→</span></>}
                      </button>
                      <span className="text-dim text-[12.5px]">Sin presión. Revisaremos tu solicitud y te contactaremos.</span>
                    </div>
                  </form>
                )}
              </div>
            </div>
          </div>
        </section>

        {/* FAQ */}
        <section id="preguntas" className="py-16 sm:py-24">
          <div className="max-w-290 mx-auto px-4 sm:px-6 grid grid-cols-1 lg:grid-cols-[.7fr_1.3fr] gap-10 lg:gap-17.5">
            <div>
              <span className="text-sea text-[13px] font-extrabold uppercase tracking-[.17em]">Preguntas frecuentes</span>
              <h2 className="text-[clamp(2rem,4vw,3.1rem)] font-extrabold text-ink mt-3.5 tracking-[-.03em] leading-[1.1]">Respuestas claras antes de comenzar.</h2>
            </div>
            <div className="border-t border-line">
              {FAQS.map(faq => (
                <details key={faq.q} className="border-b border-line py-5 group">
                  <summary className="list-none cursor-pointer text-ink font-bold flex justify-between gap-6 [&::-webkit-details-marker]:hidden">
                    {faq.q}
                    <span aria-hidden="true" className="text-coral text-xl group-open:hidden">+</span>
                    <span aria-hidden="true" className="text-coral text-xl hidden group-open:inline">−</span>
                  </summary>
                  <p className="mt-3 pr-0 sm:pr-9 text-dim">{faq.a}</p>
                </details>
              ))}
            </div>
          </div>
        </section>

        {/* Final CTA */}
        <section className="py-16 sm:py-20 bg-coral text-white text-center">
          <div className="max-w-290 mx-auto px-4 sm:px-6">
            <h2 className="text-[clamp(2rem,4vw,3.1rem)] font-extrabold tracking-[-.03em]">Empieza con una conversación clara.</h2>
            <p className="max-w-160 mx-auto mt-4 mb-7 text-white/80">Cuéntanos qué deseas lograr y te ayudaremos a entender cuál podría ser el siguiente paso.</p>
            <a href="#contacto" className="inline-flex items-center gap-2 min-h-12.5 rounded-full bg-white text-coral-deep font-extrabold px-7 no-underline">
              Recibir orientación <span aria-hidden="true">→</span>
            </a>
          </div>
        </section>
      </main>

      {/* Footer */}
      <footer className="py-8 bg-[#020a1a] text-white/64 text-[13.5px]">
        <div className="max-w-290 mx-auto px-4 sm:px-6 flex flex-col sm:flex-row gap-4 justify-between items-center">
          <img src="/iLoveDRRealty_White.png" alt="I Love DR Realty" className="w-31 opacity-90" />
          <div className="flex flex-wrap gap-4.5 justify-center">
            <span>© {new Date().getFullYear()} I Love DR Realty</span>
            <Link href="/privacy-policy/" className="text-white no-underline">Política de Privacidad</Link>
            <Link href="/terms-of-service/" className="text-white no-underline">Términos de Servicio</Link>
          </div>
        </div>
      </footer>

      {/* Sticky mobile CTA */}
      <a
        href="#contacto"
        className="sm:hidden fixed z-60 left-2.5 right-2.5 bottom-2.5 min-h-13.5 flex items-center justify-center rounded-full text-white bg-coral font-extrabold no-underline shadow-[0_14px_40px_rgba(4,18,43,.32)]"
      >
        Recibir orientación
      </a>
    </div>
  )
}
