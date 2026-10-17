-- Obra 3, fase 1 (17/10/2026): fila única do portal OBSERVANDO — registra cada login e cada reseleção de contrato na
-- conta do sistema, com a origem e quantos outros logins aconteceram nos 3 min antes (sessões disputando a conta).
-- Uma semana disso vira o número de capacidade (logins/dia, pico de simultâneos) antes de passar a enfileirar.
CREATE SCHEMA IF NOT EXISTS portal;
CREATE TABLE IF NOT EXISTS portal.uso (
  id          bigserial PRIMARY KEY,
  quando      timestamptz NOT NULL DEFAULT now(),
  tipo        text NOT NULL,       -- LOGIN | RESELECAO
  contrato    text,
  origem      text,                -- script/rotina que abriu (ex.: verificar_acompanhamentos, run-inclusao, faturamento)
  pid         int,
  simultaneos int                  -- outros logins (de outro processo) nos 3 min anteriores
);
CREATE INDEX IF NOT EXISTS ix_uso_quando ON portal.uso (quando);
