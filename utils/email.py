from html import escape
from urllib.parse import quote, urlparse
from config import settings

CODE_EXPIRE_MINUTES = 15


def _is_google_scheduling_url(url: str | None) -> bool:
    """User.calendly_url holds either a Calendly or a Google Calendar booking link; tell them apart by host."""
    host = urlparse(url or "").hostname or ""
    return host in ("calendar.app.google", "calendar.google.com")


def _send(to_email: str, subject: str, html: str) -> None:
    if not settings.resend_api_key:
        print(f"[DEV] Email to {to_email}: {subject}")
        return
    import resend
    resend.api_key = settings.resend_api_key
    resend.Emails.send({"from": settings.email_from, "to": to_email, "subject": subject, "html": html})


def _email_wrap(header_text: str, body_html: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<body style="margin:0;padding:0;background:#f5f3ef;font-family:sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f5f3ef;padding:40px 16px">
    <tr><td align="center">
      <table width="100%" style="max-width:480px;background:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.07)">
        <tr>
          <td align="left" style="background:#00102e;padding:20px 28px">
            <img src="{_logo_src()}" alt="I Love DR Realty" style="height:44px;width:auto;display:block" />
          </td>
        </tr>
        <tr><td style="padding:32px 32px 0">{body_html}</td></tr>
        <tr>
          <td style="padding:20px 32px 28px;border-top:1px solid #f0ebe2">
            <p style="margin:0;color:#aaa;font-size:12px;line-height:1.6">
              {header_text} — I Love DR Realty
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def send_listing_approved_email(to_email: str, display_name: str, listing_title: str, listing_url: str) -> None:
    body = f"""
      <h2 style="margin:0 0 10px;color:#00102e;font-size:21px;font-weight:700">Your listing is live!</h2>
      <p style="margin:0 0 20px;color:#555;font-size:14.5px;line-height:1.6">
        Hi {escape(display_name)}, your listing <strong>{escape(listing_title)}</strong> has been approved and is now publicly visible on I Love DR Realty.
      </p>
      <table cellpadding="0" cellspacing="0" style="margin-bottom:28px">
        <tr><td>
          <a href="{listing_url}" style="display:inline-block;background:#00102e;color:#ffffff;text-decoration:none;font-weight:700;font-size:14.5px;padding:13px 30px;border-radius:8px">
            View Listing &rarr;
          </a>
        </td></tr>
      </table>
    """
    _send(to_email, "Your listing has been approved ✓", _email_wrap("Listing approved", body))


def send_listing_rejected_email(to_email: str, display_name: str, listing_title: str, reason: str) -> None:
    body = f"""
      <h2 style="margin:0 0 10px;color:#00102e;font-size:21px;font-weight:700">Your listing needs revision</h2>
      <p style="margin:0 0 12px;color:#555;font-size:14.5px;line-height:1.6">
        Hi {escape(display_name)}, your listing <strong>{escape(listing_title)}</strong> was not approved.
      </p>
      <div style="background:#fdf2f2;border:1px solid #f5dada;border-radius:10px;padding:16px 20px;margin-bottom:24px">
        <p style="margin:0;color:#b91c1c;font-size:14px;line-height:1.6">{escape(reason)}</p>
      </div>
      <p style="margin:0 0 28px;color:#555;font-size:14px;line-height:1.6">
        Please update your listing and resubmit for review from your dashboard.
      </p>
    """
    _send(to_email, "Your listing needs revision", _email_wrap("Listing not approved", body))


def send_upgrade_approved_email(to_email: str, display_name: str, new_role: str) -> None:
    role_label = escape(new_role.replace("_", " ").title())
    body = f"""
      <h2 style="margin:0 0 10px;color:#00102e;font-size:21px;font-weight:700">Account upgraded to {role_label}</h2>
      <p style="margin:0 0 20px;color:#555;font-size:14.5px;line-height:1.6">
        Hi {escape(display_name)}, your request to become a <strong>{role_label}</strong> on I Love DR Realty has been approved.
        Log in to your dashboard to access your new features.
      </p>
      <table cellpadding="0" cellspacing="0" style="margin-bottom:28px">
        <tr><td>
          <a href="{settings.landing_url}/login" style="display:inline-block;background:#00102e;color:#ffffff;text-decoration:none;font-weight:700;font-size:14.5px;padding:13px 30px;border-radius:8px">
            Go to Dashboard &rarr;
          </a>
        </td></tr>
      </table>
    """
    _send(to_email, f"Your account has been upgraded to {role_label}", _email_wrap("Account upgrade approved", body))


def send_upgrade_rejected_email(to_email: str, display_name: str, reason: str) -> None:
    body = f"""
      <h2 style="margin:0 0 10px;color:#00102e;font-size:21px;font-weight:700">Upgrade request not approved</h2>
      <p style="margin:0 0 12px;color:#555;font-size:14.5px;line-height:1.6">
        Hi {escape(display_name)}, your role upgrade request was not approved at this time.
      </p>
      <div style="background:#fdf2f2;border:1px solid #f5dada;border-radius:10px;padding:16px 20px;margin-bottom:28px">
        <p style="margin:0;color:#b91c1c;font-size:14px;line-height:1.6">{escape(reason)}</p>
      </div>
    """
    _send(to_email, "Your upgrade request was not approved", _email_wrap("Upgrade request", body))


def send_realtor_assigned_owner_email(to_email: str, realtor_name: str, owner_name: str, owner_email: str) -> None:
    body = f"""
      <h2 style="margin:0 0 10px;color:#00102e;font-size:21px;font-weight:700">You've been assigned a new property owner</h2>
      <p style="margin:0 0 20px;color:#555;font-size:14.5px;line-height:1.6">
        Hi {escape(realtor_name)}, a property owner has been assigned to you on I Love DR Realty.
        Log in to your dashboard to view their details and begin managing their listing.
      </p>
      <div style="background:#f5f9ff;border:1px solid #d0e4f7;border-radius:10px;padding:16px 20px;margin-bottom:24px">
        <p style="margin:0 0 4px;color:#0b63ab;font-size:13px;font-weight:700">{escape(owner_name)}</p>
        <p style="margin:0;color:#555;font-size:13px">{escape(owner_email)}</p>
      </div>
      <table cellpadding="0" cellspacing="0" style="margin-bottom:28px">
        <tr><td>
          <a href="{settings.landing_url}/dashboard" style="display:inline-block;background:#00102e;color:#ffffff;text-decoration:none;font-weight:700;font-size:14.5px;padding:13px 30px;border-radius:8px">
            Go to Dashboard &rarr;
          </a>
        </td></tr>
      </table>
    """
    _send(to_email, f"New owner assigned to you — {owner_name}", _email_wrap("Owner assignment", body))


def send_lead_notification(lead, property_info: dict | None = None, notify_email: str | None = None) -> None:
    email = notify_email or settings.notify_email
    if not email:
        return
    type_labels = {
        "property_inquiry": "Property Inquiry",
        "buyer_interest":   "Buyer Interest",
        "seller_interest":  "Seller Interest",
        "rental_interest":  "Rental Interest",
        "booking":          "Booking Request",
    }
    type_label = type_labels.get(lead.type, escape(lead.type))
    phone_row = f"<tr><td style='padding:4px 0;color:#888;font-size:13px;width:90px'>Phone</td><td style='padding:4px 0;font-size:13px;color:#00102e'>{escape(lead.phone)}</td></tr>" if lead.phone else ""
    message_block = f"""
      <div style="background:#f5f3ef;border-radius:8px;padding:14px 16px;margin:16px 0">
        <p style="margin:0;color:#555;font-size:13.5px;line-height:1.6;white-space:pre-wrap">{escape(lead.message)}</p>
      </div>""" if lead.message else ""
    property_block = ""
    if property_info:
        prop_title = escape(property_info.get("title", ""))
        prop_url = f"{settings.landing_url}/listing/{property_info.get('id', '')}"
        property_block = f"""
      <p style="margin:16px 0 6px;font-size:12px;font-weight:700;color:#888;text-transform:uppercase;letter-spacing:.06em">Property</p>
      <a href="{prop_url}" style="color:#0b63ab;font-size:13.5px;font-weight:600;text-decoration:none">{prop_title} &rarr;</a>"""
    dashboard_url = f"{settings.landing_url}/dashboard"
    body = f"""
      <h2 style="margin:0 0 4px;color:#00102e;font-size:20px;font-weight:700">New lead captured</h2>
      <p style="margin:0 0 20px;color:#888;font-size:13px">{type_label}</p>
      <table style="border-collapse:collapse;width:100%">
        <tr><td style="padding:4px 0;color:#888;font-size:13px;width:90px">Name</td><td style="padding:4px 0;font-size:13.5px;font-weight:600;color:#00102e">{escape(lead.name)}</td></tr>
        <tr><td style="padding:4px 0;color:#888;font-size:13px">Email</td><td style="padding:4px 0;font-size:13px;color:#00102e"><a href="mailto:{escape(lead.email)}" style="color:#0b63ab">{escape(lead.email)}</a></td></tr>
        {phone_row}
      </table>
      {message_block}
      {property_block}
      <table cellpadding="0" cellspacing="0" style="margin-top:24px;margin-bottom:8px">
        <tr><td>
          <a href="{dashboard_url}" style="display:inline-block;background:#00102e;color:#ffffff;text-decoration:none;font-weight:700;font-size:13.5px;padding:11px 26px;border-radius:8px">
            View in Dashboard &rarr;
          </a>
        </td></tr>
      </table>
    """
    _send(email, f"New lead: {lead.name}", _email_wrap("Lead notification", body))


def send_realtor_lead_assigned_email(
    to_email: str,
    realtor_name: str,
    lead,
    property_info: dict | None = None,
    calendly_url: str | None = None,
) -> None:
    type_labels = {
        "property_inquiry": "Property Inquiry",
        "buyer_interest":   "Buyer Interest",
        "seller_interest":  "Seller Interest",
        "rental_interest":  "Rental Interest",
        "booking":          "Booking Request",
    }
    type_label = type_labels.get(lead.type, escape(lead.type))
    phone_row = f"<tr><td style='padding:4px 0;color:#888;font-size:13px;width:90px'>Phone</td><td style='padding:4px 0;font-size:13px;color:#00102e'>{escape(lead.phone)}</td></tr>" if lead.phone else ""
    message_block = f"""
      <div style="background:#f5f3ef;border-radius:8px;padding:14px 16px;margin:16px 0">
        <p style="margin:0;color:#555;font-size:13.5px;line-height:1.6;white-space:pre-wrap">{escape(lead.message)}</p>
      </div>""" if lead.message else ""
    property_block = ""
    if property_info:
        prop_title = escape(property_info.get("title", ""))
        prop_url = f"{settings.landing_url}/listing/{property_info.get('id', '')}"
        property_block = f"""
      <p style="margin:16px 0 6px;font-size:12px;font-weight:700;color:#888;text-transform:uppercase;letter-spacing:.06em">Property</p>
      <a href="{prop_url}" style="color:#0b63ab;font-size:13.5px;font-weight:600;text-decoration:none">{prop_title} &rarr;</a>"""
    dashboard_url = f"{settings.landing_url}/dashboard"
    body = f"""
      <h2 style="margin:0 0 4px;color:#00102e;font-size:20px;font-weight:700">You have a new lead</h2>
      <p style="margin:0 0 20px;color:#888;font-size:13px">Hi {escape(realtor_name)}, a lead has been assigned to you.</p>
      <p style="margin:0 0 6px;font-size:12px;font-weight:700;color:#888;text-transform:uppercase;letter-spacing:.06em">{type_label}</p>
      <table style="border-collapse:collapse;width:100%;margin-bottom:4px">
        <tr><td style="padding:4px 0;color:#888;font-size:13px;width:90px">Name</td><td style="padding:4px 0;font-size:13.5px;font-weight:600;color:#00102e">{escape(lead.name)}</td></tr>
        <tr><td style="padding:4px 0;color:#888;font-size:13px">Email</td><td style="padding:4px 0;font-size:13px;color:#00102e"><a href="mailto:{escape(lead.email)}" style="color:#0b63ab">{escape(lead.email)}</a></td></tr>
        {phone_row}
      </table>
      {message_block}
      {property_block}
      <table cellpadding="0" cellspacing="0" style="margin-top:24px;margin-bottom:8px">
        <tr><td>
          <a href="{dashboard_url}" style="display:inline-block;background:#00102e;color:#ffffff;text-decoration:none;font-weight:700;font-size:13.5px;padding:11px 26px;border-radius:8px">
            View Lead &rarr;
          </a>
        </td></tr>
      </table>
    """
    scheduling_label = "Google Calendar" if _is_google_scheduling_url(calendly_url) else "Calendly"
    calendly_block = f"""
<table cellpadding="0" cellspacing="0" style="margin-bottom:16px">
  <tr><td>
    <a href="{escape(calendly_url or '')}" style="display:inline-block;background:#006BFF;color:#ffffff;text-decoration:none;font-weight:700;font-size:14px;padding:11px 26px;border-radius:8px">
      Schedule a Meeting &#8594;
    </a>
  </td></tr>
</table>
<p style="margin:0 0 20px;color:#888;font-size:12.5px;line-height:1.6">
  Use your {scheduling_label} link above to schedule a meeting with this lead.
</p>
""" if calendly_url else ""
    body = body + calendly_block
    _send(to_email, f"New lead assigned to you — {lead.name}", _email_wrap("Lead assigned", body))


def send_password_reset_email(to_email: str, reset_url: str) -> None:
    body = f"""
      <h2 style="margin:0 0 10px;color:#00102e;font-size:21px;font-weight:700">Reset your password</h2>
      <p style="margin:0 0 20px;color:#555;font-size:14.5px;line-height:1.6">
        We received a request to reset the password for your I Love DR Realty account.
        Click the button below to set a new password. This link expires in <strong>1 hour</strong>.
      </p>
      <table cellpadding="0" cellspacing="0" style="margin-bottom:24px">
        <tr><td>
          <a href="{reset_url}" style="display:inline-block;background:#e10f1f;color:#ffffff;text-decoration:none;font-weight:700;font-size:14.5px;padding:13px 30px;border-radius:8px">
            Reset Password &rarr;
          </a>
        </td></tr>
      </table>
      <p style="margin:0 0 6px;color:#999;font-size:12.5px">If the button doesn't work, copy and paste this link:</p>
      <p style="margin:0 0 28px;font-size:12px;word-break:break-all">
        <a href="{reset_url}" style="color:#e10f1f;text-decoration:underline">{reset_url}</a>
      </p>
    """
    _send(
        to_email,
        "Reset your I Love DR Realty password",
        _email_wrap("If you didn't request this, you can safely ignore this email.", body),
    )


def send_password_changed_email(to_email: str) -> None:
    body = f"""
      <h2 style="margin:0 0 10px;color:#00102e;font-size:21px;font-weight:700">Your password has been changed</h2>
      <p style="margin:0 0 20px;color:#555;font-size:14.5px;line-height:1.6">
        The password for your I Love DR Realty account was successfully reset.
        You can now log in with your new password.
      </p>
      <table cellpadding="0" cellspacing="0" style="margin-bottom:24px">
        <tr><td>
          <a href="{settings.landing_url}/login" style="display:inline-block;background:#00102e;color:#ffffff;text-decoration:none;font-weight:700;font-size:14.5px;padding:13px 30px;border-radius:8px">
            Log In &rarr;
          </a>
        </td></tr>
      </table>
      <p style="margin:0 0 28px;color:#888;font-size:13px;line-height:1.6">
        If you did not make this change, please contact us immediately at
        <a href="mailto:support@ilovedrrealty.com" style="color:#e10f1f;text-decoration:underline">support@ilovedrrealty.com</a>.
      </p>
    """
    _send(
        to_email,
        "Your I Love DR Realty password has been changed",
        _email_wrap("Security notice — if you didn't do this, contact us right away.", body),
    )


def _logo_src() -> str:
    # White version for use on the coral header
    base = settings.logo_url or f"{settings.landing_url}/iLoveDRRealty_White.png"
    return base


def send_verification_email(to_email: str, code: str) -> None:
    verify_url = f"{settings.landing_url}/verify?email={quote(to_email)}&code={code}"

    if not settings.resend_api_key:
        print(f"[DEV] Verification code for {to_email}: {code}  |  {verify_url}")
        return

    import resend
    resend.api_key = settings.resend_api_key
    resend.Emails.send({  # type: ignore[attr-defined]
        "from": settings.email_from,
        "to": to_email,
        "subject": "Your I Love DR Realty verification code",
        "html": f"""
        <!DOCTYPE html>
        <html lang="en">
        <body style="margin:0;padding:0;background:#f5f3ef;font-family:sans-serif">
          <table width="100%" cellpadding="0" cellspacing="0" style="background:#f5f3ef;padding:40px 16px">
            <tr><td align="center">
              <table width="100%" style="max-width:480px;background:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.07)">

                <!-- Header -->
                <tr>
                  <td align="left" style="background:#00102e;padding:20px 28px">
                    <img src="{_logo_src()}" alt="I Love DR Realty" style="height:44px;width:auto;display:block" />
                  </td>
                </tr>

                <!-- Body -->
                <tr>
                  <td style="padding:32px 32px 0">
                    <h2 style="margin:0 0 10px;color:#00102e;font-size:21px;font-weight:700">Verify your email address</h2>
                    <p style="margin:0 0 24px;color:#555;font-size:14.5px;line-height:1.6">
                      Use the code below to verify your account. It expires in <strong>{CODE_EXPIRE_MINUTES} minutes</strong>.
                    </p>

                    <!-- Code box -->
                    <div style="font-size:40px;font-weight:700;letter-spacing:14px;color:#e10f1f;padding:22px 16px;background:#fdf2f2;border-radius:10px;text-align:center;margin-bottom:24px;border:1px solid #f5dada">
                      {code}
                    </div>

                    <p style="margin:0 0 14px;color:#555;font-size:14px">Or click the button to confirm directly:</p>

                    <!-- CTA button -->
                    <table cellpadding="0" cellspacing="0" style="margin-bottom:24px">
                      <tr>
                        <td>
                          <a href="{verify_url}"
                             style="display:inline-block;background:#e10f1f;color:#ffffff;text-decoration:none;font-weight:700;font-size:14.5px;padding:13px 30px;border-radius:8px">
                            Verify Email &rarr;
                          </a>
                        </td>
                      </tr>
                    </table>

                    <p style="margin:0 0 6px;color:#999;font-size:12.5px">If the button doesn't work, copy and paste this link:</p>
                    <p style="margin:0 0 28px;font-size:12px;word-break:break-all">
                      <a href="{verify_url}" style="color:#e10f1f;text-decoration:underline">{verify_url}</a>
                    </p>
                  </td>
                </tr>

                <!-- Footer -->
                <tr>
                  <td style="padding:20px 32px 28px;border-top:1px solid #f0ebe2">
                    <p style="margin:0;color:#aaa;font-size:12px;line-height:1.6">
                      If you didn't create an I Love DR Realty account, you can safely ignore this email.
                    </p>
                  </td>
                </tr>

              </table>
            </td></tr>
          </table>
        </body>
        </html>
        """,
    })


def send_booking_submitted_email(
    to_email: str,
    guest_name: str,
    listing_title: str,
    check_in: str,
    check_out: str,
    guests: int,
    total_price: float | None,
    is_paypal: bool,
) -> None:
    payment_notice = ""
    if is_paypal and total_price is not None:
        payment_notice = f"""
      <div style="background:#f0faf4;border:1px solid #b6e4c7;border-radius:10px;padding:14px 18px;margin-bottom:20px">
        <p style="margin:0;color:#1f7a3d;font-size:13.5px;font-weight:600">Payment of ${total_price:,.2f} authorized</p>
        <p style="margin:0;color:#555;font-size:13px;margin-top:4px">You won't be charged until the owner accepts your booking.</p>
      </div>"""
    body = f"""
      <h2 style="margin:0 0 10px;color:#00102e;font-size:21px;font-weight:700">Booking request received!</h2>
      <p style="margin:0 0 16px;color:#555;font-size:14.5px;line-height:1.6">
        Hi {escape(guest_name)}, we've received your booking request for <strong>{escape(listing_title)}</strong>.
        The owner will review and respond shortly.
      </p>
      {payment_notice}
      <div style="background:#f5f3ef;border-radius:10px;padding:16px 20px;margin-bottom:28px">
        <table style="border-collapse:collapse;width:100%">
          <tr><td style="padding:4px 0;color:#888;font-size:13px;width:90px">Check-in</td><td style="padding:4px 0;font-size:13.5px;font-weight:600;color:#00102e">{escape(check_in)}</td></tr>
          <tr><td style="padding:4px 0;color:#888;font-size:13px">Check-out</td><td style="padding:4px 0;font-size:13.5px;font-weight:600;color:#00102e">{escape(check_out)}</td></tr>
          <tr><td style="padding:4px 0;color:#888;font-size:13px">Guests</td><td style="padding:4px 0;font-size:13px;color:#00102e">{guests}</td></tr>
        </table>
      </div>
    """
    _send(to_email, f"Booking request received — {listing_title}", _email_wrap("Booking request", body))


def send_booking_confirmed_email(
    to_email: str,
    guest_name: str,
    listing_title: str,
    check_in: str,
    check_out: str,
    total_price: float | None,
) -> None:
    charge_row = (
        f"<tr><td style='padding:4px 0;color:#888;font-size:13px;width:90px'>Total charged</td>"
        f"<td style='padding:4px 0;font-size:13.5px;font-weight:700;color:#1f7a3d'>${total_price:,.2f}</td></tr>"
        if total_price else ""
    )
    body = f"""
      <h2 style="margin:0 0 10px;color:#00102e;font-size:21px;font-weight:700">Your booking is confirmed!</h2>
      <p style="margin:0 0 16px;color:#555;font-size:14.5px;line-height:1.6">
        Great news, {escape(guest_name)}! The owner has accepted your booking for <strong>{escape(listing_title)}</strong>.
      </p>
      <div style="background:#f0faf4;border:1px solid #b6e4c7;border-radius:10px;padding:16px 20px;margin-bottom:24px">
        <table style="border-collapse:collapse;width:100%">
          <tr><td style="padding:4px 0;color:#888;font-size:13px;width:90px">Check-in</td><td style="padding:4px 0;font-size:13.5px;font-weight:600;color:#00102e">{escape(check_in)}</td></tr>
          <tr><td style="padding:4px 0;color:#888;font-size:13px">Check-out</td><td style="padding:4px 0;font-size:13.5px;font-weight:600;color:#00102e">{escape(check_out)}</td></tr>
          {charge_row}
        </table>
      </div>
      <p style="margin:0 0 28px;color:#555;font-size:13.5px;line-height:1.6">
        Questions? Contact us at
        <a href="mailto:support@ilovedrrealty.com" style="color:#0b63ab">support@ilovedrrealty.com</a>.
      </p>
    """
    _send(to_email, f"Booking confirmed — {listing_title}", _email_wrap("Booking confirmed", body))


def send_booking_declined_email(
    to_email: str,
    guest_name: str,
    listing_title: str,
    check_in: str,
    check_out: str,
) -> None:
    body = f"""
      <h2 style="margin:0 0 10px;color:#00102e;font-size:21px;font-weight:700">Booking request not accepted</h2>
      <p style="margin:0 0 16px;color:#555;font-size:14.5px;line-height:1.6">
        Hi {escape(guest_name)}, unfortunately the owner was unable to accept your booking request for
        <strong>{escape(listing_title)}</strong> ({escape(check_in)} → {escape(check_out)}).
        If a payment was authorized, it has been voided and you will not be charged.
      </p>
      <table cellpadding="0" cellspacing="0" style="margin-bottom:28px">
        <tr><td>
          <a href="{settings.landing_url}" style="display:inline-block;background:#00102e;color:#ffffff;text-decoration:none;font-weight:700;font-size:14.5px;padding:13px 30px;border-radius:8px">
            Browse Other Listings &rarr;
          </a>
        </td></tr>
      </table>
    """
    _send(to_email, f"Booking request not accepted — {listing_title}", _email_wrap("Booking declined", body))


def send_owner_new_booking_email(
    to_email: str,
    owner_name: str,
    guest_name: str,
    listing_title: str,
    check_in: str,
    check_out: str,
    guests: int,
) -> None:
    dashboard_url = f"{settings.landing_url}/dashboard"
    body = f"""
      <h2 style="margin:0 0 4px;color:#00102e;font-size:20px;font-weight:700">New booking request</h2>
      <p style="margin:0 0 20px;color:#888;font-size:13px">Hi {escape(owner_name)}, someone wants to book your property.</p>
      <div style="background:#f5f9ff;border:1px solid #d0e4f7;border-radius:10px;padding:16px 20px;margin-bottom:20px">
        <table style="border-collapse:collapse;width:100%">
          <tr><td style="padding:4px 0;color:#888;font-size:13px;width:90px">Guest</td><td style="padding:4px 0;font-size:13.5px;font-weight:600;color:#00102e">{escape(guest_name)}</td></tr>
          <tr><td style="padding:4px 0;color:#888;font-size:13px">Listing</td><td style="padding:4px 0;font-size:13px;color:#00102e">{escape(listing_title)}</td></tr>
          <tr><td style="padding:4px 0;color:#888;font-size:13px">Check-in</td><td style="padding:4px 0;font-size:13px;color:#00102e">{escape(check_in)}</td></tr>
          <tr><td style="padding:4px 0;color:#888;font-size:13px">Check-out</td><td style="padding:4px 0;font-size:13px;color:#00102e">{escape(check_out)}</td></tr>
          <tr><td style="padding:4px 0;color:#888;font-size:13px">Guests</td><td style="padding:4px 0;font-size:13px;color:#00102e">{guests}</td></tr>
        </table>
      </div>
      <table cellpadding="0" cellspacing="0" style="margin-bottom:8px">
        <tr><td>
          <a href="{dashboard_url}" style="display:inline-block;background:#00102e;color:#ffffff;text-decoration:none;font-weight:700;font-size:13.5px;padding:11px 26px;border-radius:8px">
            Review Booking &rarr;
          </a>
        </td></tr>
      </table>
    """
    _send(to_email, f"New booking request — {listing_title}", _email_wrap("New booking", body))


def send_owner_payout_released_email(
    to_email: str,
    owner_name: str,
    listing_title: str,
    check_in: str,
    check_out: str,
    payout_amount: float,
) -> None:
    body = f"""
      <h2 style="margin:0 0 10px;color:#00102e;font-size:21px;font-weight:700">Your payout has been sent!</h2>
      <p style="margin:0 0 16px;color:#555;font-size:14.5px;line-height:1.6">
        Hi {escape(owner_name)}, your rental income for <strong>{escape(listing_title)}</strong> has been released to your PayPal account.
      </p>
      <div style="background:#f0faf4;border:1px solid #b6e4c7;border-radius:10px;padding:16px 20px;margin-bottom:24px">
        <table style="border-collapse:collapse;width:100%">
          <tr><td style="padding:4px 0;color:#888;font-size:13px;width:90px">Listing</td><td style="padding:4px 0;font-size:13px;color:#00102e">{escape(listing_title)}</td></tr>
          <tr><td style="padding:4px 0;color:#888;font-size:13px">Dates</td><td style="padding:4px 0;font-size:13px;color:#00102e">{escape(check_in)} → {escape(check_out)}</td></tr>
          <tr><td style="padding:4px 0;color:#888;font-size:13px">Payout</td><td style="padding:4px 0;font-size:15px;font-weight:700;color:#1f7a3d">${payout_amount:,.2f}</td></tr>
        </table>
      </div>
      <p style="margin:0 0 28px;color:#555;font-size:13px;line-height:1.6">
        Funds typically arrive within 1–2 business days. Check your PayPal account for details.
      </p>
    """
    _send(
        to_email,
        f"Payout sent — ${payout_amount:,.2f} for {listing_title}",
        _email_wrap("Payout released", body),
    )


def send_admin_payout_failed_email(
    to_email: str,
    listing_title: str,
    booking_id: str,
    payout_email: str,
    payout_amount: float,
) -> None:
    dashboard_url = f"{settings.landing_url}/dashboard"
    body = f"""
      <h2 style="margin:0 0 4px;color:#00102e;font-size:20px;font-weight:700">Automatic payout failed</h2>
      <p style="margin:0 0 16px;color:#888;font-size:13px">The nightly payout cron could not release funds for the following booking.</p>
      <div style="background:#fdf2f2;border:1px solid #f5dada;border-radius:10px;padding:16px 20px;margin-bottom:20px">
        <table style="border-collapse:collapse;width:100%">
          <tr><td style="padding:4px 0;color:#888;font-size:13px;width:110px">Booking ID</td><td style="padding:4px 0;font-size:12.5px;color:#00102e;word-break:break-all">{escape(booking_id)}</td></tr>
          <tr><td style="padding:4px 0;color:#888;font-size:13px">Listing</td><td style="padding:4px 0;font-size:13px;color:#00102e">{escape(listing_title)}</td></tr>
          <tr><td style="padding:4px 0;color:#888;font-size:13px">PayPal email</td><td style="padding:4px 0;font-size:13px;color:#00102e">{escape(payout_email)}</td></tr>
          <tr><td style="padding:4px 0;color:#888;font-size:13px">Amount</td><td style="padding:4px 0;font-size:13.5px;font-weight:700;color:#b91c1c">${payout_amount:,.2f}</td></tr>
        </table>
      </div>
      <table cellpadding="0" cellspacing="0" style="margin-bottom:8px">
        <tr><td>
          <a href="{dashboard_url}" style="display:inline-block;background:#00102e;color:#ffffff;text-decoration:none;font-weight:700;font-size:13.5px;padding:11px 26px;border-radius:8px">
            Review in Dashboard &rarr;
          </a>
        </td></tr>
      </table>
    """
    _send(to_email, f"Payout failed — {listing_title}", _email_wrap("Payout failure alert", body))


def send_payout_request_email(
    to_email: str,
    owner_name: str,
    listing_title: str,
    check_in: str,
    check_out: str,
    total_price: float,
    payout_amount: float,
) -> None:
    dashboard_url = f"{settings.landing_url}/dashboard"
    body = f"""
      <h2 style="margin:0 0 4px;color:#00102e;font-size:20px;font-weight:700">Payout request received</h2>
      <p style="margin:0 0 20px;color:#888;font-size:13px">An owner or realtor is requesting a manual payout for a failed booking payout.</p>
      <div style="background:#f5f9ff;border:1px solid #d0e4f7;border-radius:10px;padding:16px 20px;margin-bottom:20px">
        <table style="border-collapse:collapse;width:100%">
          <tr><td style="padding:4px 0;color:#888;font-size:13px;width:100px">Owner</td><td style="padding:4px 0;font-size:13.5px;font-weight:600;color:#00102e">{escape(owner_name)}</td></tr>
          <tr><td style="padding:4px 0;color:#888;font-size:13px">Listing</td><td style="padding:4px 0;font-size:13px;color:#00102e">{escape(listing_title)}</td></tr>
          <tr><td style="padding:4px 0;color:#888;font-size:13px">Dates</td><td style="padding:4px 0;font-size:13px;color:#00102e">{escape(check_in)} &rarr; {escape(check_out)}</td></tr>
          <tr><td style="padding:4px 0;color:#888;font-size:13px">Total</td><td style="padding:4px 0;font-size:13px;color:#00102e">${total_price:,.2f}</td></tr>
          <tr><td style="padding:4px 0;color:#888;font-size:13px">Payout</td><td style="padding:4px 0;font-size:13.5px;font-weight:700;color:#1f7a3d">${payout_amount:,.2f}</td></tr>
        </table>
      </div>
      <table cellpadding="0" cellspacing="0" style="margin-bottom:8px">
        <tr><td>
          <a href="{dashboard_url}" style="display:inline-block;background:#00102e;color:#ffffff;text-decoration:none;font-weight:700;font-size:13.5px;padding:11px 26px;border-radius:8px">
            Go to Dashboard &rarr;
          </a>
        </td></tr>
      </table>
    """
    _send(to_email, f"Payout request — {escape(listing_title)}", _email_wrap("Payout request", body))
