import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger(__name__)

SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
SMTP_HOST = "smtp-relay.brevo.com"
SMTP_PORT = 587

LANG_GREETINGS = {
    "ta": ("வணக்கம்", "உங்கள் நினைவுகளை எழுத தொடங்குங்கள்."),
    "hi": ("नमस्ते", "अपनी यादें लिखना शुरू करें।"),
    "ml": ("നമസ്കാരം", "നിങ്ങളുടെ ഓർമ്മകൾ എഴുതാൻ തുടങ്ങൂ."),
    "te": ("నమస్కారం", "మీ జ్ఞాపకాలు రాయడం ప్రారంభించండి."),
    "kn": ("ನಮಸ್ಕಾರ", "ನಿಮ್ಮ ನೆನಪುಗಳನ್ನು ಬರೆಯಲು ಪ್ರಾರಂಭಿಸಿ."),
}


def _send(to_email: str, subject: str, html_body: str) -> bool:
    if not SMTP_USER or not SMTP_PASS:
        logger.warning("SMTP credentials not configured — skipping email to %s", to_email)
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = "Ninaivugal 🌸 <hello@ninaivugal.space>"
        msg["To"] = to_email
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, to_email, msg.as_string())
        logger.info("Email sent to %s: %s", to_email, subject)
        return True
    except Exception as e:
        logger.error("Failed to send email to %s: %s", to_email, e)
        return False


def send_welcome_email(name: str, to_email: str, preferred_language: str = "en") -> bool:
    greeting, tagline = LANG_GREETINGS.get(preferred_language, ("Hello", "Start writing your memories."))

    html = f"""
    <div style="font-family: Georgia, serif; max-width: 560px; margin: 0 auto; color: #1a1512; padding: 40px 20px;">
      <p style="font-size: 28px; font-style: italic; color: #b4854a; margin: 0 0 8px;">{greeting},</p>
      <h1 style="font-weight: 400; font-size: 36px; margin: 0 0 24px;">{name}.</h1>
      <p style="font-size: 17px; line-height: 1.6; color: #5a4e42;">
        Your diary is ready. Every entry is just for you — written in your language, shaped by your words, stored privately under your account.
      </p>
      <p style="font-size: 15px; line-height: 1.6; color: #8a7e72; font-style: italic;">
        {tagline}
      </p>
      <hr style="border: none; border-top: 1px solid #e8e0d4; margin: 32px 0;" />
      <p style="font-size: 12px; color: #b0a898; letter-spacing: 0.1em; text-transform: uppercase;">
        Ninaivugal · நினைவுகள் · Est. 2026
      </p>
    </div>
    """
    return _send(to_email, f"Welcome to Ninaivugal, {name}", html)


def send_password_reset_email(name: str, to_email: str, reset_link: str) -> bool:
    html = f"""
    <div style="font-family: Georgia, serif; max-width: 560px; margin: 0 auto; color: #1a1512; padding: 40px 20px;">
      <p style="font-size: 28px; font-style: italic; color: #b4854a; margin: 0 0 8px;">Hey {name},</p>
      <p style="font-size: 17px; line-height: 1.6; color: #5a4e42;">
        We received a request to reset your Ninaivugal passphrase. Click the button below — the link is valid for 1 hour.
      </p>
      <div style="text-align: center; margin: 32px 0;">
        <a href="{reset_link}" style="display: inline-block; padding: 14px 32px; background: #1a1512; color: #faf8f5; border-radius: 999px; font-family: sans-serif; font-size: 15px; font-weight: 500; text-decoration: none; letter-spacing: 0.02em;">
          Reset my passphrase →
        </a>
      </div>
      <p style="font-size: 14px; line-height: 1.6; color: #8a7e72;">
        If you didn't request this, you can safely ignore this email. Your passphrase won't change.
      </p>
      <hr style="border: none; border-top: 1px solid #e8e0d4; margin: 32px 0;" />
      <p style="font-size: 12px; color: #b0a898; letter-spacing: 0.1em; text-transform: uppercase;">
        Ninaivugal · நினைவுகள் · Est. 2026
      </p>
    </div>
    """
    return _send(to_email, "Reset your Ninaivugal passphrase", html)


def send_reminder_email(name: str, to_email: str) -> bool:
    html = f"""
    <div style="font-family: Georgia, serif; max-width: 560px; margin: 0 auto; color: #1a1512; padding: 40px 20px;">
      <p style="font-size: 28px; font-style: italic; color: #b4854a; margin: 0 0 8px;">Hey {name},</p>
      <p style="font-size: 17px; line-height: 1.6; color: #5a4e42;">
        A few minutes with your diary can do more than you think. Pour your day out — whatever's on your mind, in whatever words come naturally.
      </p>
      <p style="font-size: 15px; font-style: italic; color: #8a7e72;">
        Your pages are waiting.
      </p>
      <hr style="border: none; border-top: 1px solid #e8e0d4; margin: 32px 0;" />
      <p style="font-size: 12px; color: #b0a898; letter-spacing: 0.1em; text-transform: uppercase;">
        Ninaivugal · நினைவுகள்
      </p>
    </div>
    """
    return _send(to_email, f"Your diary is waiting, {name}", html)


def send_invite_email(to_email: str, inviter_name: str) -> bool:
    html = f"""
    <div style="font-family: Georgia, serif; max-width: 560px; margin: 0 auto; color: #1a1512; padding: 40px 20px;">
      <p style="font-size: 17px; line-height: 1.6; color: #5a4e42;">
        <strong>{inviter_name}</strong> thinks you'd enjoy keeping a diary on Ninaivugal — a private space to write in your own words, your own language.
      </p>
      <p style="font-size: 15px; line-height: 1.6; color: #8a7e72; font-style: italic;">
        Write in Tamil, Hindi, English, Tanglish, or any mix — your diary will shape itself around you.
      </p>
      <div style="margin: 32px 0;">
        <a href="https://ninaivugal.space/login"
           style="display:inline-block;background:#b4854a;color:#fff;text-decoration:none;
                  padding:12px 28px;border-radius:6px;font-size:14px;letter-spacing:0.06em">
          Join Ninaivugal →
        </a>
      </div>
      <hr style="border: none; border-top: 1px solid #e8e0d4; margin: 24px 0;" />
      <p style="font-size: 11px; color: #b0a898; letter-spacing: 0.1em; text-transform: uppercase;">
        Ninaivugal · நினைவுகள் · Your memories, your language
      </p>
    </div>
    """
    return _send(to_email, f"{inviter_name} invited you to Ninaivugal", html)
