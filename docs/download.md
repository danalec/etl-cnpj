[[Voltar ao README]](../README.md) • [[Índice da documentação]](index.md)

# Download dos dados (assíncrono)

Este guia mostra como baixar os arquivos oficiais de CNPJ em paralelo e onde configurar os parâmetros.

## Visão geral
- O módulo responsável pelo download é `src/downloader.py`.
- O download é **assíncrono**: `AsyncDownloader` usa `curl_cffi.requests.AsyncSession`,
  um `asyncio.Semaphore` para limitar a concorrência e `tqdm.asyncio` para o progresso.
- A verificação de integridade dos `.zip` roda num executor, fora do event loop.
- Os arquivos são salvos em `data/compressed_files`.

> A extração (`src/extract_files.py`) é a parte que usa `ThreadPoolExecutor`,
> com `EXTRACT_WORKERS`. O download é async; não confunda os dois.

## Pré-requisitos
- `.env` configurado e pastas criadas automaticamente pelo projeto.
- Defina a data de destino (ex.: `2025-11`) para montar a URL dos arquivos.

## Configuração (`.env`)
As principais variáveis para o download:

```ini
# Data alvo no formato YYYY-MM
TARGET_DATE=2025-11

# Número de downloads paralelos; a concorrência real é MAX_WORKERS x 2
MAX_WORKERS=4
# Ou fixe a concorrência diretamente (tem precedência sobre o cálculo acima)
# MAX_CONCURRENT_REQUESTS=8

# Tamanho do chunk em bytes
DOWNLOAD_CHUNK_SIZE=8192

# Limite opcional de taxa por segundo, em BYTES (0 desabilita)
RATE_LIMIT_PER_SEC=0

# Verificação de integridade dos arquivos ZIP
VERIFY_ZIP_INTEGRITY=true
```

Observações:
- `TARGET_DATE` define `download_url` baseado na URL oficial da Receita.
- `MAX_WORKERS` **não** é o número de downloads simultâneos: o downloader usa
  `MAX_CONCURRENT_REQUESTS` e, se estiver vazio, `MAX_WORKERS * 2`. Com o padrão
  `MAX_WORKERS=4` são 8 downloads em paralelo.
- `RATE_LIMIT_PER_SEC` é um limite global em **bytes por segundo**. Um valor menor
  que `DOWNLOAD_CHUNK_SIZE` trava o download por horas — o downloader emite um
  aviso nesse caso, mas o valor continua perigoso.
- Ajuste a concorrência de acordo com sua rede e as políticas do servidor.

## Executar o download
Duas formas de executar:

1. Orquestrador (apenas etapa de download):
   ```bash
   python -m src --step download
   ```

2. Módulo direto:
   ```bash
   python -m src.downloader
   ```

## Paralelismo, progresso e resiliência
- A concorrência é controlada por `MAX_CONCURRENT_REQUESTS` (ou `MAX_WORKERS * 2`).
- O progresso por arquivo é exibido com barra de avanço em bytes (`tqdm`).
- Downloads são retomados quando possível (suporte a `Range`), evitando repetir do zero.
- Se a listagem da Receita vier vazia, isso é tratado como **falha**, não como
  "nada a fazer": um mês vazio seria marcado como processado.
- Se qualquer arquivo falhar ao baixar, o estágio termina com erro e o manifesto
  não é gravado.

## Integridade e armazenamento
- Com `VERIFY_ZIP_INTEGRITY=true`, arquivos `.zip` são verificados; se corrompidos,
  são removidos e o download do arquivo é marcado como falha.
- Os arquivos baixados são gravados em `data/compressed_files`.
- Ao final, o downloader grava `data/manifest.json` com o que efetivamente ficou
  em disco. Ver [design-manifest.md](design-manifest.md).

## Boas práticas
- Evite exagerar em `MAX_WORKERS` para reduzir bloqueios.
- Utilize pausa/taxa (`RATE_LIMIT_PER_SEC`) se notar rejeições do servidor.
- Consulte o guia de [emulação de navegador](user-agent.md) para melhorar a resiliência de requisições.

