# etl-cnpj

Pipeline de ETL que baixa os [dados abertos de CNPJ da Receita Federal](https://arquivos.receitafederal.gov.br/dados/cnpj/dados_abertos_cnpj/)
e carrega em um [DuckDB](https://duckdb.org/).

Sem servidor: o banco é um arquivo. Os volumes extraídos são lidos no lugar,
sem etapa de consolidação.

Continuação de [danalec/CNPJ-Receita-Federal](https://github.com/danalec/CNPJ-Receita-Federal).

## Requisitos

- Python 3.10+
- Espaço em disco para os volumes extraídos (dezenas de GB)

## Começando

```bash
cp .env.example .env
pip install -r requirements.txt -r requirements-dev.txt

python -m src --force
```

O banco fica em `data/cnpj.duckdb`.

No Windows, `./tasks.ps1` embrulha os comandos comuns:

```powershell
./tasks.ps1 install     ./tasks.ps1 lint      ./tasks.ps1 test
./tasks.ps1 etl         ./tasks.ps1 step load ./tasks.ps1 verify
```

## CLI

```bash
python -m src [opções]
```

| Opção | O que faz |
|---|---|
| `--force` | ignora o histórico e roda todas as etapas |
| `--resume` | pula etapas já concluídas |
| `--dry-run` | mostra o que seria executado |
| `--step {check,download,extract,load,constraints}` | executa só uma etapa |
| `--only` / `--exclude` | restringem o passo `load` por nome de config |
| `--run-queries` | roda os `.sql` de `queries/` ao final; o diretório é criado vazio pelo projeto, então a flag não faz nada até você adicionar queries |
| `--max-workers N` | sobrescreve `MAX_WORKERS` |
| `--rate-limit-per-sec N` | sobrescreve `RATE_LIMIT_PER_SEC` (bytes/s) |
| `--skip-zip-verify` | pula a verificação de integridade dos ZIPs |

`--dry-run` e `--run-queries` só valem no pipeline completo.

## Etapas

| Etapa | O que faz |
|---|---|
| `check` | encontra a pasta `YYYY-MM` mais recente na Receita |
| `download` | baixa os ZIPs em paralelo, com resume por header `Range` |
| `extract` | descompacta cada ZIP |
| `load` | valida, cria as tabelas e carrega |
| `constraints` | `ANALYZE` e `CHECKPOINT` |

A carga roda em duas fases, porque o DuckDB impõe as chaves estrangeiras durante
o `INSERT`: primeiro as tabelas de domínio, depois o backfill das linhas pai
ausentes, e por último as tabelas filhas.

Dentro de cada fase há dois caminhos. As tabelas que não têm limpador nem schema
de validação são lidas, convertidas, deduplicadas e gravadas inteiramente pelo
DuckDB, sem passar por pandas. As que precisam de limpeza ou de validação por
linha continuam pelo pandas, porque os cleaners e os quality gates operate em
DataFrame. O resumo de cada tabela registra qual dos dois a produziu, em
`load_strategy`. Os detalhes estão em [docs/carga.md](docs/carga.md).

## Configuração

Tudo via `.env`. Ver [`.env.example`](.env.example) e a lista completa em
[`src/settings.py`](src/settings.py).

## Desenvolvimento

```bash
ruff check . && mypy src && pytest -q
```

Não há serviço para subir: os testes de carga rodam contra um DuckDB temporário.

## Documentação

- [docs/index.md](docs/index.md) — configuração e troubleshooting
- [docs/descricao-dados.md](docs/descricao-dados.md) — layout dos dados da Receita
- [docs/diagrama_er.md](docs/diagrama_er.md) — diagrama entidade-relacionamento
- [docs/download.md](docs/download.md) — detalhes do downloader
- [docs/auto-repair.md](docs/auto-repair.md) — normalização e quality gates
- [docs/boas-praticas-indices.md](docs/boas-praticas-indices.md) — índices e performance
- [docs/user-agent.md](docs/user-agent.md) — emulação de navegador
- [docs/docker.md](docs/docker.md) — Docker
- [docs/carga.md](docs/carga.md) — os dois caminhos de carga e as armadilhas do leitor

## Licença

Apache-2.0 — [LICENSE](LICENSE)