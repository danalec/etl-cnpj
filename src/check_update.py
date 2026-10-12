import requests
import logging
import shutil
from typing import Optional
from bs4 import BeautifulSoup
import re

from .settings import settings

# Configura logger local para este script
logger = logging.getLogger("updater")


def get_latest_remote_date() -> Optional[str]:
    """
    Acessa o site da Receita e descobre qual é a pasta de data mais recente.
    Retorna uma string ex: '2025-11'
    """
    try:
        response = requests.get(settings.rfb_base_url, timeout=30)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        links = soup.find_all("a")

        dates = []
        # Padrão regex para pegar pastas no formato YYYY-MM
        date_pattern = re.compile(r"^(\d{4}-\d{2})/$")

        for link in links:
            href = link.get("href")
            if not isinstance(href, str):
                continue
            match = date_pattern.match(href)
            if match:
                dates.append(match.group(1))

        if not dates:
            logger.warning("Nenhuma data encontrada na página da Receita.")
            return None

        # Ordena decrescente para pegar a mais recente (2025-11 > 2025-10)
        dates.sort(reverse=True)
        return dates[0]

    except Exception as e:
        logger.error(f"Erro ao checar atualizações: {e}")
        return None


def get_local_version() -> Optional[str]:
    """Lê a última versão processada do arquivo de estado."""
    state_file = settings.state_file

    if state_file.exists():
        return state_file.read_text().strip()
    return None


def update_local_version(version) -> None:
    """Salva a nova versão no arquivo de estado."""
    settings.state_file.write_text(version)


def clean_data_dirs() -> None:
    """
    Limpa as pastas de dados antigos antes de baixar os novos.
    Isso é crucial para não misturar dados de meses diferentes e estourar o disco.
    """
    logger.info("Limpando diretórios de dados antigos...")

    compressed_dir = settings.compressed_dir
    extracted_dir = settings.extracted_dir

    # Limpa compressed_files
    for item in compressed_dir.glob("*"):
        if item.is_file():
            item.unlink()

    # Limpa extracted_files (remove pastas inteiras)
    for item in extracted_dir.glob("*"):
        if item.is_dir():
            shutil.rmtree(item)
        elif item.is_file():
            item.unlink()

    logger.info("Diretórios limpos.")


def check_updates(skip_clean: bool = False) -> Optional[str]:
    logger.info("Verificando atualizações na Receita Federal...")

    latest_remote = get_latest_remote_date()
    last_processed = get_local_version()

    if not latest_remote:
        logger.error("Não foi possível determinar a versão remota. Abortando.")
        return None

    logger.info(f"Última versão disponível: {latest_remote}")
    logger.info(f"Última versão processada: {last_processed}")

# Adopt the newest folder found even when it is the one already processed.
    # settings.download_url is derived from target_date, so leaving it empty
    # makes the downloader request an empty URL. --force reaches the pipeline
    # regardless of this function's return value.
    settings.target_date = latest_remote

    if latest_remote == last_processed:
        return None

    logger.info(f"Nova versão encontrada: {latest_remote}. Iniciando atualização.")

    if not skip_clean:
        logger.info("Removendo arquivos antigos! Caso existam")
        clean_data_dirs()

    return latest_remote


if __name__ == "__main__":
    from .settings import setup_logging

    setup_logging()
    check_updates()
