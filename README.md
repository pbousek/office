# office

Dva lokální nástroje pro OSVČ: evidence času a fakturace. Žádný cloud, žádné předplatné — data v SQLite.

- **[TimeTrack](timetrack/)** — zápis odpracovaného času, stopky, export do PDF
- **[Fakturace](fakturace/)** — faktury, PDF/ISDOCX export, import z TimeTracku, párování plateb
- **[tools/](tools/)** — stopky jako okno vždy navrchu + záchranné sítě proti zapomínání (logování adresářů/ssh, večerní rekonciliace, upomínky)

## Spuštění přes Docker Compose

```bash
git clone https://github.com/pbousek/office.git
cd office
docker compose up --build -d
```

- TimeTrack: `http://localhost:8731`
- Fakturace: `http://localhost:8732`

Data jsou v pojmenovaných Docker volumes (`timetrack_data`, `fakturace_data`) a přežijí restart i rebuild.

## Aktualizace

```bash
git pull
docker compose up --build -d
```

## Přihlášení a uživatelé

Obě appky sdílejí uživatelské účty (`office_auth/`, SQLite `auth.db` ve volume `auth_data`).

- Při prvním startu vznikne účet **`admin` / `admin`** (nebo heslo z env `OFFICE_ADMIN_PASSWORD`) a po přihlášení si **musí změnit heslo**. Jméno jde v *Uživatelé* přejmenovat.
- Admin v *Uživatelé* zakládá další účty a u každého zaškrtává, do které appky smí (TimeTrack / Fakturace) a jestli je admin. Nový uživatel si heslo při prvním přihlášení mění.
- Záznamy času a stopky jsou **per uživatel**; zákazníci a činnosti jsou společné. Fakturace (billing, import z TT) počítá s hodinami všech uživatelů.
- Ve Fakturaci je *Nastavení* (firma, SMTP) jen pro adminy.
- Přihlášení drží 30 dní (session cookie, prodlužuje se používáním). Po 10 neúspěšných pokusech z jedné IP se přihlášení na 15 minut zablokuje.
- Skripty v `tools/` se autentizují API tokenem (👤 účet → API token, env `TIMETRACK_TOKEN`).

Data jedné instance jsou společná pro všechny její uživatele (jedna firma, jedna fakturační řada). Pro jinou firmu spusť samostatnou instanci, tj. vlastní compose projekt a porty:

```bash
docker compose -p office-pepa up --build -d   # porty uprav v kopii docker-compose.yml
```

## Lokální spuštění bez Dockeru

Viz README v jednotlivých složkách: [timetrack/README.md](timetrack/README.md), [fakturace/README.md](fakturace/README.md).
