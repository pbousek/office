# tools/ — usnadnění a záchranné sítě pro TimeTrack

Cíl: přestat zapomínat vykazovat čas. Tři vrstvy:

1. **Widget se stopkami** — malé okno vždy navrchu, START/STOP/pauza.
2. **Pasivní logy** — shell zaznamenává adresáře a ssh session.
3. **Rekonciliace + upomínky** — večer porovná logy se zapsaným a upozorní na díry.

Widget a logy se nevidí navzájem; rekonciliace je jen kontrola, nic nepřepisuje.

---

## 1. Widget se stopkami

V TimeTracku je na `/widget`. Jako samostatné okno:

```bash
cd timetrack
pip install -r requirements-desktop.txt      # pywebview (+ GTK/Qt backend)
python3 desktop_widget.py
```

- **+ nový timer** — vyber zákazníka (činnost a popisek nepovinné), běží čas.
- **Víc timerů běží zároveň** — nic se automaticky neparkuje. Když u jednoho
  klienta hlídáš deploy a vedle děláš pro druhého, běží obě stopky; hlavička
  ukáže „souběžný běh (N stopky)".
- **Pauza / Pokračovat** — když práci fakt přerušíš (oběd, call); nasčítaný čas
  se neztratí, ostatní běžící stopky pauza neovlivní.
- **Stop** — zapíše záznam (`konec = začátek + odpracováno`, pauzy vystřižené).
  Souběžné stopky tak dají záznamy, které se v čase překrývají — schválně.
- **×** — zahodit timer bez uložení.

Autostart přes systemd (uživatelský):

```ini
# ~/.config/systemd/user/timetrack-widget.service
[Unit]
Description=TimeTrack widget
After=graphical-session.target
[Service]
ExecStart=/usr/bin/python3 %h/…/office/timetrack/desktop_widget.py
Restart=on-failure
[Install]
WantedBy=graphical-session.target
```

```bash
systemctl --user enable --now timetrack-widget
```

---

## 2. Pasivní logy

Do `~/.bashrc` (nebo `~/.zshrc`):

```bash
source /CESTA/K/office/tools/shell-logger.sh   # čas + $PWD + příkaz -> shell.log
source /CESTA/K/office/tools/ssh-wrap.sh        # ssh session -> ssh.log
```

Loguje se do `~/.local/share/timetrack/{shell,ssh}.log` (přepíšeš přes `TT_LOG_DIR`).
Jen prostý text, můžeš kdykoli smazat.

Ansible/scp/rsync se řeší přes adresář projektu (běží ze složky) — viz dirmap.

---

## 3. Mapování na zákazníky

```bash
mkdir -p ~/.config/timetrack
cp tools/dirmap.example  ~/.config/timetrack/dirmap
cp tools/hostmap.example ~/.config/timetrack/hostmap
$EDITOR ~/.config/timetrack/dirmap
```

- **dirmap** — `prefix cesty -> zákazník` (nejdelší shoda vyhrává, `__skip__` = ignorovat)
- **hostmap** — `fnmatch vzor -> zákazník` proti ssh cíli
- Jméno zákazníka musí sedět na jméno v TimeTracku (kvůli porovnání se záznamy).

---

## 4. Rekonciliace a upomínky

```bash
python3 tools/reconcile.py            # dnešek: viděno vs. zapsáno po zákaznících
python3 tools/reconcile.py yesterday
```

```
TimeTrack — rekonciliace 2026-09-01

Zákazník                  viděno   zapsáno   rozdíl
----------------------------------------------------
Acme s.r.o.                3.10h     1.50h    +1.60h   <- nezapsáno ~1.6 h
Globex a.s.                0.80h     0.75h    +0.05h
```

Cron (`crontab -e`), cesty uprav:

```cron
0 9-17 * * 1-5  /CESTA/K/office/tools/nudge.sh ask      # "co teď děláš?" (ne když timer běží)
30 18  * * 1-5  /CESTA/K/office/tools/nudge.sh evening   # večerní bilance
```

Notifikace jdou přes `notify-send`; když nastavíš `TT_NTFY_URL` (např. `https://ntfy.sh/moje-tema`),
pošlou se i tam (funguje i na mobil).

### Proměnné prostředí

| proměnná | výchozí | význam |
|---|---|---|
| `TIMETRACK_URL` | `http://localhost:8731` | adresa appky |
| `TIMETRACK_TOKEN` | — | API token (TimeTrack → 👤 účet → API token); nutný, jakmile appka vyžaduje přihlášení |
| `TT_LOG_DIR` | `~/.local/share/timetrack` | kam se loguje |
| `TT_CONFIG_DIR` | `~/.config/timetrack` | dirmap / hostmap |
| `TT_IDLE_MIN` | `15` | mezera, která ukončí blok práce |
| `TT_MIN_BLOCK_MIN` | `5` | kratší bloky ignorovat |
| `TT_GAP_ALERT_MIN` | `30` | od jakého rozdílu (viděno − zapsáno) přijde notifikace |
| `TT_NTFY_URL` | — | ntfy téma pro push |
