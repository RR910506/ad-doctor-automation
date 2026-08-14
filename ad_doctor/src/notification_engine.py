from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from email.message import EmailMessage
import os
import re
import smtplib
from typing import Any

from src.config import DATABASE_PATH
from src.db import get_connection, initialize_database
from src.security.resolver import build_secret_provider


EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class SmtpSettings:
    host: str | None
    port: int
    username: str | None
    password: str | None
    from_email: str | None
    use_tls: bool
    source: str = "environment"

    @property
    def missing_fields(self) -> list[str]:
        missing = []
        if not self.host:
            missing.append("AD_DOCTOR_SMTP_HOST")
        if not self.from_email:
            missing.append("AD_DOCTOR_SMTP_FROM")
        if self.username and not self.password:
            missing.append("AD_DOCTOR_SMTP_PASSWORD")
        return missing

    @property
    def is_ready(self) -> bool:
        return not self.missing_fields


@dataclass(frozen=True)
class NotificationSummary:
    recipients: int
    sent: int
    failed: int
    skipped_reason: str | None = None


def load_smtp_settings(database_path=DATABASE_PATH, *, secret_provider=None) -> SmtpSettings:
    saved = load_saved_smtp_settings(database_path=database_path)
    secrets = secret_provider or build_secret_provider(database_path)
    host=secrets.resolve("SMTP_HOST"); port=secrets.resolve("SMTP_PORT"); username=secrets.resolve("SMTP_USERNAME"); password=secrets.resolve("SMTP_PASSWORD"); from_email=secrets.resolve("SMTP_FROM_EMAIL")
    return SmtpSettings(
        host=host.value,
        port=int(port.value or "587"),
        username=username.value,
        password=password.value,
        from_email=from_email.value,
        use_tls=parse_bool(saved.get("use_tls"), default_env=os.getenv("AD_DOCTOR_SMTP_USE_TLS")),
        source="saved" if saved else "environment",
    )


def load_saved_smtp_settings(database_path=DATABASE_PATH) -> dict[str, Any]:
    initialize_database(database_path=database_path)
    with closing(get_connection(database_path)) as connection:
        row = connection.execute(
            """
            SELECT
                smtp_host,
                smtp_port,
                smtp_username,
                smtp_password,
                smtp_from,
                use_tls,
                updated_at
            FROM email_settings
            WHERE id = 1
            """
        ).fetchone()
    return dict(row) if row else {}


def save_smtp_settings(
    *,
    smtp_host: str,
    smtp_port: int,
    smtp_username: str | None,
    smtp_password: str | None,
    smtp_from: str,
    use_tls: bool,
    keep_existing_password: bool = True,
    database_path=DATABASE_PATH,
) -> SmtpSettings:
    initialize_database(database_path=database_path)
    from src.security.resolver import configure_runtime_secret
    for key, value in {
        "SMTP_HOST": smtp_host, "SMTP_PORT": smtp_port, "SMTP_USERNAME": smtp_username,
        "SMTP_PASSWORD": smtp_password, "SMTP_FROM_EMAIL": smtp_from,
    }.items():
        configure_runtime_secret(database_path, key, value)
    # Secret-bearing SMTP fields are now read-only legacy compatibility data.
    # New configuration actions persist only the public TLS preference.
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            """
            INSERT INTO email_settings (id, use_tls, updated_at)
            VALUES (1, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                use_tls = excluded.use_tls,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                1 if use_tls else 0,
            ),
        )
        connection.commit()
    return load_smtp_settings(database_path=database_path)


def parse_bool(value: Any, *, default_env: str | None = None) -> bool:
    if value is None:
        value = default_env
    if value is None:
        return True
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def upsert_app_user(
    email: str,
    display_name: str | None = None,
    notifications_enabled: bool = True,
    mark_logged_in: bool = True,
    database_path=DATABASE_PATH,
) -> None:
    normalized_email = normalize_email(email)
    initialize_database(database_path=database_path)
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            """
            INSERT INTO app_users (
                email,
                display_name,
                notifications_enabled,
                last_login_at,
                updated_at
            )
            VALUES (?, ?, ?, CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END, CURRENT_TIMESTAMP)
            ON CONFLICT(email) DO UPDATE SET
                display_name = excluded.display_name,
                notifications_enabled = excluded.notifications_enabled,
                last_login_at = CASE
                    WHEN ? THEN CURRENT_TIMESTAMP
                    ELSE app_users.last_login_at
                END,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                normalized_email,
                display_name,
                1 if notifications_enabled else 0,
                1 if mark_logged_in else 0,
                1 if mark_logged_in else 0,
            ),
        )
        connection.commit()


def list_app_users(database_path=DATABASE_PATH) -> list[dict[str, Any]]:
    initialize_database(database_path=database_path)
    with closing(get_connection(database_path)) as connection:
        rows = connection.execute(
            """
            SELECT
                id,
                email,
                display_name,
                notifications_enabled,
                last_login_at,
                created_at,
                updated_at
            FROM app_users
            ORDER BY notifications_enabled DESC, email ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def list_notification_recipients(database_path=DATABASE_PATH) -> list[str]:
    initialize_database(database_path=database_path)
    with closing(get_connection(database_path)) as connection:
        rows = connection.execute(
            """
            SELECT email
            FROM app_users
            WHERE notifications_enabled = 1
            ORDER BY email ASC
            """
        ).fetchall()
    return [str(row["email"]) for row in rows]


def list_notification_events(limit: int = 50, database_path=DATABASE_PATH) -> list[dict[str, Any]]:
    initialize_database(database_path=database_path)
    with closing(get_connection(database_path)) as connection:
        rows = connection.execute(
            """
            SELECT
                id,
                run_id,
                recipient_email,
                subject,
                status,
                error_message,
                sent_at,
                created_at
            FROM notification_events
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def notify_daily_scrape_complete(
    summary,
    database_path=DATABASE_PATH,
    error_message: str | None = None,
) -> NotificationSummary:
    recipients = list_notification_recipients(database_path=database_path)
    if not recipients:
        return NotificationSummary(recipients=0, sent=0, failed=0, skipped_reason="No notification recipients.")
    smtp_settings = load_smtp_settings(database_path=database_path)
    run_id = str(getattr(summary, "run_id", "unknown"))
    subject = (
        "Ad Doctor scraping failed"
        if error_message
        else "Ad Doctor scraping is done: content is ready"
    )
    body = build_daily_notification_body(summary, error_message=error_message)
    if not smtp_settings.is_ready:
        skipped = f"SMTP is not configured: missing {', '.join(smtp_settings.missing_fields)}."
        record_notification_events(
            run_id=run_id,
            recipients=recipients,
            subject=subject,
            status="skipped",
            error_message=skipped,
            database_path=database_path,
        )
        return NotificationSummary(
            recipients=len(recipients),
            sent=0,
            failed=0,
            skipped_reason=skipped,
        )

    sent = 0
    failed = 0
    for recipient in recipients:
        try:
            send_email(smtp_settings, recipient, subject, body)
            record_notification_event(
                run_id=run_id,
                recipient_email=recipient,
                subject=subject,
                status="sent",
                error_message=None,
                database_path=database_path,
            )
            sent += 1
        except Exception as exc:
            record_notification_event(
                run_id=run_id,
                recipient_email=recipient,
                subject=subject,
                status="failed",
                error_message=str(exc),
                database_path=database_path,
            )
            failed += 1
    return NotificationSummary(recipients=len(recipients), sent=sent, failed=failed)


def send_test_notification_email(
    recipient: str,
    database_path=DATABASE_PATH,
) -> NotificationSummary:
    normalized_recipient = normalize_email(recipient)
    subject = "Ad Doctor email test"
    body = (
        "This is a test email from Ad Doctor.\n\n"
        "If you received this, content-ready email alerts are configured correctly."
    )
    smtp_settings = load_smtp_settings(database_path=database_path)
    if not smtp_settings.is_ready:
        skipped = f"SMTP is not configured: missing {', '.join(smtp_settings.missing_fields)}."
        record_notification_event(
            run_id="email_test",
            recipient_email=normalized_recipient,
            subject=subject,
            status="skipped",
            error_message=skipped,
            database_path=database_path,
        )
        return NotificationSummary(
            recipients=1,
            sent=0,
            failed=0,
            skipped_reason=skipped,
        )
    try:
        send_email(smtp_settings, normalized_recipient, subject, body)
    except Exception as exc:
        record_notification_event(
            run_id="email_test",
            recipient_email=normalized_recipient,
            subject=subject,
            status="failed",
            error_message=str(exc),
            database_path=database_path,
        )
        return NotificationSummary(recipients=1, sent=0, failed=1)
    record_notification_event(
        run_id="email_test",
        recipient_email=normalized_recipient,
        subject=subject,
        status="sent",
        error_message=None,
        database_path=database_path,
    )
    return NotificationSummary(recipients=1, sent=1, failed=0)


def build_daily_notification_body(summary, error_message: str | None = None) -> str:
    if error_message:
        return (
            "Ad Doctor daily scraping failed.\n\n"
            f"Run ID: {getattr(summary, 'run_id', 'unknown')}\n"
            f"Error: {error_message}\n"
        )
    return "\n".join(
        [
            "Ad Doctor daily scraping is complete. Content is ready for review.",
            "",
            f"Run ID: {summary.run_id}",
            f"Articles seen: {summary.articles_seen}",
            f"Articles inserted: {summary.articles_inserted}",
            f"Articles updated: {summary.articles_updated}",
            f"Fetch failures: {summary.fetch_failures}",
            f"Topic links: {summary.topics_linked}",
            f"Qualified topics: {summary.qualified_topics}",
            f"Locked topics: {summary.locked_topics}",
            f"Research dossiers: {summary.research_dossiers}",
            f"Campaign dissections: {summary.dissections}",
            f"Blog drafts: {summary.blog_drafts}",
            f"Social drafts: {summary.social_drafts}",
        ]
    )


def send_email(settings: SmtpSettings, recipient: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = settings.from_email or ""
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP(settings.host or "", settings.port, timeout=20) as server:
        if settings.use_tls:
            server.starttls()
        if settings.username:
            server.login(settings.username, settings.password or "")
        server.send_message(message)


def record_notification_events(
    run_id: str,
    recipients: list[str],
    subject: str,
    status: str,
    error_message: str | None,
    database_path=DATABASE_PATH,
) -> None:
    for recipient in recipients:
        record_notification_event(
            run_id=run_id,
            recipient_email=recipient,
            subject=subject,
            status=status,
            error_message=error_message,
            database_path=database_path,
        )


def record_notification_event(
    run_id: str,
    recipient_email: str,
    subject: str,
    status: str,
    error_message: str | None,
    database_path=DATABASE_PATH,
) -> None:
    initialize_database(database_path=database_path)
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            """
            INSERT INTO notification_events (
                run_id,
                recipient_email,
                subject,
                status,
                error_message,
                sent_at
            )
            VALUES (?, ?, ?, ?, ?, CASE WHEN ? = 'sent' THEN CURRENT_TIMESTAMP ELSE NULL END)
            """,
            (run_id, recipient_email, subject, status, error_message, status),
        )
        connection.commit()


def normalize_email(email: str) -> str:
    normalized = email.strip().lower()
    if not EMAIL_PATTERN.fullmatch(normalized):
        raise ValueError("Enter a valid email address.")
    return normalized
