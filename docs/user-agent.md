[[Voltar ao README]](../README.md) • [[Índice da documentação]](index.md)

# Emulação de navegador (Downloader)

## O que o projeto faz

O downloader não gerencia uma lista de `User-Agent`. Ele usa
[`curl_cffi`](https://curl-cffi.readthedocs.io/) com `impersonate`, que reproduz a impressão
digital TLS (JA3/JA4) **e** os cabeçalhos HTTP — incluindo `User-Agent` — de um navegador real. É
por isso que a requisição passa por WAFs que bloqueiam `requests`/`urllib` comuns.

A escolha é feita pela variável `IMPERSONATE`:

| Valor            | Navegador imitado |
|------------------|-------------------|
| `chrome`         | Chrome atual      |
| `chrome110`      | Chrome 110 (padrão)|
| `edge99`         | Edge 99           |
| `safari15_3`     | Safari 15.3       |

```ini
IMPERSONATE=chrome110
```

Esse valor é repassado em `src/downloader.py::_get_session`. Perfil fixo é intencional: alternar
aleatoriamente entre perfis produz uma impressão digital inconsistente ao longo da mesma sessão e
torna a evasão de WAF menos eficaz, não mais.

## Resistência a bloqueios

O que realmente reduz bloqueios, em ordem de efeito:

1. `IMPERSONATE` — impressão digital TLS compatível com navegador.
2. `PROXIES` + `PROXY_ROTATION_STRATEGY` — distribuir as requisições por IPs diferentes.
3. `RATE_LIMIT_PER_SEC` — limite global em **bytes por segundo**. `0` desativa.
4. `RETRY_MAX_ATTEMPTS` / `RETRY_BACKOFF_FACTOR` — backoff exponencial entre tentativas.
5. O circuit breaker em `AsyncDownloader`, que pausa o download após 5 erros consecutivos em vez de
   insistir contra um servidor que está recusando.

## Se você realmente quiser trocar o User-Agent

Não é uma opção suportada aqui: `AsyncSession` de `curl_cffi` aceita um header explícito, mas
sobrepor o `User-Agent` sem mudar a impressão digital TLS produz uma requisição que não corresponde
a nenhum navegador real, o que costuma piorar a detecção em vez de melhorar.

Para ajustar o comportamento, prefira mudar `IMPERSONATE`.