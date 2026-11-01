"""Baixa falsa de comissão (achado 2 da auditoria de 01/11/2026; incidente de 21/08): página inválida do portal nunca
pode virar "pago". Sem portal e sem banco: cursor falso e, para o HTTP 404, Chromium de verdade num servidor local."""
import http.server
import os
import sys
import threading
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import scraper  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

falhas = 0
HOJE = date(2026, 11, 1)
ERRO = getattr(scraper, "PortalConsultaError", ())


def ok(cond, msg):
    global falhas
    print(("ok    " if cond else "FALHOU ") + msg, flush=True)
    falhas += 0 if cond else 1


CAB = "<tr><th>Pedido</th><th>Lote</th><th>Integração</th><th>Valor</th></tr>"
LINHA = "<tr><td>9105876071</td><td>250648</td><td>26/10/2026</td><td>384,30</td></tr>"
TOTAL = "<tr><td>Total</td><td></td><td></td><td>384,30</td></tr>"


def pagina(linhas):
    return f"<html><body><table>{CAB}{linhas}</table></body></html>"


class CursorFalso:
    def __init__(self, abertos):
        self.abertos, self.sql = abertos, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.sql.append(sql)

    def fetchall(self):
        return self.abertos


class ConexaoFalsa:
    def __init__(self, abertos):
        self.c, self.rollbacks, self.commits = CursorFalso(abertos), 0, 0

    def cursor(self):
        return self.c

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass

    def baixas(self):
        return [s for s in self.c.sql if "status = 'pago'" in s]


def rodar(rows, abertos):
    """Devolve (conexão, erro levantado)."""
    conn = ConexaoFalsa(abertos)
    try:
        scraper.upsert_pedidos(conn, "128A", 2026, HOJE, rows)
        return conn, None
    except Exception as e:
        return conn, e


def levanta(html):
    try:
        scraper.parse_table(html)
        return False
    except Exception as e:
        return bool(ERRO) and isinstance(e, ERRO)


# Leitura da página
ok(not scraper.parse_table("<html><body>404 Not Found</body></html>"), "página sem tabela não traz lote")
ok(scraper.parse_table("<html><body>404 Not Found</body></html>") is None, "página sem tabela é diferente de lista vazia")
ok(scraper.parse_table(pagina("")) == [], "tabela só com cabeçalho é lista vazia de verdade")
ok(len(scraper.parse_table(pagina(LINHA + TOTAL))) == 1, "linha de total não é lote nem erro")
ok(levanta(pagina("<tr><td>9105876071</td><td>250648</td><td>2026-10-26</td><td>384,30</td></tr>")),
   "lote com data em formato novo é erro, não lote sumido")
ok(levanta(pagina("<tr><td>9105876071</td><td>250648</td><td>26/10/2026</td><td>sem valor</td></tr>")),
   "lote com valor ilegível é erro, não lote sumido")
ok(levanta("<table><tr><th>Pedido</th><th>Lote</th><th>Valor</th></tr></table>"), "cabeçalho sem coluna obrigatória é erro")

# Baixa
conn, erro = rodar(scraper.parse_table("<html><body>404 Not Found</body></html>"), [("9105876071", "250648")])
ok(not conn.baixas(), "INCIDENTE 21/08: página inválida com lote aberto não baixa")
ok(bool(ERRO) and isinstance(erro, ERRO), "página inválida com lote aberto vira erro (e-mail ao André pelo n8n)")
conn, erro = rodar(scraper.parse_table("<html><body>sem tabela</body></html>"), [])
ok(erro is None and not conn.c.sql[1:], "código sem lote aberto e página sem tabela: sem alarme diário e sem gravar")
conn, erro = rodar([], [("9105876071", "250648")])
ok(erro is None and len(conn.baixas()) == 1, "tabela confirmada e lote fora dela continua dando baixa")
conn, erro = rodar(scraper.parse_table(pagina(LINHA)), [("9105876071", "250648")])
ok(erro is None and not conn.baixas(), "lote ainda listado não baixa")


# HTTP de verdade
class Servidor(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        nao_achou = "falta" in self.path
        corpo = b"<html>Not Found</html>" if nao_achou else pagina(LINHA).encode()
        self.send_response(404 if nao_achou else 200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, *a):
        pass


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Servidor)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_port}"
modelo = scraper.CONSULTA_URL_TEMPLATE
with sync_playwright() as pw:
    nav = pw.chromium.launch()
    pag = nav.new_page()
    adapter = scraper.HapvidaAdapter(pag)
    scraper.CONSULTA_URL_TEMPLATE = base + "/falta?s={sessao}&a={ano}"
    try:
        r = adapter.fetch_open_invoices("x", 2026)
        ok(False, f"HTTP 404 do portal é erro (devolveu {r!r})")
    except Exception as e:
        ok(bool(ERRO) and isinstance(e, ERRO), "HTTP 404 do portal é erro")
    scraper.CONSULTA_URL_TEMPLATE = base + "/ok?s={sessao}&a={ano}"
    ok(len(adapter.fetch_open_invoices("x", 2026)) == 1, "HTTP 200 com tabela traz o lote")
    nav.close()
scraper.CONSULTA_URL_TEMPLATE = modelo
srv.shutdown()


# run_all: um ano ruim não derruba o outro e o erro chega a quem avisa o André
class AdapterFalso:
    def __init__(self, page):
        self.anos = []

    def login(self, u, s):
        return "s"

    def fetch_open_invoices(self, sessao, ano):
        self.anos.append(ano)
        if ano == HOJE.year and ERRO:
            raise ERRO("página inválida")
        return []


class Navegador:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __getattr__(self, nome):
        return lambda *a, **k: self

    @property
    def chromium(self):
        return self


class ColetaFalsa:
    @staticmethod
    def meses_recentes(hoje):
        return []

    @staticmethod
    def coletar(*a):
        return {"erros": []}


conn = ConexaoFalsa([])
guarda = {n: getattr(scraper, n) for n in ("HapvidaAdapter", "sync_playwright", "list_concessionaria_secrets",
                                           "get_credentials", "build_summary", "registrar_fonte_atualizacao", "date")}


class DataFixa(date):
    @classmethod
    def today(cls):
        return HOJE


scraper.HapvidaAdapter, scraper.sync_playwright = AdapterFalso, lambda: Navegador()
scraper.list_concessionaria_secrets = lambda p: [("128A", "hapvida-128a")]
scraper.get_credentials = lambda p, s: {"usuario": "u", "senha": "s"}
scraper.build_summary = lambda c, h: {"novos": [], "atrasados": []}
scraper.registrar_fonte_atualizacao = lambda: None
scraper.date = DataFixa
scraper.psycopg2.connect = lambda **k: conn
sys.modules["comissao_detalhe"] = ColetaFalsa
os.environ.setdefault("GCP_PROJECT", "teste")
try:
    res = scraper.run_all()
    erros = res.get("erros", [])
    ok(any(e.get("ano") == HOJE.year and "128A" == e.get("codigo") for e in erros), "run_all devolve o erro com código e ano")
    ok(conn.rollbacks >= 1, "run_all desfaz a transação do ano com erro")
finally:
    for n, v in guarda.items():
        setattr(scraper, n, v)

print(f'{{"falhas": {falhas}}}', flush=True)
sys.exit(1 if falhas else 0)
