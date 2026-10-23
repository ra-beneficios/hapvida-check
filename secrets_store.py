"""Leitura de segredos (credenciais de portal, app Graph) independente da nuvem.

SECRETS_BACKEND=file   (padrao) arquivo JSON {nome: {...}} em SECRETS_FILE (permissao 600, montado so-leitura); nao depende de nuvem
SECRETS_BACKEND=azure  Azure Key Vault; URL em AZURE_KEYVAULT_URL, identidade gerenciada da VM (sem senha)
O Google Secret Manager saiu em 23/10/2026 (saida do GCP; so' o Gemini fica no Google). Copia de referencia: Bitwarden.
O argumento `project` continua nas assinaturas so' por compatibilidade com quem chama; e' ignorado.

Os nomes dos segredos sao os mesmos nos dois lados (ex.: hapvida-portal-saude). Valor = JSON.
"""
import json
import os

_client = None


def _backend() -> str:
    return os.environ.get("SECRETS_BACKEND", "file").lower()


def _get_client():
    global _client
    if _client is None:
        if _backend() == "file":
            path = os.environ["SECRETS_FILE"]
            if os.stat(path).st_mode & 0o077:
                raise PermissionError(f"{path} legivel por grupo/outros; esperado 600")
            with open(path, encoding="utf-8") as f:
                _client = json.load(f)
        elif _backend() == "azure":
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient

            _client = SecretClient(vault_url=os.environ["AZURE_KEYVAULT_URL"], credential=DefaultAzureCredential())
        else:
            raise ValueError(f"SECRETS_BACKEND={_backend()} nao existe mais (use file ou azure)")
    return _client


def get_json(secret_id: str, project: str | None = None) -> dict:
    client = _get_client()
    if _backend() == "file":
        return client[secret_id]
    return json.loads(client.get_secret(secret_id).value)


def list_ids(prefix: str = "", project: str | None = None) -> list[str]:
    client = _get_client()
    if _backend() == "file":
        return [i for i in client if i.startswith(prefix)]
    return [p.name for p in client.list_properties_of_secrets() if p.name.startswith(prefix)]
