# Fakturace

Jednoduchá lokální fakturace. Žádný cloud, žádné předplatné, data jsou v SQLite na tvém disku.

## Co to umí

- evidence zákazníků s vyhledáváním v ARES (IČO nebo název → předvyplní formulář)
- vytváření faktur: položky, datum, splatnost, měna, poznámka
- podpora plátce i neplátce DPH (sazby 0 %, 12 %, 21 %)
- stavy faktury: Návrh → Odesláno → Zaplaceno / Storno
- export do PDF (s QR platbou ve formátu SPD)
- export do ISDOCX (ZIP s ISDOC 6.0.2 XML + PDF — kompatibilní s Pohodou, Money S3 atd.)
- import záznamů z TimeTracku — vyber zákazníka a období, záznamy se předvyplní jako položky faktury
- statistiky: vyfakturováno/zaplaceno po měsících a zákaznících, přehled roku

## Spuštění

```bash
pip install -r requirements.txt --break-system-packages   # nebo do venv
python3 app.py
```

Appka poběží na `http://localhost:8732`. Data se ukládají do `data/fakturace.db`
(vytvoří se automaticky při prvním spuštění).

## Autostart (systemd)

Jednou nainstalovat:

```bash
mkdir -p ~/.config/systemd/user
cp fakturace.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now fakturace
```

Příkazy pro správu:

```bash
systemctl --user status fakturace     # stav
systemctl --user restart fakturace    # restart po aktualizaci kódu
systemctl --user stop fakturace       # zastavit
journalctl --user -u fakturace -f     # logy živě
```

## První spuštění

1. Otevři `http://localhost:8732/settings` a vyplň údaje své firmy (název, IČO, DIČ, adresa, IBAN)
   — IČO nebo název lze doplnit automaticky z ARES
2. Přidej zákazníky přes `http://localhost:8732/customers`
3. Vytvoř první fakturu přes **+ Nová faktura**

## Propojení s TimeTrackem

Fakturace čte TimeTrack databázi read-only z `../timetrack/data/timetrack.db`.
Při tvorbě faktury se zobrazí sekce **Import ze záznamu z TimeTracku** — vyber zákazníka
a období, záznamy se načtou a přidají jako položky faktury (hodiny × sazba).

## Struktura

- `app.py` — FastAPI routy (port 8732)
- `db.py` — SQLite vrstva (settings, customers, invoices, invoice_items)
- `ares.py` — vyhledávání v ARES
- `timetrack.py` — read-only přístup k TimeTrack DB
- `pdf_export.py` — generování PDF s QR platbou (reportlab)
- `isdoc_export.py` — generování ISDOCX (ISDOC 6.0.2 + PDF v ZIPu)
- `templates/` — Jinja2 šablony
- `static/style.css` — styly

## Backup

Celá appka je jeden SQLite soubor (`data/fakturace.db`). Stačí ho zálohovat běžným způsobem.
