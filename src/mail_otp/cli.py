"""mail-otp: wait for verification mail and print its code (read-only IMAP)."""
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
import time
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime
from importlib.resources import files
from pathlib import Path

from . import __version__

CONFIG_DIR = Path(os.environ.get("MAIL_OTP_HOME", "~/.config/mail-otp")).expanduser()
ACCOUNTS_FILE = CONFIG_DIR / "accounts.ini"
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

def read_config():
    config = configparser.ConfigParser()
    config.read(ACCOUNTS_FILE)
    return config


def load_account(name):
    config = read_config()
    if not config.has_section(name):
        known = ", ".join(config.sections()) or "none; run `mail-otp add <name>`"
        raise SystemExit(f"no account '{name}' (accounts: {known})")
    section = config[name]
    return {
        "name": name,
        "host": section["host"],
        "port": section.getint("port", 993),
        "user": section["user"],
        "password": (CONFIG_DIR / f"{name}.pw").read_text().strip(),
    }


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


def parse(raw, pattern):
    """발신자 주소가 정규식과 일치하면 (when, sender, subject, text), 아니면 None."""
    msg = email.message_from_bytes(raw)
    sender, subject = header(msg, "From"), header(msg, "Subject")
    if not pattern.fullmatch(parseaddr(sender)[1]):
        return None
    try:
        when = parsedate_to_datetime(msg["Date"])
    except (TypeError, ValueError):
        when = dt.datetime.now(dt.timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    text = re.sub(r"[ \t\r\f\v]+", " ", body_text(msg))
    return when, sender, subject, re.sub(r"\n\s*\n+", "\n", text).strip()


class Watcher:
    """한 계정의 INBOX에서 시작 시점 이후에 도착한 메일만 골라낸다."""

    def __init__(self, account):
        self.account = account
        self.imap = connect(account)
        _, data = self.imap.status("INBOX", "(UIDNEXT)")
        self.next_uid = int(re.search(rb"UIDNEXT (\d+)", data[0])[1])
        self.imap.select("INBOX", readonly=True)

    def reconnect(self):
        """서버가 오래된 연결을 끊었을 때 다시 붙는다. 기준 UID는 그대로 둔다."""
        self.close()
        self.imap = connect(self.account)
        self.imap.select("INBOX", readonly=True)

    def poll(self):
        """새로 도착한 메일의 원문을 차례로 돌려준다."""
        self.imap.noop()
        _, data = self.imap.uid("SEARCH", None, f"UID {self.next_uid}:*")
        # "n:*"는 새 메일이 없어도 마지막 메일을 돌려주므로 UID로 다시 거른다
        uids = sorted(int(u) for u in data[0].split() if int(u) >= self.next_uid)
        for uid in uids:
            _, parts = self.imap.uid("FETCH", str(uid), "(BODY.PEEK[])")
            self.next_uid = uid + 1
            if parts and isinstance(parts[0], tuple):
                yield parts[0][1]

    def close(self):
        try:
            self.imap.logout()
        except (imaplib.IMAP4.error, OSError):
            pass


def show(name, mail, full):
    when, sender, subject, text = mail
    print(f"--- [{name}] {when.astimezone():%Y-%m-%d %H:%M} | {sender}")
    print(f"subject: {subject}")
    print(f"codes:   {', '.join(candidates(subject + chr(10) + text)) or '-'}")
    print(text if full else text[:300])
    print(flush=True)


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
    print(f"saved [{args.name}] {user} @ {host}")


def cmd_list(args):
    config = read_config()
    if not config.sections():
        print("no accounts; run `mail-otp add <name>`")
        return
    for name in config.sections():
        section = config[name]
        print(f"{name}\t{section['user']}\t{section['host']}")


def cmd_watch(args):
    try:
        pattern = re.compile(args.sender, re.I)
    except re.error as e:
        raise SystemExit(f"bad regular expression: {e}")
    account = load_account(args.account)
    try:
        watcher = Watcher(account)
    except (imaplib.IMAP4.error, OSError) as e:
        raise SystemExit(f"[{account['name']}] error: {e}")

    timeout = args.timeout if args.timeout is not None else (0 if args.keep else 300)
    deadline = time.monotonic() + timeout if timeout else None
    print(f"waiting for mail from {args.sender} ({account['name']}); Ctrl+C to stop", file=sys.stderr, flush=True)
    try:
        while deadline is None or time.monotonic() < deadline:
            try:
                raws = list(watcher.poll())
            except (imaplib.IMAP4.abort, OSError):
                try:
                    watcher.reconnect()
                except (imaplib.IMAP4.error, OSError) as e:
                    print(f"[{account['name']}] reconnect failed: {e}", file=sys.stderr)
                raws = []
            for raw in raws:
                mail = parse(raw, pattern)
                if not mail:
                    continue
                if sys.stdout.isatty():
                    print("\a", end="")
                show(account["name"], mail, args.full)
                if not args.keep:
                    return
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return
    finally:
        watcher.close()
    print(f"no new mail from {args.sender} within {timeout} seconds", file=sys.stderr)
    sys.exit(1)


def print_help():
    text = files("mail_otp").joinpath("HELP.md").read_text(encoding="utf-8")
    print(text.replace("~/.config/mail-otp", str(CONFIG_DIR)), end="")


class HelpAction(argparse.Action):
    """-h/--help도 help 명령과 같은 설명서를 보여준다."""

    def __init__(self, option_strings, dest, **kwargs):
        super().__init__(option_strings, dest, nargs=0, default=argparse.SUPPRESS, help="show the full help")

    def __call__(self, parser, namespace, values, option_string=None):
        print_help()
        parser.exit()


def main():
    # 최상위 도움말은 argparse 대신 HELP.md 하나로 통일한다
    ap = argparse.ArgumentParser(prog="mail-otp", add_help=False)
    ap.add_argument("-h", "--help", action=HelpAction)
    ap.add_argument("-V", "--version", action="version", version=f"mail-otp {__version__}")
    sub = ap.add_subparsers(dest="command")

    add = sub.add_parser("add", help="add or update an IMAP account (tests the login before saving)")
    add.add_argument("name", help=f"account name; {', '.join(KNOWN_HOSTS)} fill in the host")
    add.add_argument("--host", help="IMAP server")
    add.add_argument("--port", type=int, default=993, help="IMAP SSL port (default 993)")
    add.add_argument("--user", help="login name (asked if omitted)")

    sub.add_parser("list", help="list accounts")

    watch = sub.add_parser(
        "watch", help="wait for the next mail from a sender and print it",
        description="Wait for the next mail whose sender address fully matches the regular expression "
                    "(case-insensitive), print it and exit.",
        epilog="examples:\n  mail-otp watch naver 'noreply@github\\.com'\n  mail-otp watch naver '.*@(github|gitlab)\\.com'",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    watch.add_argument("account", help="account name from `mail-otp list`")
    watch.add_argument("sender", help="regular expression for the sender address")
    watch.add_argument("--timeout", type=int, help="give up after this many seconds (default 300, 0 = never)")
    watch.add_argument("--keep", action="store_true", help="keep watching after the first mail (no timeout by default)")
    watch.add_argument("--full", action="store_true", help="print the full body (for login links)")
    watch.add_argument("--interval", type=float, default=3, help="seconds between checks (default 3)")

    sub.add_parser("help", help="show the full help")
    args = ap.parse_args()

    if args.command == "add":
        cmd_add(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "watch":
        cmd_watch(args)
    else:
        print_help()
