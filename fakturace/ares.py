"""ARES lookup — Administrativní registr ekonomických subjektů."""
import httpx

ARES_BASE = "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest"


def _parse_subject(data: dict) -> dict:
    adresa = data.get("sidlo", {})
    domovni = adresa.get("cisloDomovni")
    orientacni = adresa.get("cisloOrientacni")
    cislo = "/".join(filter(None, [str(domovni) if domovni else "", str(orientacni) if orientacni else ""]))
    return {
        "ico": data.get("ico", ""),
        "name": data.get("obchodniJmeno", ""),
        "dic": data.get("dic", ""),
        "street": " ".join(filter(None, [adresa.get("nazevUlice", ""), cislo])),
        "city": adresa.get("nazevObce", ""),
        "zip": str(adresa.get("psc", "") or ""),
    }


def lookup_ico(ico: str) -> dict | None:
    try:
        r = httpx.get(f"{ARES_BASE}/ekonomicke-subjekty/{ico}", timeout=5)
        if r.status_code == 200:
            return _parse_subject(r.json())
    except Exception:
        pass
    return None


def search_name(query: str, limit: int = 10) -> list[dict]:
    try:
        r = httpx.post(
            f"{ARES_BASE}/ekonomicke-subjekty/vyhledat",
            json={"obchodniJmeno": query, "pocet": limit},
            timeout=5,
        )
        if r.status_code == 200:
            items = r.json().get("ekonomickeSubjekty", [])
            return [_parse_subject(s) for s in items]
    except Exception:
        pass
    return []
