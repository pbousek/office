# TimeTrack

Jednoduchá lokální evidence odpracovaného času. Žádný cloud, žádné předplatné,
žádné omezování free tieru — data jsou v SQLite na tvém disku.

## Co to umí

- zápis záznamu: zákazník, činnost, čas od–do, nepovinná poznámka
- editace a mazání záznamů
- filtrování podle měsíce/roku a podle zákazníka
- export měsíčního souhrnu do PDF (rozdělené po zákaznících, mezisoučty,
  celkový součet), s plnou podporou české diakritiky

## Spuštění

```bash
pip install -r requirements.txt --break-system-packages   # nebo do venv
python3 app.py
```

Appka poběží na `http://localhost:8731`. Data se ukládají do `data/timetrack.db`
(vytvoří se automaticky při prvním spuštění).

## Autostart (systemd)

Pro spuštění na pozadí bez terminálu — jednou nainstalovat:

```bash
mkdir -p ~/.config/systemd/user
cp timetrack.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now timetrack
```

Appka se pak spustí automaticky při každém přihlášení. Užitečné příkazy:

```bash
systemctl --user status timetrack     # stav
systemctl --user restart timetrack    # restart po aktualizaci kódu
systemctl --user stop timetrack       # zastavit
journalctl --user -u timetrack -f     # logy živě
```

## Provoz

Appka je navržená jako lokální nástroj — žádná autentizace, žádný HTTPS,
poslouchá jen na `127.0.0.1`. Pokud bys ji chtěl později hostit i pro přístup
z mobilu/jiného stroje, je potřeba doplnit alespoň basic auth a poslouchat na
`0.0.0.0` nebo to schovat za nginx s autentizací — momentálně to není řešeno,
protože jde o čistě lokální nástroj.

## Backup

Celá appka je jeden SQLite soubor (`data/timetrack.db`). Stačí ho zálohovat
běžným způsobem (kopiya, rsync, cokoliv co už používáš).

## Struktura

- `app.py` — FastAPI routy
- `db.py` — SQLite vrstva (žádný ORM, čisté SQL)
- `pdf_export.py` — generování měsíčního PDF reportu (reportlab)
- `templates/` — Jinja2 šablony (index, edit)
- `static/style.css` — styly
