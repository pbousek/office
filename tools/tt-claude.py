#!/usr/bin/env python3
"""Čas strávený s Claude Code -> návrh záznamů pro TimeTrack.

    tt-claude.py plan [DEN]      # návrh (jen čte; DEN = today|yesterday|YYYY-MM-DD)
    tt-claude.py sync [DEN] [--activity A --note "..."] [--yes]
                                 # průběžný zápis: doplní nové bloky a prodlouží rozběhnuté záznamy
    tt-claude.py add --customer X --activity Y --from 2026-11-09T21:10 --to 2026-11-09T23:05 \\
                     [--note "..."] [--yes]    # bez --yes jen vypíše, co by zapsal

`sync` je idempotentní: blok bez záznamu vloží (potřebuje --activity a --note), záznam, který
se s blokem překrývá, jen PRODLOUŽÍ (nikdy nezkracuje, poznámku nemění, pokud nedostane --note),
blok překrytý víc záznamy nechá být. Bez --yes jen vypíše, co by udělal.

`plan` čte transkripty sessions (~/.claude/projects/*/*.jsonl), z časových značek
zpráv skládá bloky práce (mezera delší než TT_IDLE_MIN ukončí blok), zákazníka určí
podle pracovního adresáře session (~/.config/timetrack/dirmap, stejný formát jako
u reconcile.py) a porovná s tím, co už je v TimeTracku zapsáno (GET /api/day).

Konfigurace (env, nebo soubory v ~/.config/timetrack):
    TIMETRACK_URL    / soubor `url`    (výchozí http://localhost:8731)
    TIMETRACK_TOKEN  / soubor `token`  API token (👤 účet -> API token), práva 600
    TT_CONFIG_DIR, TT_CLAUDE_DIR (~/.claude/projects)
    TT_IDLE_MIN (15)       mezera, která ukončí blok práce
    TT_MIN_BLOCK_MIN (5)   kratší bloky se zahodí
    TT_GAP_MAX_MIN (60)    mezera u stejného zákazníka, která se započítá (čekání, krátké pauzy),
                           pokud ji nevyplňuje práce pro jiného zákazníka / přeskočený adresář
"""
import argparse
import glob
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

HOME = os.path.expanduser("~")
CFG_DIR = os.environ.get("TT_CONFIG_DIR", os.path.join(HOME, ".config/timetrack"))
CLAUDE_DIR = os.environ.get("TT_CLAUDE_DIR", os.path.join(HOME, ".claude/projects"))
IDLE = timedelta(minutes=float(os.environ.get("TT_IDLE_MIN", "15")))
MIN_BLOCK = timedelta(minutes=float(os.environ.get("TT_MIN_BLOCK_MIN", "5")))
EPS = timedelta(minutes=2)
GAP_MAX = timedelta(minutes=float(os.environ.get("TT_GAP_MAX_MIN", "60")))


def _cfg(name: str, env: str, default: str = "") -> str:
    val = os.environ.get(env, "").strip()
    if val:
        return val
    try:
        with open(os.path.join(CFG_DIR, name)) as f:
            return f.read().strip() or default
    except OSError:
        return default


URL = _cfg("url", "TIMETRACK_URL", "http://localhost:8731").rstrip("/")
TOKEN = _cfg("token", "TIMETRACK_TOKEN")


# ---------- dirmap ----------

def load_dirmap() -> list[tuple[str, str]]:
    rules = []
    try:
        with open(os.path.join(CFG_DIR, "dirmap")) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                sep = "->" if "->" in line else "\t"
                if sep not in line:
                    continue
                prefix, customer = (p.strip() for p in line.split(sep, 1))
                rules.append((prefix.rstrip("/"), customer))
    except OSError:
        pass
    return sorted(rules, key=lambda r: -len(r[0]))


def customer_for(cwd: str, rules) -> str | None:
    """Zákazník podle nejdelšího prefixu; None = nemapováno; '__skip__' = ignorovat."""
    for prefix, customer in rules:
        if cwd == prefix or cwd.startswith(prefix + "/"):
            return customer
    return None


# ---------- transkripty ----------

def _local(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)


def read_sessions(day: date):
    """Yield dict(id, title, cwd, first_prompt, events[list of datetime]) pro sessions s aktivitou v daný den."""
    lo = datetime.combine(day, datetime.min.time())
    hi = lo + timedelta(days=1)
    # soubor, který se od půlnoci toho dne nezměnil, nemůže obsahovat událost toho dne
    min_mtime = lo.timestamp()
    for path in glob.glob(os.path.join(CLAUDE_DIR, "*", "*.jsonl")):
        if os.path.getmtime(path) < min_mtime:
            continue
        events, title, cwd, first_prompt = [], "", "", ""
        try:
            with open(path, errors="replace") as f:
                for line in f:
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    t = d.get("type")
                    if t == "custom-title":
                        title = d.get("customTitle") or title
                    elif t in ("user", "assistant") and d.get("timestamp"):
                        ts = _local(d["timestamp"])
                        if lo <= ts < hi:
                            events.append(ts)
                            if t == "user" and d.get("cwd") and not cwd:
                                cwd = d["cwd"]
                            if t == "user" and not first_prompt:
                                c = (d.get("message") or {}).get("content")
                                if isinstance(c, str):
                                    first_prompt = " ".join(c.split())[:90]
        except OSError:
            continue
        if events:
            yield {"id": os.path.basename(path)[:-6], "title": title, "cwd": cwd,
                   "first_prompt": first_prompt, "events": sorted(events)}


def blocks_of(events: list[datetime]) -> list[tuple[datetime, datetime]]:
    blocks, start, prev = [], events[0], events[0]
    for ts in events[1:]:
        if ts - prev > IDLE:
            blocks.append((start, prev))
            start = ts
        prev = ts
    blocks.append((start, prev))
    return blocks


def merge(blocks):
    """Sloučí překrývající se bloky (souběžné sessions u stejného zákazníka)."""
    out = []
    for s, e, labels in sorted(blocks):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e), out[-1][2] + [l for l in labels if l not in out[-1][2]])
        else:
            out.append((s, e, list(labels)))
    return out


def close_gaps(merged, others):
    """Spojí sousední bloky se stejným zákazníkem, když mezera <= GAP_MAX a nepřekrývá ji
    žádný blok jiného zákazníka (`others`: [(start, end)]) – čekání na odpověď/zálohu se počítá."""
    out = []
    for s, e, labels in merged:
        if out:
            ps, pe, pl = out[-1]
            filled = any(os_ < s and oe > pe for os_, oe in others)
            if s - pe <= GAP_MAX and not filled:
                out[-1] = (ps, max(pe, e), pl + [l for l in labels if l not in pl])
                continue
        out.append((s, e, list(labels)))
    return out


# ---------- TimeTrack API ----------

def _request(path: str, data: dict | None = None):
    headers = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(URL + path, data=body, headers=headers)
    return urllib.request.urlopen(req, timeout=10)


def entries_of_day(day: date):
    if not TOKEN:
        return None, "žádný API token (~/.config/timetrack/token) – nelze porovnat se zapsaným"
    try:
        with _request(f"/api/day?day={day.isoformat()}") as r:
            return json.load(r)["entries"], ""
    except (urllib.error.URLError, ValueError, KeyError) as e:
        return None, f"TimeTrack nedostupný ({e})"


# ---------- příkazy ----------

def hours(a: datetime, b: datetime) -> str:
    return f"{(b - a).total_seconds() / 3600:.2f} h"


def parse_day(text: str) -> date:
    return {"today": date.today(), "yesterday": date.today() - timedelta(days=1)}.get(text) \
        or date.fromisoformat(text)


def compute(day: date):
    """-> (rules, {zákazník: [(start, end, [popisky])]}, nemapováno, přeskočeno)."""
    rules = load_dirmap()
    per_customer: dict[str, list] = {}
    unmapped, skipped = [], []
    spans = []  # (zákazník|None, start, end) všech bloků – kvůli kontrole, co vyplňuje mezery
    for s in read_sessions(day):
        label = s["title"] or s["first_prompt"] or s["id"][:8]
        customer = customer_for(s["cwd"], rules)
        bl = blocks_of(s["events"])
        spans.extend((customer, b[0], b[1]) for b in bl)
        if customer == "__skip__":
            skipped.append((label, s["cwd"], bl))
            continue
        if customer is None:
            unmapped.append((label, s["cwd"], bl))
            continue
        per_customer.setdefault(customer, []).extend((b[0], b[1], [label]) for b in bl)
    result = {}
    for customer, bl in per_customer.items():
        others = [(a, b) for c, a, b in spans if c != customer]
        result[customer] = [(s.replace(second=0, microsecond=0), e.replace(second=0, microsecond=0), lab)
                            for s, e, lab in close_gaps(merge(bl), others) if e - s >= MIN_BLOCK]
    return rules, result, unmapped, skipped


def cmd_plan(args):
    day = parse_day(args.day)
    rules, blocks, unmapped, skipped = compute(day)
    print(f"== Čas s Claude Code, {day.isoformat()} (mezera >{IDLE.seconds // 60} min ukončí blok, ale u stejného zákazníka se\n   mezera do {GAP_MAX.seconds // 60} min započítá, pokud ji nevyplňuje práce pro jiného) ==")
    if not rules:
        print("(chybí ~/.config/timetrack/dirmap – všechno je nemapované)")
    for customer, merged in sorted(blocks.items()):
        total = sum(((e - s) for s, e, _ in merged), timedelta())
        print(f"\n{customer}  –  {total.total_seconds() / 3600:.2f} h")
        for s, e, labels in merged:
            print(f"  {s:%H:%M}–{e:%H:%M}  {hours(s, e):>7}  {' | '.join(labels)}")
    for title, items in (("NEMAPOVÁNO (doplň do dirmap)", unmapped), ("přeskočeno (__skip__)", skipped)):
        if items:
            print(f"\n{title}:")
            for label, cwd, bl in items:
                for s, e in bl:
                    print(f"  {s:%H:%M}–{e:%H:%M}  {hours(s, e):>7}  {label}   [{cwd}]")

    entries, why = entries_of_day(day)
    print("\n== Už zapsáno v TimeTracku ==")
    if entries is None:
        print(f"({why})")
    elif not entries:
        print("(nic)")
    else:
        for e in entries:
            print(f"  {e['start_time'][11:16]}–{e['end_time'][11:16]}  {e['customer']}  {e.get('activity', '')}  {e.get('note', '')}")
    print("\n(nic se nezapsalo – `add` a `sync` zapisují jen s --yes)")


def _form(customer, activity, start, end, note):
    return {
        "customer": customer, "activity": activity, "date": f"{start:%Y-%m-%d}",
        "start_h": f"{start:%H}", "start_m": f"{start:%M}", "end_h": f"{end:%H}", "end_m": f"{end:%M}",
        "note": note, "year": start.year, "month": start.month, "filter_customer": "",
    }


def _post(path: str, form: dict):
    if not TOKEN:
        sys.exit("chybí API token (~/.config/timetrack/token)")
    try:
        _request(path, form).close()
    except urllib.error.URLError as e:
        sys.exit(f"zápis selhal: {e}")


def cmd_sync(args):
    day = parse_day(args.day)
    _, blocks, _, _ = compute(day)
    entries, why = entries_of_day(day)
    if entries is None:
        sys.exit(why)
    todo = 0
    for customer, merged in sorted(blocks.items()):
        mine = [e for e in entries if e["customer"] == customer]
        for s, e, labels in merged:
            hit = [x for x in mine if datetime.fromisoformat(x["start_time"]) < e
                   and datetime.fromisoformat(x["end_time"]) > s]
            rng = f"{s:%H:%M}–{e:%H:%M} ({hours(s, e)})"
            if not hit:
                if not (args.activity and args.note):
                    print(f"NOVÝ blok {customer} {rng} [{' | '.join(labels)}] – chybí --activity/--note")
                    todo += 1
                    continue
                print(f"PŘIDAT {customer} {rng}  {args.activity}: {args.note}")
                if args.yes:
                    _post("/entries/add", _form(customer, args.activity, s, e, args.note))
                else:
                    todo += 1
            elif len(hit) > 1:
                print(f"PŘESKOČENO {customer} {rng} – překrývá {len(hit)} záznamů (ručně rozdělené?)")
            else:
                x = hit[0]
                xs, xe = datetime.fromisoformat(x["start_time"]), datetime.fromisoformat(x["end_time"])
                ns = s if s <= xs - EPS else xs  # drobné odchylky (sekundy, zaokrouhlení) neřešíme
                ne = e if e >= xe + EPS else xe
                note = args.note or x.get("note", "")
                activity = args.activity or x.get("activity", "")
                if (ns, ne, note, activity) == (xs, xe, x.get("note", ""), x.get("activity", "")):
                    print(f"beze změny {customer} {xs:%H:%M}–{xe:%H:%M}")
                    continue
                print(f"PRODLOUŽIT {customer} #{x['id']} {xs:%H:%M}–{xe:%H:%M} -> {ns:%H:%M}–{ne:%H:%M} ({hours(ns, ne)})")
                if args.yes:
                    _post(f"/entries/{x['id']}/edit", _form(customer, activity, ns, ne, note))
                else:
                    todo += 1
    if todo and not args.yes:
        print("(dry-run – přidej --yes pro zápis)")
    if not blocks:
        print("(žádné bloky pro zákazníky z dirmap)")


def cmd_add(args):
    start, end = datetime.fromisoformat(args.start), datetime.fromisoformat(args.end)
    if end <= start:
        sys.exit("konec musí být po začátku")
    print(f"{start:%Y-%m-%d} {start:%H:%M}–{end:%H:%M} ({hours(start, end)})  {args.customer} / {args.activity}  {args.note}")
    if not args.yes:
        print("(dry-run – přidej --yes pro zápis)")
        return
    _post("/entries/add", _form(args.customer, args.activity, start, end, args.note))
    print("zapsáno")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("day", nargs="?", default="today")
    p.set_defaults(fn=cmd_plan)
    y = sub.add_parser("sync")
    y.add_argument("day", nargs="?", default="today")
    y.add_argument("--activity", default="")
    y.add_argument("--note", default="")
    y.add_argument("--yes", action="store_true")
    y.set_defaults(fn=cmd_sync)
    a = sub.add_parser("add")
    a.add_argument("--customer", required=True)
    a.add_argument("--activity", required=True)
    a.add_argument("--from", dest="start", required=True)
    a.add_argument("--to", dest="end", required=True)
    a.add_argument("--note", default="")
    a.add_argument("--yes", action="store_true")
    a.set_defaults(fn=cmd_add)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
