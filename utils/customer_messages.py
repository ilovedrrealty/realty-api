"""Pure request-related welcome copy. No sending, tracking URLs or marketing drip."""
from dataclasses import dataclass
from html import escape
import re
from uuid import UUID


@dataclass(frozen=True)
class CustomerMessage:
    subject: str
    html: str
    text: str
    sms: str


COPY = {
    "en": {
        "signup": ("Welcome to I Love DR Realty", "Thank you for joining I Love DR Realty!", "Start by exploring properties, or tell us what you want to buy, sell or rent.", "Explore properties", "/search/"),
        "buyer_interest": ("Thank you — let’s find your property", "Thank you for contacting I Love DR Realty! We received your buying request.", "The next step is to share your preferred area, budget and purchase timeline with our team.", "Explore properties", "/search/"),
        "seller_interest": ("Thank you — let’s plan your property sale", "Thank you for contacting I Love DR Realty! We received your selling request.", "Have your property location, features and preferred timeline ready so our team can help plan the next step.", "See our selling services", "/selling/"),
        "rental_interest": ("Thank you — let’s find your rental", "Thank you for contacting I Love DR Realty! We received your rental request.", "Let our team know your preferred dates, area, number of guests and budget to help narrow the options.", "Explore rentals", "/rentals/"),
        "property_inquiry": ("Thank you for your property inquiry", "Thank you for your interest! I Love DR Realty received your property inquiry.", "Our team can help confirm availability and arrange the next step. A request does not reserve a property or confirm a viewing.", "Review your property", "/search/"),
        "booking": ("We received your stay request", "Thank you for contacting I Love DR Realty about your stay.", "Your request is not a confirmed booking. Please follow the booking page for availability, terms and secure payment.", "Explore rentals", "/rentals/"),
        "greeting": "Hi", "footer": "This message acknowledges your request. It does not subscribe you to marketing.",
        "sms_stop": "Reply STOP to opt out.",
    },
    "es": {
        "signup": ("Te damos la bienvenida a I Love DR Realty", "¡Gracias por registrarte en I Love DR Realty!", "Comienza explorando propiedades o cuéntanos qué deseas comprar, vender o alquilar.", "Explorar propiedades", "/search/"),
        "buyer_interest": ("Gracias — encontremos tu propiedad", "¡Gracias por contactar a I Love DR Realty! Recibimos tu solicitud de compra.", "El siguiente paso es compartir con nuestro equipo la zona que prefieres, tu presupuesto y cuándo deseas comprar.", "Explorar propiedades", "/search/"),
        "seller_interest": ("Gracias — planifiquemos la venta de tu propiedad", "¡Gracias por contactar a I Love DR Realty! Recibimos tu solicitud de venta.", "Ten a mano la ubicación, las características de tu propiedad y el plazo deseado para que nuestro equipo te ayude con el siguiente paso.", "Ver servicios de venta", "/selling/"),
        "rental_interest": ("Gracias — encontremos tu alquiler", "¡Gracias por contactar a I Love DR Realty! Recibimos tu solicitud de alquiler.", "Comparte con nuestro equipo las fechas, la zona, el número de huéspedes y tu presupuesto para encontrar opciones adecuadas.", "Explorar alquileres", "/rentals/"),
        "property_inquiry": ("Gracias por tu consulta sobre la propiedad", "¡Gracias por tu interés! I Love DR Realty recibió tu consulta sobre la propiedad.", "Nuestro equipo puede ayudarte a confirmar la disponibilidad y coordinar el siguiente paso. La solicitud no reserva la propiedad ni confirma una visita.", "Ver la propiedad", "/search/"),
        "booking": ("Recibimos tu solicitud de estadía", "Gracias por contactar a I Love DR Realty sobre tu estadía.", "Tu solicitud no es una reserva confirmada. Consulta la página de reservas para ver disponibilidad, condiciones y realizar el pago seguro.", "Explorar alquileres", "/rentals/"),
        "greeting": "Hola", "footer": "Este mensaje confirma la recepción de tu solicitud. No te suscribe a publicidad.",
        "sms_stop": "Responde STOP para dejar de recibir mensajes.",
    },
}


def customer_message(event: str, name: str = "", locale: str = "en", *, public_listing_id=None) -> CustomerMessage:
    """Only caller-verified public listing UUIDs can become property links.

    No free-form URL, message, price or contact data enters an outbound template.
    Invalid/absent listing context falls back to public search. URLs never log in
    a user, reveal a private project, or claim availability/booking confirmation.
    """
    language = "es" if locale == "es" else "en"
    copy = COPY[language]
    if event not in {"signup", "buyer_interest", "seller_interest", "rental_interest", "property_inquiry", "booking"}:
        raise ValueError("Unsupported customer confirmation event")
    subject, thanks, next_step, label, path = copy[event]
    if event == "property_inquiry" and public_listing_id:
        try:
            path = f"/listing/{UUID(str(public_listing_id))}/"
        except (ValueError, TypeError, AttributeError):
            pass
    if event == "property_inquiry" and path == "/search/":
        label = copy["signup"][3]
    url = "https://ilovedrrealty.com" + path
    # Strip control characters and bound customer-supplied names before render.
    safe_name = " ".join(re.sub(r"[\x00-\x1f\x7f]", "", str(name)).split())[:100]
    greeting = f"{copy['greeting']} {safe_name}," if safe_name else f"{copy['greeting']},"
    text = f"{greeting}\n\n{thanks}\n\n{next_step}\n\n{label}: {url}\n\n{copy['footer']}"
    html = f'''<!DOCTYPE html><html lang="{language}"><body style="margin:0;background:#f5f3ef;font-family:Arial,sans-serif"><main style="max-width:520px;margin:24px auto;background:white;padding:28px;color:#00102e"><h1 style="font-size:22px">{escape(subject)}</h1><p>{escape(greeting)}</p><p>{escape(thanks)}</p><p style="line-height:1.6">{escape(next_step)}</p><p><a href="{url}" style="display:inline-block;padding:12px 20px;background:#00102e;color:white;border-radius:8px">{escape(label)}</a></p><p style="font-size:12px;color:#666">{escape(copy['footer'])}</p></main></body></html>'''
    # No user name, free text or property address is needed in a phone alert.
    sms = f"{thanks} {label}: {url} {copy['sms_stop']}"
    return CustomerMessage(subject, html, text, sms)


def owner_sms(event: str) -> str:
    if event == "signup":
        return "I Love DR Realty: A new verified user registered. Review securely: https://app.ilovedrrealty.com/users?workspace=admin Reply STOP to opt out."
    if event not in {"buyer_interest", "seller_interest", "rental_interest", "property_inquiry", "booking"}:
        raise ValueError("Unsupported owner alert event")
    label = event.replace("_", " ")
    return f"I Love DR Realty: New {label}. Review securely: https://app.ilovedrrealty.com/leads?workspace=admin Reply STOP to opt out."
