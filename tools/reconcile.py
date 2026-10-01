#!/usr/bin/env python3
"""Rekonciliace: co logy vidí u kterého zákazníka vs. co je zapsáno v TimeTracku.

    python3 reconcile.py              # dnešek
    python3 reconcile.py yesterday
    python3 reconcile.py 2026-08-30

Čte:
    ~/.local/share/timetrack/shell.log   (čas \\t cwd \\t příkaz)      z shell-logger.sh
    ~/.local/share/timetrack/ssh.log     (start \\t end \\t exit \\t cíl) z ssh-wrap.sh
    ~/.config/timetrack/dirmap           (prefix -> zákazník)
    ~/.config/timetrack/hostmap          (vzor -> zákazník)

Proměnné prostředí:
    TT_LOG_DIR, TT_CONFIG_DIR, TIMETRACK_URL
    TIMETRACK_TOKEN       API token z TimeTracku (👤 účet → API token)
    TT_IDLE_MIN (15)      mezera, která ukončí blok práce
    TT_MIN_BLOCK_MIN (5)  kratší bloky ignorovat
    TT_GAP_ALERT_MIN (30) o kolik musí "viděno" převýšit "zapsáno" pro notifikaci
    TT_NTFY_URL           když nastaveno, pošle sem shrnutí (HTTP POST)
"""
import fnmatch
import json
import os
import subprocess
import sys
import urllib.request
from datetime import date, datetime, time, timedelta

HOME = os.path.expanduser("~")
LOG_DIR = os.environ.get("TT_LOG_DIR", os.path.join(HOME, ".local/share/timetrack"))
CFG_DIR = os.environ.get("TT_CONFIG_DIR", os.path.join(HOME, ".config/timetrack"))
URL = os.environ.get("TIMETRACK_URL", "http://localhost:8731").rstrip("/")
TOKEN = os.environ.get("TIMETRACK_TOKEN", "").strip()
IDLE = timedelta(minutes=float(os.environ.get("TT_IDLE_MIN", "15")))
MIN_BLOCK = timedelta(minutes=float(os.environ.get("TT_MIN_BLOCK_MIN", "5")))
GAP_ALERT = float(os.environ.get("TT_GAP_ALERT_MIN", "30")) / 60.0
NTFY = os.environ.get("TT_NTFY_URL", "").strip()
SSH_MAX = timedelta(hours=16)  # delší ssh session = zapomenutá, ořízni


def parse_day(arg: str | None) -> date:
    if not arg or arg == "today":
        return date.today()
    if arg == "yesterday":
        return date.today() - timedelta(days=1)
    return date.fromisoformat(arg)


def load_map(path: str) -> list[tuple[str, str]]:
    rules = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "->" in line:
                    k, v = line.split("->", 1)
                elif "\t" in line:
                    k, v = line.split("\t", 1)
                else:
                    continue
                rules.append((k.strip(), v.strip()))
    except FileNotFoundError:
        pass
    return rules


def map_dir(path: str, rules: list[tuple[str, str]]) -> str | None:
    best = None
    for prefix, cust in rules:
        p = os.path.expanduser(prefix).rstrip("/")
        if (path == p or path.startswith(p + "/")) and (best is None or len(p) > best[0]):
            best = (len(p), cust)
    return best[1] if best else None


def map_host(target: str, rules: list[tuple[str, str]]) -> str | None:
    host = target
    for tok in target.split():
        if "@" in tok:
            host = tok.split("@", 1)[1]
            break
        if tok and not tok.startswith("-"):
            host = tok
            break
    for pat, cust in rules:
        if fnmatch.fnmatch(target, pat) or fnmatch.fnmatch(host, pat):
            return cust
    return None


def parse_ts(s: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(s.strip())
    except ValueError:
        return None
    if dt.tzinfo is not None:  # 'date -Iseconds' píše offset — převeď na lokální čas bez tz
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def blocks(times: list[datetime]) -> list[tuple[datetime, datetime]]:
    times = sorted(times)
    if not times:
        return []
    out = []
    start = end = times[0]
    for t in times[1:]:
        if t - end <= IDLE:
            end = t
        else:
            out.append((start, end))
            start = end = t
    out.append((start, end))
    return out


def notify(title: str, body: str):
    try:
        subprocess.run(["notify-send", "-i", "appointment-soon", title, body],
                       check=False, timeout=5)
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        pass
    if NTFY:
        try:
            req = urllib.request.Request(
                NTFY, data=body.encode("utf-8"),
                headers={"Title": title.encode("ascii", "replace").decode(), "Tags": "hourglass"})
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass


def main():
    day = parse_day(sys.argv[1] if len(sys.argv) > 1 else None)
    day_start = datetime.combine(day, time.min)
    day_end = day_start + timedelta(days=1)

    dirmap = load_map(os.path.join(CFG_DIR, "dirmap"))
    hostmap = load_map(os.path.join(CFG_DIR, "hostmap"))

    samples: dict[str, list[datetime]] = {}

    def add(cust: str | None, ts: datetime):
        if cust and cust != "__skip__" and day_start <= ts < day_end:
            samples.setdefault(cust, []).append(ts)

    try:
        with open(os.path.join(LOG_DIR, "shell.log"), encoding="utf-8", errors="replace") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 2:
                    continue
                ts = parse_ts(parts[0])
                if ts:
                    add(map_dir(parts[1], dirmap), ts)
    except FileNotFoundError:
        pass

    try:
        with open(os.path.join(LOG_DIR, "ssh.log"), encoding="utf-8", errors="replace") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 4:
                    continue
                a, b = parse_ts(parts[0]), parse_ts(parts[1])
                if not a or not b or b < a:
                    continue
                b = min(b, a + SSH_MAX)
                cust = map_host(parts[3], hostmap)
                if not cust or cust == "__skip__":
                    continue
                t = a
                while t <= b:
                    add(cust, t)
                    t += timedelta(minutes=1)
    except FileNotFoundError:
        pass

    seen: dict[str, float] = {}
    for cust, times in samples.items():
        total = sum((e - s for s, e in blocks(times) if e - s >= MIN_BLOCK), timedelta())
        if total > timedelta():
            seen[cust] = total.total_seconds() / 3600.0

    logged: dict[str, float] = {}
    api_ok = True
    try:
        req = urllib.request.Request(f"{URL}/api/day?day={day.isoformat()}",
                                     headers={"Authorization": f"Bearer {TOKEN}"} if TOKEN else {})
        with urllib.request.urlopen(req, timeout=5) as r:
            logged = {k: float(v) for k, v in json.load(r).get("by_customer", {}).items()}
    except Exception:
        api_ok = False

    custs = sorted(set(seen) | set(logged),
                   key=lambda c: seen.get(c, 0) - logged.get(c, 0), reverse=True)

    out = [f"TimeTrack — rekonciliace {day.isoformat()}", "",
           f"{'Zákazník':<24}{'viděno':>9}{'zapsáno':>10}{'rozdíl':>9}",
           "-" * 52]
    alerts = []
    for c in custs:
        sv, lg = seen.get(c, 0.0), logged.get(c, 0.0)
        lg_txt = f"{lg:>8.2f}h" if api_ok else "     ?  "
        out.append(f"{c[:24]:<24}{sv:>8.2f}h{lg_txt:>10}{sv - lg:>+8.2f}h")
        if api_ok and sv - lg >= GAP_ALERT:
            alerts.append((c, sv - lg))
    if not custs:
        out.append("(žádná aktivita podle logů — máš vyplněný dirmap/hostmap?)")
    if custs:
        tot_seen = sum(seen.values())
        tot_log = sum(logged.values())
        out.append("-" * 52)
        out.append(f"{'celkem':<24}{tot_seen:>8.2f}h"
                   + (f"{tot_log:>9.2f}h" if api_ok else "     ?  "))
        if api_ok and tot_log - tot_seen >= 0.5:
            out.append(f"(zapsáno je o {tot_log - tot_seen:.1f} h víc než hodin u počítače "
                       f"— souběžně účtovaný čas, ok pokud to tak chceš)")
    if not api_ok:
        out += ["", f"! TimeTrack API nedostupné ({URL}) — sloupec 'zapsáno' chybí."]

    print("\n".join(out))

    if alerts:
        notify("TimeTrack: nezapsané hodiny?",
               "\n".join(f"{c}: chybí ~{d:.1f} h" for c, d in alerts) + f"\n{URL}/widget")


if __name__ == "__main__":
    main()
