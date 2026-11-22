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
  allow          optional: if present, only these senders may be watched, one per line

sender: noreply@github.com (exact address) or @github.com (domain and its subdomains);
        with -E, a regular expression the whole address must match, e.g. '.*@(github|gitlab)\\.com'
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


def parse(raw, match, allowlist):
    """지정한 발신자의 메일이면 (when, sender, subject, text), 아니면 None."""
    msg = email.message_from_bytes(raw)
    sender, subject = header(msg, "From"), header(msg, "Subject")
    address = parseaddr(sender)[1].lower()
    if not match(address) or (allowlist and not allowed(address, allowlist)):
        return None
    try:
        when = parsedate_to_datetime(msg["Date"])
    except (TypeError, ValueError):
        when = dt.datetime.now(dt.timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    text = re.sub(r"[ \t\r\f\v]+", " ", body_text(msg))
    return when, sender, subject, re.sub(r"\n\s*\n+", "\n", text).strip()


def fetch_recent(account, cutoff, match, allowlist):
    """cutoff 이후 지정한 발신자의 메일을 최신순으로 돌려준다."""
    imap = connect(account)
    # readonly + BODY.PEEK: 읽음 표시도 남기지 않는다
    imap.select("INBOX", readonly=True)
    since = (cutoff - dt.timedelta(days=1)).strftime("%d-%b-%Y")
    _, data = imap.search(None, "SINCE", since)
    for msg_id in reversed(data[0].split()[-50:]):
        _, parts = imap.fetch(msg_id, "(BODY.PEEK[])")
        mail = parse(parts[0][1], match, allowlist)
        if mail and mail[0] >= cutoff:
            yield mail
    imap.logout()


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


def sender_matcher(args, allowlist):
    """발신자 인자를 '주소 -> 일치 여부' 함수로 바꾼다."""
    if args.regex:
        try:
            patterns = [re.compile(p.strip(), re.I) for p in args.senders]
        except re.error as e:
            raise SystemExit(f"bad regular expression: {e}")
        # 정규식은 미리 allow 파일과 비교할 수 없으므로 메일마다 parse()에서 확인한다
        return lambda address: any(p.fullmatch(address) for p in patterns)

    senders = [s.strip().lower() for s in args.senders]
    for entry in senders:
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+|@?[^@\s]+\.[^@\s]+", entry):
            raise SystemExit(f"not a mail address or @domain: {entry} (use -E for a regular expression)")
        # allow 파일이 있으면 그 밖의 발신자는 지정할 수 없다
        probe = entry if "@" in entry.lstrip("@") else "x@" + entry.lstrip("@")
        if allowlist and not allowed(probe, allowlist):
            raise SystemExit(f"{entry} is not in {ALLOW_FILE}")
    return lambda address: allowed(address, senders)


def require_setup(args):
    allowlist = load_allowlist()
    match = sender_matcher(args, allowlist)
    accounts = load_accounts(args.account)
    if not accounts:
        raise SystemExit("no accounts: run `mail-otp add <name>` first")
    return match, ", ".join(args.senders), allowlist, accounts


def cmd_watch(args):
    match, label, allowlist, accounts = require_setup(args)
    watchers = []
    for account in accounts:
        try:
            watchers.append(Watcher(account))
        except (imaplib.IMAP4.error, OSError) as e:
            print(f"[{account['name']}] error: {e}", file=sys.stderr)
    if not watchers:
        raise SystemExit("could not connect to any account")

    timeout = args.timeout if args.timeout is not None else (0 if args.keep else 300)
    deadline = time.monotonic() + timeout if timeout else None
    names = ", ".join(w.account["name"] for w in watchers)
    print(f"waiting for mail from {label} ({names}); Ctrl+C to stop", file=sys.stderr, flush=True)
    try:
        while deadline is None or time.monotonic() < deadline:
            for watcher in watchers:
                try:
                    raws = list(watcher.poll())
                except (imaplib.IMAP4.abort, OSError):
                    try:
                        watcher.reconnect()
                    except (imaplib.IMAP4.error, OSError) as e:
                        print(f"[{watcher.account['name']}] reconnect failed: {e}", file=sys.stderr)
                    continue
                for raw in raws:
                    mail = parse(raw, match, allowlist)
                    if not mail:
                        continue
                    if sys.stdout.isatty():
                        print("\a", end="")
                    show(watcher.account["name"], mail, args.full)
                    if not args.keep:
                        return
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return
    finally:
        for watcher in watchers:
            watcher.close()
    print(f"no new mail from {label} within {timeout} seconds", file=sys.stderr)
    sys.exit(1)


def cmd_recent(args):
    match, label, allowlist, accounts = require_setup(args)
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=args.since)
    found = 0
    for account in accounts:
        try:
            for mail in fetch_recent(account, cutoff, match, allowlist):
                found += 1
                show(account["name"], mail, args.full)
        except (imaplib.IMAP4.error, OSError) as e:
            print(f"[{account['name']}] error: {e}", file=sys.stderr)
    if not found:
        print(f"no mail from {label} in the last {args.since} minutes")


def main():
    # 발신자를 위치 인자로 받으므로 하위 명령은 첫 단어를 보고 직접 나눈다
    argv = sys.argv[1:]
    command = argv[0] if argv and argv[0] in ("recent", "add", "guide") else "watch"
    if command != "watch":
        argv = argv[1:]

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("senders", nargs="+", metavar="sender",
                        help="mail address (noreply@github.com) or domain (@github.com) to accept mail from")
    common.add_argument("-E", "--regex", action="store_true",
                        help="treat each sender as a regular expression the whole address must match")
    common.add_argument("--account", help="use only this account")
    common.add_argument("--full", action="store_true", help="print the full body (for magic links)")

    if command == "guide":
        print(files("mail_otp").joinpath("AGENT.md").read_text(encoding="utf-8"), end="")
    elif command == "add":
        ap = argparse.ArgumentParser(prog="mail-otp add",
                                     description="Add or update an account. The login is tested before saving.")
        ap.add_argument("name", help=f"account name; {', '.join(KNOWN_HOSTS)} fill in the host")
        ap.add_argument("--host", help="IMAP server")
        ap.add_argument("--port", type=int, default=993, help="IMAP SSL port (default 993)")
        ap.add_argument("--user", help="login name (asked if omitted)")
        cmd_add(ap.parse_args(argv))
    elif command == "recent":
        ap = argparse.ArgumentParser(prog="mail-otp recent", parents=[common],
                                     description="Show mail from the sender that already arrived.")
        ap.add_argument("--since", type=int, default=10, help="minutes to look back (default 10)")
        cmd_recent(ap.parse_args(argv))
    else:
        ap = argparse.ArgumentParser(
            prog="mail-otp",
            usage="%(prog)s [options] sender [sender ...]\n       %(prog)s recent [options] sender [sender ...]\n"
                  "       %(prog)s add [options] name\n       %(prog)s guide    (instructions for AI agents)",
            description="Wait for the next mail from the sender and print its code (read-only IMAP).",
            epilog=EPILOG,
            formatter_class=argparse.RawDescriptionHelpFormatter,
            parents=[common],
        )
        ap.add_argument("-V", "--version", action="version", version=f"mail-otp {__version__}")
        ap.add_argument("--timeout", type=int, help="give up after this many seconds (default 300, 0 = never)")
        ap.add_argument("--keep", action="store_true", help="keep watching after the first mail (no timeout by default)")
        ap.add_argument("--interval", type=float, default=3, help="seconds between checks (default 3)")
        cmd_watch(ap.parse_args(argv))
