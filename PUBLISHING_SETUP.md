# Optional approved document and email delivery

These integrations are separate from collection and AI generation. Creating a
Google document or sending an email requires an explicit action on a draft that
passed automated checks and has a recorded human approval. The application does
not perform either action merely because a draft was generated or approved.

## Google Docs

Enable Google Docs API and Google Drive API in an owner-controlled Google Cloud
project. Authorize an OAuth user for the scopes `documents` and `drive.file`:

- `https://www.googleapis.com/auth/documents`
- `https://www.googleapis.com/auth/drive.file`

Add the resulting authorized-user JSON to the app's server secrets as
`GOOGLE_AUTHORIZED_USER_JSON`, using a TOML multiline string when entering it in
Streamlit. This JSON contains refresh-token/client secrets; do not put it in Git
or chat. The owner must complete Google's account consent; Codex cannot grant
that consent on the owner's behalf.

Documents are created in the authorized user's Drive with its default private
visibility. The app does not add public link permissions. An operation tag and
saved publication record avoid creating or inserting the same content twice.
An uncertain document-create outcome is held for reconciliation instead of
automatically creating another document.

## Email

Configure a provider that supports authenticated SMTP on port 587 with STARTTLS:

```toml
SMTP_HOST = "smtp.YOUR_PROVIDER.example"
SMTP_PORT = "587"
SMTP_USERNAME = "YOUR_PRIVATE_ACCOUNT"
SMTP_PASSWORD = "YOUR_PRIVATE_APP_PASSWORD"
FROM_EMAIL = "editor@YOUR_DOMAIN.example"
```

Use the provider's approved sender and complete any required sender/domain
verification. Passwords belong only in server secrets. A user explicitly enters
one recipient and requests delivery of approved content. The app records the
recipient and delivery outcome in private operational storage. It never expands
the recipient into an unreviewed mailing list.

Each approved-content/recipient combination has one delivery claim. If the SMTP
connection breaks after sending begins, the outcome is marked `unknown`. Do not
delete that record or resend blindly: check the mail provider's delivery log to
reconcile whether it accepted the message. Before-send failures are also saved;
automatic delivery retries are disabled. After correcting a recorded
before-send connection/authentication failure, another explicit delivery action
can retry it safely. A per-operation lease prevents concurrent retry clicks from
issuing duplicate sends or document creates.

Consented-list delivery attempts at most 100 new recipients per click. Saved
successful deliveries are counted as already sent; uncertain/in-progress
deliveries are held. Both are skipped without consuming the new-send allowance,
so a later click advances to remaining recipients. The subscriber directory
currently reads up to 1,000 records; larger lists require a paginated directory
before complete delivery can be claimed.

Neither integration has been validated against an owner's real account until
credentials are configured and an explicitly authorized live action succeeds.
