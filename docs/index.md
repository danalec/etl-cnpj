[[Voltar ao README]](../README.md)

# etl-cnpj — Documentação

## Visão Geral
- Pipeline de `ETL` para baixar, tratar e carregar os dados públicos de CNPJ: https://arquivos.receitafederal.gov.br/dados/cnpj/dados_abertos_cnpj/
- Destino: **DuckDB**, um arquivo. Sem servidor, sem porta, sem credenciais.
- Os volumes extraídos são lidos **no lugar**: não há consolidação nem CSV intermediário.
- Integridade: PKs e FKs são declaradas no `CREATE TABLE` e impostas no `INSERT`.
- Nota de integridade: versões da Receita têm lacunas (ex.: `2025-11` sem o país `150`).
  Quem falta é criado como `NAO CONSTA NA ORIGEM` antes da carga dos filhos
  (`ENABLE_CONSTRAINTS_BACKFILL`).

## Início Rápido
- Instala dependências: `pip install -r requirements.txt -r requirements-dev.txt`
- Orquestrador completo: `python -m src`
- Etapas específicas:
  - `python -m src.check_update`
  - `python -m src.downloader`
  - `python -m src.extract_files`
  - `python -m src.database_loader`

### CLI do orquestrador (`src`)
- `--force`: ignora histórico e roda todas as etapas
- `--resume`: retoma de onde parou, pulando etapas já concluídas
- `--dry-run`: mostra o que seria executado, sem tocar em nada
- `--step {check,download,extract,load,constraints}`: executa somente a etapa informada
- `--only` / `--exclude`: restringem as tabelas do passo `load`, por nome de **config**
- `--run-queries`: executa as queries em `queries/` após o pipeline
- `--max-workers`, `--rate-limit-per-sec`, `--skip-zip-verify`: sobrescrevem o `.env` nesta execução

### Uso no Windows (PowerShell)
Sem `make`, utilize `./tasks.ps1` na raiz:
- `install` — instala dependências (`pip`, ou `poetry install --with dev` se Poetry estiver disponível)
- `lint` — `ruff check .`
- `check` — `python -m compileall -q src` (byte-compile; **não** é a etapa `check` do pipeline)
- `test` — `pytest -q`
- `pipeline` — `python -m src`
- `etl` — do download até as constraints
- `step <nome>` — roda uma etapa (`download`, `load`, `constraints`, …)
- `verify` — lint + compile + mypy + testes
- `ci` — testes, lint e build da imagem

## Pré-requisitos

- `Python` 3.10+
- `DuckDB` 1.5+, instalado junto com as dependências
- Espaço em disco para os volumes extraídos (dezenas de GB)

## Configuração (`.env`)
Copie `.env.example` para `.env`. As variáveis correspondem aos campos de `Settings` em `src/settings.py`.

```ini
DUCKDB_PATH=data/cnpj.duckdb
DUCKDB_THREADS=0
DUCKDB_MEMORY_LIMIT=
TARGET_DATE=

STRICT_FK_VALIDATION=true
ENABLE_CONSTRAINTS_BACKFILL=true
ENABLE_QUALITY_GATES=true
GATE_MIN_ROWS=20
```

Variáveis relevantes: `CHUNK_SIZE` (linhas por chunk na carga), `AUTO_REPAIR_LEVEL`
(`none | basic | aggressive`), `ENABLE_QUALITY_GATES`, `GATE_MAX_CHANGED_RATIO`,
`GATE_MAX_NULL_DELTA_RATIO`, `RATE_LIMIT_PER_SEC` (bytes/s; `0` desativa).
A lista completa está em `.env.example` e em `src/settings.py`.

## Etapas do Pipeline
- Verificação de atualização: `../src/check_update.py`
- Download: `../src/downloader.py`
- Extração: `../src/extract_files.py`
- Carga: `../src/database_loader.py`
- Backfill das linhas pai ausentes: `../src/backfill.py` (roda entre as fases da carga)
- Normalização, telemetria e quality gates: `../src/validation.py`
- Schema e índices: `../src/schema.sql`
- Conexão DuckDB: `../src/db.py`

## Estado entre execuções
- `data/runs.json` — ledger de resume: para cada mês, o status de cada etapa.
- `data/last_version_processed.txt` — o último mês marcado como completo. Só é
  gravado quando todas as etapas terminaram; uma carga parcial (`--only`/`--exclude`)
  ou com etapa pendente deixa o mês sem carimbo, e a próxima execução tenta de novo.
- `data/manifest.json` — o que o download realmente gravou. Ver
  [design-manifest.md](design-manifest.md).
- `queries/` — criado vazio pelo projeto; a flag `--run-queries` executa os `.sql`
  dali e não faz nada enquanto o diretório estiver vazio.

## Notas de Performance
- Carga por `INSERT ... SELECT` a partir de um DataFrame registrado, sem serialização intermediária.
- Tabelas e índices nascem juntos em `schema.sql`.
- Processamento em _chunks_ via Pandas para reduzir uso de memória.
- Busca por nome é varredura de coluna: ~2 s em 60 milhões de linhas.
- `cnae_fiscal_secundaria` em `TEXT` (literal `{a,b}`) em vez de lista nativa.

## Testes
- `pytest -q`. Não há serviço para subir: os testes de carga usam um DuckDB temporário.

## Troubleshooting

- **Referência órfã aborta a carga**: o `INSERT` é rejeitado. O backfill cria o pai
  antes; se estiver desligado, ou carregue a tabela de domínio antes, ou use
  `STRICT_FK_VALIDATION=false`.
- **Linha some da tabela**: linha sem chave primária é descartada, não só registrada.
  A contagem está em `critical_key_null_rows_skipped` no summary da tabela.
- **CSV com BOM/linhas quebradas**: o `strip_bom` e o `normalize_line_endings` são aplicados
  automaticamente. Campos com `;` interno, como `cnae_fiscal_secundaria`, vêm entre aspas
  na origem e precisam continuar assim.
- **Backup**: use `EXPORT DATABASE`. Copiar o `.duckdb` com o banco aberto falha no
  Windows, que mantém lock exclusivo.

## Conteúdos Relacionados
- Boas práticas de índices: [boas-praticas-indices.md](boas-praticas-indices.md)
- Guia de Docker: [docker.md](docker.md)
- Emulação de navegador (Downloader): [user-agent.md](user-agent.md)
- Download dos dados: [download.md](download.md)
- AUTO-REPAIR: Normalização e Telemetria: [auto-repair.md](auto-repair.md)
- O caminho de carga: [carga.md](carga.md)
- Layout dos dados: [descricao-dados.md](descricao-dados.md)
- Diagrama ER: [diagrama_er.md](diagrama_er.md)