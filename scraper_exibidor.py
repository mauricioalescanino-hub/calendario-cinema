"""
scraper_exibidor.py
───────────────────
Raspa todos os lançamentos do exibidor.com.br dos próximos 6 meses
e gera o arquivo filmes.json para o Calendário de Cinema.

INSTALAÇÃO (rode uma vez):
    pip install playwright beautifulsoup4
    playwright install chromium

USO:
    python scraper_exibidor.py

O arquivo filmes.json será criado na mesma pasta.
Depois é só subir filmes.json junto com index.html no GitHub.
"""

import json
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    print("❌  Playwright não instalado. Rode:")
    print("    pip install playwright")
    print("    playwright install chromium")
    sys.exit(1)

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("❌  BeautifulSoup não instalado. Rode:")
    print("    pip install beautifulsoup4")
    sys.exit(1)

# ── Configurações ────────────────────────────────────────────────
BASE_URL   = "https://www.exibidor.com.br"
HOJE       = datetime.today()
LIMITE     = HOJE + timedelta(days=185)   # ~6 meses
MAX_FICHAS = 80                            # máximo de fichas a visitar
DELAY_MS   = 600                           # pausa entre fichas (ms)
OUTPUT     = Path("filmes.json")
# ────────────────────────────────────────────────────────────────


def log(msg, emoji="▸"):
    print(f"{emoji}  {msg}", flush=True)


def extrair_data(texto):
    """Converte dd/mm/yyyy → yyyy-mm-dd."""
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", texto)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return None


def limpar(texto):
    if not texto:
        return ""
    return re.sub(r"\s+", " ", texto.replace("\xa0", " ")).strip()


def scrape():
    filmes = []

    with sync_playwright() as p:
        log("Abrindo browser...", "🌐")
        browser = p.chromium.launch(headless=True)
        ctx     = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0.0.0 Safari/537.36",
            locale="pt-BR",
        )
        page = ctx.new_page()

        # ── 1. Abre calendário e espera carregar ─────────────────
        log("Carregando exibidor.com.br/lancamentos/ ...", "📅")
        page.goto(f"{BASE_URL}/lancamentos/", wait_until="domcontentloaded")

        # Espera os cards de filme aparecerem (JS assíncrono)
        try:
            page.wait_for_selector("a[href*='/filme/']", timeout=15000)
        except PWTimeout:
            log("Timeout esperando os filmes carregarem.", "⚠️")

        # Scroll pra forçar lazy load
        for _ in range(6):
            page.keyboard.press("End")
            page.wait_for_timeout(800)

        html_cal = page.content()

        # ── 2. Extrai links de fichas técnicas ───────────────────
        soup_cal = BeautifulSoup(html_cal, "html.parser")
        links_raw = soup_cal.find_all("a", href=re.compile(r"/filme/\d+/"))
        hrefs_vistos = set()
        fichas = []

        for a in links_raw:
            href = a.get("href", "")
            if not href.startswith("http"):
                href = BASE_URL + href
            m = re.search(r"/filme/(\d+)/", href)
            if m and m.group(1) not in hrefs_vistos:
                hrefs_vistos.add(m.group(1))
                fichas.append({"id": m.group(1), "url": href.split("?")[0]})

        log(f"{len(fichas)} fichas encontradas no calendário.", "✅")

        if not fichas:
            log("Nenhuma ficha encontrada — o site pode ter mudado a estrutura.", "❌")
            browser.close()
            return []

        # ── 3. Visita cada ficha e extrai dados ──────────────────
        total = min(len(fichas), MAX_FICHAS)
        log(f"Lendo {total} fichas técnicas...\n", "🎬")

        for i, ficha in enumerate(fichas[:total]):
            try:
                page.goto(ficha["url"], wait_until="domcontentloaded", timeout=12000)
                page.wait_for_timeout(DELAY_MS)
                html_f = page.content()
            except PWTimeout:
                log(f"  [{i+1}/{total}] Timeout: {ficha['url']}", "⚠️")
                continue

            soup = BeautifulSoup(html_f, "html.parser")

            # Título — pega o H1 do conteúdo, não o do header/nav
            titulo = ""
            for h1 in soup.find_all("h1"):
                txt = limpar(h1.get_text())
                # ignora H1s do menu/header (muito curtos ou com "Exibidor")
                if txt and txt.lower() not in ("exibidor", "") and len(txt) > 2:
                    titulo = txt
                    break
            # fallback: tenta pegar via title da página
            if not titulo or titulo.lower() == "exibidor":
                title_tag = soup.find("title")
                if title_tag:
                    titulo = limpar(title_tag.get_text().split("|")[0].split("-")[0]).strip()
            if not titulo or titulo.lower() == "exibidor":
                continue

            # Data de estreia
            estreia_str = None
            for tag in soup.find_all(["h3", "h4", "p", "li", "strong", "span"]):
                txt = tag.get_text()
                d = extrair_data(txt)
                if d:
                    estreia_str = d
                    break

            if not estreia_str:
                continue

            # Filtra janela de 6 meses
            try:
                dt = datetime.strptime(estreia_str, "%Y-%m-%d")
            except ValueError:
                continue

            if dt < HOJE or dt > LIMITE:
                continue

            # Distribuidora
            distribuidora = ""
            for label in soup.find_all(string=re.compile(r"Distribuidor", re.I)):
                parent = label.find_parent()
                if parent:
                    nxt = parent.find_next_sibling()
                    if nxt:
                        distribuidora = limpar(nxt.get_text())[:50]
                        break
            if not distribuidora:
                # tenta via li próximo
                for li in soup.find_all("li"):
                    txt = li.get_text()
                    if re.search(r"distribuidor", txt, re.I):
                        distribuidora = limpar(txt.split(":")[-1])[:50]
                        break

            # Gênero
            genero = ""
            for label in soup.find_all(string=re.compile(r"Gênero|Genero", re.I)):
                parent = label.find_parent()
                if parent:
                    nxt = parent.find_next_sibling()
                    if nxt:
                        genero = limpar(nxt.get_text())[:40]
                        break
            if not genero:
                for li in soup.find_all("li"):
                    txt = li.get_text()
                    if re.search(r"gênero|genero", txt, re.I):
                        genero = limpar(txt.split(":")[-1])[:40]
                        break

            # Sinopse
            sinopse = ""
            sinopse_h = soup.find(string=re.compile(r"Sinopse", re.I))
            if sinopse_h:
                parent = sinopse_h.find_parent()
                if parent:
                    nxt = parent.find_next_sibling()
                    if nxt:
                        sinopse = limpar(nxt.get_text())[:200]

            # Poster (padrão claquete.com)
            poster_url = f"https://www.claquete.com/fotos/filmes/poster/{ficha['id']}_medio.jpg"
            # tenta encontrar poster real na página
            img = soup.find("img", src=re.compile(r"claquete\.com.*poster"))
            if img:
                src = img.get("src", "")
                if src.startswith("http"):
                    poster_url = src

            filme = {
                "id":           int(ficha["id"]),
                "titulo":       titulo,
                "estreia":      estreia_str,
                "distribuidora": distribuidora,
                "genero":       genero,
                "sinopse":      sinopse,
                "poster":       poster_url,
                "urlExibidor":  ficha["url"],
            }
            filmes.append(filme)

            status = "✅" if distribuidora else "◻️"
            print(f"  {status} [{i+1}/{total}] {titulo} ({estreia_str})")

        browser.close()

    # Ordena por data
    filmes.sort(key=lambda f: f["estreia"])
    return filmes


def main():
    print("=" * 55)
    print("  Scraper Exibidor → filmes.json")
    print(f"  Período: {HOJE.strftime('%d/%m/%Y')} → {LIMITE.strftime('%d/%m/%Y')}")
    print("=" * 55 + "\n")

    filmes = scrape()

    if not filmes:
        log("Nenhum filme encontrado. Verifique sua conexão.", "❌")
        sys.exit(1)

    # Salva JSON
    output = {
        "gerado_em": HOJE.strftime("%Y-%m-%d %H:%M"),
        "total":     len(filmes),
        "filmes":    filmes,
    }
    OUTPUT.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{'=' * 55}")
    log(f"{len(filmes)} filmes salvos em {OUTPUT}", "🎉")
    log("Agora suba o filmes.json junto com o index.html no GitHub.", "📤")
    print("=" * 55)


if __name__ == "__main__":
    main()
