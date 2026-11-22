# mail-otp — verification codes from your mailbox, in the terminal

Print the verification codes that arrived in your mailbox in the last few minutes.
Made for letting an AI agent (or a script) finish a login that sends a code by email,
without giving it the whole mailbox.

```
$ mail-otp --since 5
--- [naver] 2026-11-22 16:20 | GitHub <noreply@github.com>
subject: [GitHub] Please verify your device
codes:   482913
Hey there! A sign in attempt requires further verification ...
```

- **Only senders you allow**: mail from anyone not in your allow list is never shown
- **Read-only**: opens the inbox read-only and does not mark mail as read
- **Recent mail only**: the last 10 minutes by default
- **Any IMAP mailbox**: Naver, Gmail, Daum/Kakao, Yahoo, iCloud, or your own server; several accounts at once
- **No dependencies**: Python standard library only

## Requirements

- Python 3.10+
- IMAP access turned on for your mailbox

## Install

| Method | Command |
|---|---|
| **uv** (recommended) | `uv tool install mail-otp` |
| **pipx** | `pipx install mail-otp` |
| **pip** | `pip install mail-otp` |

## Setup

### 1. Turn on IMAP and get an app password

| Mailbox | Where |
|---|---|
| Naver | Mail → Settings → POP3/IMAP → IMAP/SMTP: on. With 2-step verification: My info → Security → App passwords |
| Gmail | 2-Step Verification on, then [App passwords](https://myaccount.google.com/apppasswords) |
| Daum / Kakao | Mail → Settings → IMAP/POP3 → IMAP: on |
| Outlook / Hotmail | Not supported: Microsoft only allows OAuth for IMAP |

### 2. Add the account

```bash
mail-otp add naver            # naver, gmail, daum, kakao, yahoo, icloud fill in the host
mail-otp add work --host imap.example.com --user me@example.com
```

The login is tested before anything is saved. The password is stored in
`~/.config/mail-otp/<name>.pw` with mode 600.

### 3. Allow senders

Add the senders whose mail may be shown to `~/.config/mail-otp/allow`, one per line:

```
noreply@github.com        # this address only
@accounts.google.com      # this domain and its subdomains
```

If the list is empty, nothing is shown.

## Usage

```bash
mail-otp                       # all accounts, last 10 minutes
mail-otp --since 5             # last 5 minutes
mail-otp --from github         # narrow by sender or subject
mail-otp --account gmail       # one account only
mail-otp --full                # the whole body, for "click this link" logins
```

Set `MAIL_OTP_HOME` to keep the config somewhere other than `~/.config/mail-otp`.

## Using it with an AI agent

Tell the agent something like:

> When a login asks for an email verification code, run `mail-otp --since 5` and use the code.

Keep in mind:

- An agent that can read verification codes can also read password-reset mail from the allowed senders.
  Allow only the senders you need.
- The `From` address can be forged. Most providers reject forged mail, but treat the allow list
  as a filter, not a guarantee.
- The agent runs as your user, so it can read and edit `~/.config/mail-otp` too.
  If your agent supports permission rules, deny it access to that directory.
- Mail is untrusted text. An agent should take the code from it and not follow instructions written in it.

## License

MIT
