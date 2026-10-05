FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

# Achado 31/07: TZ=America/Sao_Paulo já vinha no docker-compose, mas essa imagem não
# tem o pacote tzdata — o glibc não resolvia o fuso e caía em UTC silenciosamente
# (date mostrava "America" no lugar do fuso, sinal claro de zoneinfo ausente).
# DEBIAN_FRONTEND=noninteractive evita o prompt do debconf (Geographic area?) que
# travava o build esperando input que nunca vem num `docker build`.
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py scraper.py censo_legado.py censo_legado_parser.py censo_saude.py copart_saude.py boletos_saude.py documentos_saude.py documentos_legado.py enviar_documentos.py auto_commit.py bulk_import_beneficiarios_oficial.py movimentacao_saude.py secrets_store.py verificar_acompanhamentos.py faturamento_mensal.py padrao_faturamento.py envio_faturamento.py exclusao_api.py ./

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
