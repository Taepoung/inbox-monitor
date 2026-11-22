"""mail-otp: print recent verification codes from IMAP mailboxes (read-only)."""
import argparse
import configparser
import datetime as dt
import email
import getpass
import html
import imaplib
import os
import re
import sys
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

from . import __version__

CONFIG_DIR = Path(os.environ.get("MAIL_OTP_HOME", "~/.config/mail-otp")).expanduser()
ACCOUNTS_FILE = CONFIG_DIR / "accounts.ini"
ALLOW_FILE = CONFIG_DIR / "allow"
CODE_RE = re.compile(r"(?<![\d-])(\d{4,8})(?![\d-])")
KEYWORDS = re.compile(r"인증|코드|번호|code|verif|otp|pin|login|로그인", re.I)
NAME_RE = re.compile(r"[A-Za-z0-9_-]+")

# 계정 이름만으로 호스트를 채워 주는 잘 알려진 서비스들
KNOWN_HOSTS = {
    "naver": "imap.naver.com",
    "gmail": "imap.gmail.com",
    "daum": "imap.daum.net",
    "kakao": "imap.kakao.com",
    "yahoo": "imap.mail.yahoo.com",
    "icloud": "imap.mail.me.com",
}

EPILOG = f"""\
config: {CONFIG_DIR}
  accounts.ini   one section per account (host, user, optional port)
  <name>.pw      password for that account, mode 600
  allow          senders whose mail may be shown, one per line:
                   noreply@github.com    exact address
                   @accounts.google.com  whole domain (subdomains included)
"""


def load_allowlist():
    if not ALLOW_FILE.exists():
        return []
    lines = (line.split("#")[0].strip().lower() for line in ALLOW_FILE.read_text().splitlines())
    return [line for line in lines if line]


def read_config():
    config = configparser.ConfigParser()
    config.read(ACCOUNTS_FILE)
    return config


def load_accounts(only=None):
    config = read_config()
    names = [only] if only else config.sections()
    accounts = []
    for name in names:
        if not config.has_section(name):
            raise SystemExit(f"no account [{name}] in {ACCOUNTS_FILE}")
        section = config[name]
        accounts.append({
            "name": name,
            "host": section["host"],
            "port": section.getint("port", 993),
            "user": section["user"],
            "password": (CONFIG_DIR / f"{name}.pw").read_text().strip(),
        })
    return accounts


def allowed(address, allowlist):
    domain = address.rpartition("@")[2]
    for entry in allowlist:
        if entry.startswith("@") or "@" not in entry:
            base = entry.lstrip("@")
            if domain == base or domain.endswith("." + base):
                return True
        elif address == entry:
            return True
    return False


def header(msg, name):
    raw = msg.get(name, "")
    return str(make_header(decode_header(raw))) if raw else ""


def body_text(msg):
    plain, rich = [], []
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html") or part.get_filename():
            continue
        payload = part.get_payload(decode=True) or b""
        text = payload.decode(part.get_content_charset() or "utf-8", "replace")
        (plain if ctype == "text/plain" else rich).append(text)
    if plain:
        return "\n".join(plain)
    text = re.sub(r"<(script|style).*?</\1>", " ", "\n".join(rich), flags=re.S | re.I)
    return html.unescape(re.sub(r"<[^>]+>", " ", text))


def candidates(text):
    """인증 관련 단어가 있는 줄의 숫자를 먼저, 나머지 숫자는 그 뒤에."""
    near, rest = [], []
    for line in text.splitlines():
        for code in CODE_RE.findall(line):
            (near if KEYWORDS.search(line) else rest).append(code)
    seen = []
    for code in near + rest:
        if code not in seen:
            seen.append(code)
    return seen[:5]


def connect(account):
    imap = imaplib.IMAP4_SSL(account["host"], account["port"])
    imap.login(account["user"], account["password"])
    return imap


def fetch(account, cutoff, allowlist, sender_filter):
    """cutoff 이후 허용된 발신자의 메일을 (when, sender, subject, text)로 돌려준다."""
    imap = connect(account)
    # readonly + BODY.PEEK: 읽음 표시도 남기지 않는다
    imap.select("INBOX", readonly=True)
    since = (cutoff - dt.timedelta(days=1)).strftime("%d-%b-%Y")
    _, data = imap.search(None, "SINCE", since)
    for msg_id in reversed(data[0].split()[-50:]):
        _, parts = imap.fetch(msg_id, "(BODY.PEEK[])")
        msg = email.message_from_bytes(parts[0][1])
        try:
            when = parsedate_to_datetime(msg["Date"])
        except (TypeError, ValueError):
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.timezone.utc)
        if when < cutoff:
            continue
        sender, subject = header(msg, "From"), header(msg, "Subject")
        if not allowed(parseaddr(sender)[1].lower(), allowlist):
            continue
        if sender_filter and sender_filter.lower() not in (sender + subject).lower():
            continue
        text = re.sub(r"[ \t\r\f\v]+", " ", body_text(msg))
        yield when, sender, subject, re.sub(r"\n\s*\n+", "\n", text).strip()
    imap.logout()


def write_private(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(path, 0o600)


def cmd_add(args):
    if not NAME_RE.fullmatch(args.name):
        raise SystemExit("account name may only contain letters, digits, '-' and '_'")
    host = args.host or KNOWN_HOSTS.get(args.name)
    if not host:
        raise SystemExit(f"--host is required (known names: {', '.join(KNOWN_HOSTS)})")
    user = args.user or input("user: ").strip()
    password = getpass.getpass("password (app password if you use 2FA): ")

    account = {"name": args.name, "host": host, "port": args.port, "user": user, "password": password}
    try:
        connect(account).logout()
    except (imaplib.IMAP4.error, OSError) as e:
        raise SystemExit(f"login failed: {e}\nnothing was saved")

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CONFIG_DIR, 0o700)
    config = read_config()
    config[args.name] = {"host": host, "user": user}
    if args.port != 993:
        config[args.name]["port"] = str(args.port)
    with open(ACCOUNTS_FILE, "w") as f:
        config.write(f)
    write_private(CONFIG_DIR / f"{args.name}.pw", password)
    if not ALLOW_FILE.exists():
        write_private(ALLOW_FILE, "# one sender per line: name@example.com or @example.com\n")
    print(f"saved [{args.name}] {user} @ {host}")
    if not load_allowlist():
        print(f"next: add allowed senders to {ALLOW_FILE}")


def cmd_check(args):
    allowlist = load_allowlist()
    if not allowlist:
        raise SystemExit(f"no allowed senders: add addresses to {ALLOW_FILE}")
    accounts = load_accounts(args.account)
    if not accounts:
        raise SystemExit("no accounts: run `mail-otp add <name>` first")
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=args.since)

    found = 0
    for account in accounts:
        try:
            for when, sender, subject, text in fetch(account, cutoff, allowlist, args.sender):
                found += 1
                print(f"--- [{account['name']}] {when.astimezone():%Y-%m-%d %H:%M} | {sender}")
                print(f"subject: {subject}")
                print(f"codes:   {', '.join(candidates(subject + chr(10) + text)) or '-'}")
                print(text if args.full else text[:300])
                print()
        except (imaplib.IMAP4.error, OSError) as e:
            print(f"[{account['name']}] error: {e}", file=sys.stderr)

    if not found:
        print(f"no mail from allowed senders in the last {args.since} minutes")


def main():
    ap = argparse.ArgumentParser(
        prog="mail-otp",
        description="Print recent verification codes from IMAP mailboxes (read-only).",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("-V", "--version", action="version", version=f"mail-otp {__version__}")
    ap.add_argument("--since", type=int, default=10, help="minutes to look back (default 10)")
    ap.add_argument("--from", dest="sender", help="substring of sender or subject to narrow allowed mail")
    ap.add_argument("--account", help="check only this account")
    ap.add_argument("--full", action="store_true", help="print the full body (for magic links)")
    sub = ap.add_subparsers(dest="command")
    add = sub.add_parser("add", help="add or update an account (tests the login before saving)")
    add.add_argument("name", help=f"account name; {', '.join(KNOWN_HOSTS)} fill in the host")
    add.add_argument("--host", help="IMAP server")
    add.add_argument("--port", type=int, default=993, help="IMAP SSL port (default 993)")
    add.add_argument("--user", help="login name (asked if omitted)")
    args = ap.parse_args()

    if args.command == "add":
        cmd_add(args)
    else:
        cmd_check(args)
