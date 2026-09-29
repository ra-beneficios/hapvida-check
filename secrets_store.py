"""Leitura de segredos (credenciais de portal, app Graph) independente da nuvem.

SECRETS_BACKEND=gcp    (padrao) Google Secret Manager; projeto em GCP_PROJECT
SECRETS_BACKEND=azure  Azure Key Vault; URL em AZURE_KEYVAULT_URL, identidade gerenciada da VM (sem senha)
SECRETS_BACKEND=file   arquivo JSON {nome: {...}} em SECRETS_FILE (permissao 600, montado so-leitura); nao depende de nuvem

Os nomes dos segredos sao os mesmos nos dois lados (ex.: hapvida-portal-saude). Valor = JSON.
"""
import json
import os

_client = None


def _backend() -> str:
    return os.environ.get("SECRETS_BACKEND", "gcp").lower()


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
            from google.cloud import secretmanager

            _client = secretmanager.SecretManagerServiceClient()
    return _client


def get_json(secret_id: str, project: str | None = None) -> dict:
    client = _get_client()
    if _backend() == "file":
        return client[secret_id]
    if _backend() == "azure":
        return json.loads(client.get_secret(secret_id).value)
    project = project or os.environ["GCP_PROJECT"]
    name = f"projects/{project}/secrets/{secret_id}/versions/latest"
    return json.loads(client.access_secret_version(request={"name": name}).payload.data.decode("utf-8"))


def list_ids(prefix: str = "", project: str | None = None) -> list[str]:
    client = _get_client()
    if _backend() == "file":
        return [i for i in client if i.startswith(prefix)]
    if _backend() == "azure":
        return [p.name for p in client.list_properties_of_secrets() if p.name.startswith(prefix)]
    project = project or os.environ["GCP_PROJECT"]
    ids = (s.name.split("/")[-1] for s in client.list_secrets(request={"parent": f"projects/{project}"}))
    return [i for i in ids if i.startswith(prefix)]
