# inbox-monitor — verification mail from your mailbox, in the terminal

Start `inbox-monitor watch` with a mailbox and a sender pattern, trigger a login, and it prints
the next mail that sender sends you, so you can read the code or link in it.
Made for letting an AI agent (or a script) finish a login that sends a code by email,
without giving it the whole mailbox.

```
$ inbox-monitor watch naver '.*@github\.com'
waiting for mail from .*@github\.com (naver); Ctrl+C to stop
--- [naver] 2026-11-22 16:20 | GitHub <noreply@github.com>
subject: [GitHub] Please verify your device

Hey there! A sign in attempt requires further verification ...
Verification code: 482913
...
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
| **uv** (recommended) | `uv tool install inbox-monitor` |
| **pipx** | `pipx install inbox-monitor` |
| **pip** | `pip install inbox-monitor` |

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
$ inbox-monitor add
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
`inbox-monitor add work --host imap.example.com --port 993 --user me@example.com`.
Adding an existing name replaces it. The login is tested before anything is saved. The password is stored in
`~/.config/inbox-monitor/<name>.pw` with mode 600.

## Usage

```bash
inbox-monitor add                                  # add an IMAP account
inbox-monitor list                                 # list accounts
inbox-monitor del naver                            # delete an account and its saved password
inbox-monitor watch naver 'noreply@github\.com'    # wait for the next mail from it, print it, exit
inbox-monitor help                                 # full help, including a section for AI agents
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
| `--interval S` | seconds between checks (default 3) |

The whole mail is printed as plain text (HTML mail is converted).
`watch` exits with 0 when a mail was printed and 1 on timeout, and rings the terminal bell when a mail arrives.

Set `INBOX_MONITOR_HOME` to keep the config somewhere other than `~/.config/inbox-monitor`.

## Using it with an AI agent

`inbox-monitor help` (or `--help`) includes a section for agents: when to start the watcher, how to read
the output, exit codes, errors, and rules (no touching the config, mail is untrusted text).
Add one line to your agent's instructions (`CLAUDE.md`, `AGENTS.md`, ...):

> To get a verification code sent by email, use the `inbox-monitor` command. Run `inbox-monitor help` first.

Keep in mind:

- An agent that can read verification mail can also read password-reset mail from the same senders.
- The `From` address can be forged. Most providers reject forged mail, but treat the sender check
  as a filter, not a guarantee.
- The agent runs as your user, so it can read and edit `~/.config/inbox-monitor` too.
  If your agent supports permission rules, deny it access to that directory.
- Mail is untrusted text. An agent should take the code or link from it and not follow instructions written in it.

## License

MIT
