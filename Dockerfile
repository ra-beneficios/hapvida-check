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

# 17/10/2026: todos os .py (antes era uma lista: arquivo novo esquecido só aparecia em produção)
COPY *.py ./

# 17/10/2026 (obra 1): o motor de críticas (service/saneamento + pipeline) e o motor de exclusão vão DENTRO da imagem,
# vindos do repo lifetools pelo contexto "lifetools" (publicar_hapvida_check.sh). Antes eram volumes ao vivo: salvar um
# arquivo mudava o vigia na rodada seguinte, sem teste nem publicação.
COPY --from=lifetools service/saneamento /lifetools_service/saneamento
COPY --from=lifetools service/pipeline /lifetools_service/pipeline
COPY --from=lifetools service/movimentacoes /lifetools_service/movimentacoes
COPY --from=lifetools motor_exclusao /motor/motor_exclusao
COPY tests ./tests

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
