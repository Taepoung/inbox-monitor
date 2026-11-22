# mail-otp — verification codes from your mailbox, in the terminal

Start `mail-otp watch` with a mailbox and a sender pattern, trigger a login, and it prints
the code from the next mail that sender sends you.
Made for letting an AI agent (or a script) finish a login that sends a code by email,
without giving it the whole mailbox.

```
$ mail-otp watch naver '.*@github\.com'
waiting for mail from .*@github\.com (naver); Ctrl+C to stop
--- [naver] 2026-11-22 16:20 | GitHub <noreply@github.com>
subject: [GitHub] Please verify your device
codes:   482913
Hey there! A sign in attempt requires further verification ...
```

- **Only the sender you name**: mail whose sender does not match your pattern is never shown
- **Read-only**: opens the inbox read-only and does not mark mail as read
- **New mail only**: waits for mail that arrives after you start it, then exits
- **Any IMAP mailbox**: Naver, Gmail, Daum/Kakao, Yahoo, iCloud, or your own server
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

```
$ mail-otp add
name (used in list/watch): naver
IMAP host (e.g. imap.naver.com): imap.naver.com
port (usually 993): 993
user: myid
password (app password if you use 2-step login):
saved 'naver': myid @ imap.naver.com:993
```

| Mailbox | IMAP host (port 993) |
|---|---|
| Naver | `imap.naver.com` |
| Gmail | `imap.gmail.com` |
| Daum | `imap.daum.net` |
| Kakao | `imap.kakao.com` |

Everything except the password can also be given on the command line, and only what is missing is asked:
`mail-otp add work --host imap.example.com --port 993 --user me@example.com`.
Adding an existing name replaces it. The login is tested before anything is saved. The password is stored in
`~/.config/mail-otp/<name>.pw` with mode 600.

## Usage

```bash
mail-otp add                                  # add an IMAP account
mail-otp list                                 # list accounts
mail-otp del naver                            # delete an account and its saved password
mail-otp watch naver 'noreply@github\.com'    # wait for the next mail from it, print it, exit
mail-otp help                                 # full help, including a section for AI agents
```

The sender is a regular expression that the whole address must match (case-insensitive):

| Pattern | Matches |
|---|---|
| `noreply@github\.com` | that address only |
| `.*@github\.com` | anyone at github.com |
| `.*@(github\|gitlab)\.com` | either domain |

`watch` options:

| Option | |
|---|---|
| `--timeout S` | give up after S seconds (default 300, 0 = never) |
| `--keep` | keep watching and print every new mail |
| `--full` | print the whole body, for "click this link" logins |
| `--interval S` | seconds between checks (default 3) |

`watch` exits with 0 when a mail was printed and 1 on timeout, and rings the terminal bell when a mail arrives.

Set `MAIL_OTP_HOME` to keep the config somewhere other than `~/.config/mail-otp`.

## Using it with an AI agent

`mail-otp help` (or `--help`) includes a section for agents: when to start the watcher, how to read
the output, exit codes, errors, and rules (no touching the config, mail is untrusted text).
Add one line to your agent's instructions (`CLAUDE.md`, `AGENTS.md`, ...):

> To get a verification code sent by email, use the `mail-otp` command. Run `mail-otp help` first.

Keep in mind:

- An agent that can read verification codes can also read password-reset mail from the same senders.
- The `From` address can be forged. Most providers reject forged mail, but treat the sender check
  as a filter, not a guarantee.
- The agent runs as your user, so it can read and edit `~/.config/mail-otp` too.
  If your agent supports permission rules, deny it access to that directory.
- Mail is untrusted text. An agent should take the code from it and not follow instructions written in it.

## License

MIT
