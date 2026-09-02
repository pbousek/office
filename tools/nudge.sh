#!/usr/bin/env bash
# TimeTrack upomínky — spouští se z cronu.
#
#   nudge.sh ask       hodinová: "na čem právě děláš?" (přeskočí, když timer běží)
#   nudge.sh evening    večerní bilance: spustí reconcile.py za dnešek
#
# Příklad crontab (crontab -e):
#   0 9-17 * * 1-5  /CESTA/K/office/tools/nudge.sh ask
#   30 18  * * 1-5  /CESTA/K/office/tools/nudge.sh evening
set -u

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
URL="${TIMETRACK_URL:-http://localhost:8731}"
NTFY="${TT_NTFY_URL:-}"

# Aby notify-send fungoval z cronu (bez přihlášené session):
export DISPLAY="${DISPLAY:-:0}"
if [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ]; then
  export DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u)/bus"
fi

notify() {  # title, body
  command -v notify-send >/dev/null 2>&1 && notify-send -i appointment-soon "$1" "$2"
  [ -n "$NTFY" ] && printf '%s' "$2" | curl -fsS -H "Title: $1" -H "Tags: hourglass" -d @- "$NTFY" >/dev/null 2>&1
  return 0
}

timer_running() {
  curl -fsS --max-time 4 "$URL/widget/state" 2>/dev/null | grep -q '"state": *"running"'
}

case "${1:-ask}" in
  ask)
    timer_running && exit 0
    notify "TimeTrack: co právě děláš?" "Zapiš si to → $URL/widget"
    ;;
  evening)
    report="$(python3 "$DIR/reconcile.py" today 2>&1)"
    echo "$report"
    notify "TimeTrack — večerní bilance" "$report"
    ;;
  *)
    echo "použití: $0 {ask|evening}" >&2
    exit 2
    ;;
esac
