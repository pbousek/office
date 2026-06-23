# office

Dva lokální nástroje pro OSVČ: evidence času a fakturace. Žádný cloud, žádné předplatné — data v SQLite.

- **[TimeTrack](timetrack/)** — zápis odpracovaného času, export do PDF
- **[Fakturace](fakturace/)** — faktury, PDF/ISDOCX export, import z TimeTracku, párování plateb

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

## Bezpečnost

Appky nemají autentizaci — jsou navrženy pro provoz jen na localhostu nebo za reverse proxy s vlastní autentizací (nginx, Caddy...). Docker Compose binduje porty na `127.0.0.1`.

## Lokální spuštění bez Dockeru

Viz README v jednotlivých složkách: [timetrack/README.md](timetrack/README.md), [fakturace/README.md](fakturace/README.md).
