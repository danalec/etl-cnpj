[[Voltar ao README]](../README.md) • [[Índice da documentação]](index.md)

# Guia de Docker

Este guia descreve como construir a imagem Docker, executar com Docker Compose e auditar dependências com `pip-audit`.

## Build da imagem

```bash
docker build -t etl-cnpj:latest .
```

- O `Dockerfile` usa `python:3.10-slim` (a versão mínima do projeto), instala só
  `requirements.txt` e roda `python -m src`.
- O container roda como usuário `1000`, então os diretórios montados precisam ser
  graváveis por esse usuário.
- O `.dockerignore` reduz o contexto de build.

## Executar com Docker Compose

O repositório já traz um `docker-compose.yml` com um único serviço, `app`:

```bash
docker compose up --build
```

- Para modo em background: `docker compose up -d --build`.
- O serviço monta `./data` e `./logs` em `/app` e lê as variáveis do seu `.env`.
- Não há serviço de banco: o DuckDB é embutido e o banco é o arquivo
  `/app/data/cnpj.duckdb`. Não há porta, container de dependência nem credencial.
- O `CMD` da imagem é `python -m src`; o Compose apenas fixa `DUCKDB_PATH`.
  Rodar uma etapa só é `docker compose run --rm app python -m src --step download`.

## Auditoria de dependências (pip-audit)

Audite vulnerabilidades nas dependências Python.

### Local (host)

```bash
pip install pip-audit
pip-audit -r requirements.txt
```

### Dentro do contêiner

A imagem não inclui `pip-audit`. Instale-o na execução:

```bash
docker compose run --rm --entrypoint sh app -c "pip install pip-audit && pip-audit -r requirements.txt"
```

## Boas práticas
- O volume `./data` é o estado do pipeline: guarde junto com ele
  `data/last_version_processed.txt` e `data/runs.json`, que controlam o resume.
- Fixe a tag da imagem em produção; `latest` mascara regressões de dependência.
- Revise as variáveis de ambiente e segredos fora da imagem (Compose/Stack, `.env`).
- Copiar o `.duckdb` com o banco aberto falha no Windows, que mantém lock
  exclusivo. Use `EXPORT DATABASE`.

