"""SendGrid email delivery service using httpx (async-native)."""

from __future__ import annotations

import httpx
import structlog

from src.core.config import Settings

logger = structlog.get_logger(__name__)

_SENDGRID_SEND_URL = "https://api.sendgrid.com/v3/mail/send"


class EmailService:
    """Thin async wrapper around the SendGrid v3 Mail Send API."""

    def __init__(self, http_client: httpx.AsyncClient, settings: Settings) -> None:
        self._client = http_client
        self._api_key = settings.SENDGRID_API_KEY
        self._from_email = settings.SENDGRID_FROM_EMAIL
        self._from_name = settings.SENDGRID_FROM_NAME

    async def send_otp_email(self, to_email: str, otp_code: str, expire_minutes: int = 5) -> None:
        """Send a branded OTP verification email."""
        html = _render_otp_html(otp_code, expire_minutes)
        payload = {
            "personalizations": [{"to": [{"email": to_email}]}],
            "from": {"email": self._from_email, "name": self._from_name},
            "subject": f"Your LitBridge verification code: {otp_code}",
            "content": [{"type": "text/html", "value": html}],
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        resp = await self._client.post(_SENDGRID_SEND_URL, json=payload, headers=headers)

        if resp.status_code not in (200, 201, 202):
            logger.error(
                "sendgrid_send_failed",
                status=resp.status_code,
                body=resp.text,
                to=to_email,
            )
            raise RuntimeError(f"SendGrid returned {resp.status_code}")

        logger.info("otp_email_sent", to=to_email)


def _render_otp_html(code: str, expire_minutes: int) -> str:
    spaced_code = " ".join(code)
    return f"""\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background-color:#f4f7fa;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f4f7fa;padding:40px 0;">
  <tr><td align="center">
    <table role="presentation" width="480" cellpadding="0" cellspacing="0"
           style="background-color:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.06);">

      <!-- Header -->
      <tr>
        <td style="background:linear-gradient(135deg,#1a73e8,#0d47a1);padding:32px 40px;text-align:center;">
          <h1 style="margin:0;color:#ffffff;font-size:24px;font-weight:700;letter-spacing:0.5px;">
            LitBridge
          </h1>
          <p style="margin:4px 0 0;color:rgba(255,255,255,0.85);font-size:13px;">
            by Scienthesis
          </p>
        </td>
      </tr>

      <!-- Body -->
      <tr>
        <td style="padding:40px;">
          <h2 style="margin:0 0 8px;color:#1a1a2e;font-size:20px;font-weight:600;">
            Verification Code
          </h2>
          <p style="margin:0 0 28px;color:#555;font-size:15px;line-height:1.6;">
            Enter the code below to sign in to your LitBridge account.
          </p>

          <!-- OTP Code -->
          <div style="background-color:#f0f4ff;border:2px dashed #1a73e8;border-radius:10px;
                      padding:24px;text-align:center;margin-bottom:28px;">
            <span style="font-size:36px;font-weight:700;letter-spacing:10px;color:#1a73e8;
                         font-family:'Courier New',Courier,monospace;">
              {spaced_code}
            </span>
          </div>

          <p style="margin:0 0 6px;color:#888;font-size:13px;">
            This code expires in <strong>{expire_minutes} minutes</strong>.
          </p>
          <p style="margin:0;color:#888;font-size:13px;">
            If you didn&rsquo;t request this code, you can safely ignore this email.
          </p>
        </td>
      </tr>

      <!-- Footer -->
      <tr>
        <td style="background-color:#fafbfc;padding:20px 40px;border-top:1px solid #eee;text-align:center;">
          <p style="margin:0;color:#aaa;font-size:12px;">
            &copy; 2026 Scienthesis &middot; LitBridge
          </p>
        </td>
      </tr>

    </table>
  </td></tr>
</table>
</body>
</html>"""
