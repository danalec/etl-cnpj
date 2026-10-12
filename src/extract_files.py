import zipfile
import os
from pathlib import Path
from itertools import groupby, takewhile
from typing import List, Iterator, Tuple
import logging
from . import manifest
from .settings import settings
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)


def get_file_base_name(path: Path) -> str:
    """
    Extrai o nome base de um arquivo, removendo números e a extensão.
    Exemplo: "Empresas4.zip" -> "Empresas"
    """
    base = ''.join(takewhile(str.isalpha, path.stem))
    return base or "desconhecido"


def group_files(paths: List[Path]) -> Iterator[Tuple[str, Iterator[Path]]]:
    """
    Agrupa uma lista de caminhos de arquivo pelo seu nome base.
    A função requer que a lista de entrada esteja pré-ordenada
    pela chave de agrupamento.
    """
    # Pré-ordena a lista para que o groupby funcione corretamente
    sorted_paths = sorted(paths, key=get_file_base_name)
    return groupby(sorted_paths, key=get_file_base_name)


def create_directory_if_not_exists(directory: Path):
    """
    Cria um diretório de forma segura, sem falhar se ele já existir.
    Esta função é projetada para executar um efeito colateral:
    modificar o sistema de arquivos.
    """
    try:
        directory.mkdir(parents=True, exist_ok=True)
        logger.info(f"✔️ Diretório '{directory}' garantido.")
    except OSError as e:
        logger.error(f"❌ Erro ao criar o diretório '{directory}': {e}")
        raise


def extract_single_zip(zip_path: Path, destination_dir: Path):
    """Extrai um arquivo .zip e devolve os nomes dos que falharam.

    Um arquivo corrompido antes sumia em um log e a etapa reportava sucesso,
    entao o mes era carimbado como processado com dado incompleto. O nome volta
    ao chamador para que a falha seja visivel e a carga nao avance.
    """
    logger.info(f"   -> Extraindo '{zip_path.name}'...")

    try:
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            for member in zip_ref.infolist():
                member_path = Path(member.filename)
                if member_path.is_absolute():
                    logger.warning(f"   ~ Ignorando entrada absoluta: {member.filename}")
                    continue

                target_path = (destination_dir / member_path).resolve()
                base_dir = destination_dir.resolve()
                try:
                    base_str = str(base_dir)
                    target_str = str(target_path)
                    if os.path.commonpath([base_str, target_str]) != base_str:
                        logger.warning(
                            f"   ~ Ignorando entrada potencialmente maliciosa: {member.filename}"
                        )
                        continue
                except Exception:
                    logger.warning(f"   ~ Ignorando entrada inválida: {member.filename}")
                    continue

                if member.is_dir():
                    target_path.mkdir(parents=True, exist_ok=True)
                else:
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    with zip_ref.open(member, "r") as src, open(target_path, "wb") as dst:
                        dst.write(src.read())

        return []

    except zipfile.BadZipFile as e:
        logger.error(
            f"AVISO: O arquivo '{zip_path.name}' esta corrompido "
            f"ou nao e um ZIP valido: {e}"
        )
        return [zip_path.name]

    except Exception as e:
        logger.error(f"Erro inesperado ao extrair '{zip_path.name}': {e}")
        return [zip_path.name]


# --- Função Principal (Orquestrador) ---


def _verify_against_manifest() -> list[str]:
    """Archives the manifest expected but that produced no CSV.

    Without a manifest there is nothing to check against, so the stage says so
    rather than pretending the extraction was complete.
    """
    data = manifest.read_manifest(settings.data_dir)
    if data is None:
        logger.warning(
            "Sem manifesto em %s: a descompactacao nao pode ser verificada. "
            "Rode o download antes.",
            settings.data_dir,
        )
        return []

    missing: list[str] = []
    for table, names in manifest.expected_archives(data).items():
        target = settings.extracted_dir / table.lower()
        if not target.is_dir():
            missing.extend(names)
            continue
        for name in names:
            # Um ZIP de volume traz um CSV com o mesmo nome; o manifesto grava o
            # nome do arquivo, e o extrator preserva o nome interno.
            stem = Path(name).stem
            if not any(c.name == name or c.stem == stem for c in target.glob("*.csv")):
                missing.append(f"{table}/{name}")

    if missing:
        logger.error("Manifesto esperava, mas nao foram extraidos: %s", missing)
    return missing


def run_extraction():
    """
    Orquestra todo o processo de descompactação.
    """

    compressed_dir = settings.compressed_dir
    extracted_dir = settings.extracted_dir

    if not compressed_dir.is_dir():
        logger.error(
            f"❌ Erro: O diretório de origem '{compressed_dir}' não foi encontrado."
        )
        return

    logger.info(
        f"Iniciando processo de descompactação...\n"
        f"Origem: '{compressed_dir}'\n"
        f"Destino: '{extracted_dir}'\n"
    )

    # 2. Leitura do sistema de arquivos para obter a lista de arquivos.
    zip_files = list(compressed_dir.glob("*.zip"))

    if not zip_files:
        logger.error("🟡 Nenhum arquivo .zip encontrado no diretório de origem.")
        return

    # 3. Lógica pura para agrupar os arquivos.
    file_groups = group_files(zip_files)

    # 4. Extrai e coleta as falhas. Um volume corrompido nao pode desaparecer.
    failed: list[str] = []
    for base_name, files_iterator in file_groups:
        target_path = extracted_dir / base_name.lower()

        logger.info(f"\nProcessando grupo: '{base_name}'")
        create_directory_if_not_exists(target_path)

        files_list = list(files_iterator)
        with ThreadPoolExecutor(max_workers=settings.extract_workers) as executor:
            futures = [
                executor.submit(extract_single_zip, fp, target_path)
                for fp in files_list
            ]
            for future in as_completed(futures):
                failed.extend(future.result() or [])

    # 5. Confere contra o manifesto: o download registrou o que esperava, e cada
    #    arquivo esperado precisa ter produced um CSV.
    missing = _verify_against_manifest()

    if failed or missing:
        raise RuntimeError(
            "Descompactacao incompleta. "
            f"corrompidos={len(failed)}{failed if failed else ''} "
            f"ausentes_no_manifest={len(missing)}{missing if missing else ''}"
        )

    logger.info("\nDescompactacao concluida com sucesso.")


if __name__ == "__main__":
    from .settings import setup_logging

    setup_logging()
    run_extraction()
