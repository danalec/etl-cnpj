import logging
import sys
import io
from pathlib import Path
from typing import Literal, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Sobe 2 níveis para chegar na raiz do projeto (source/config.py)
    project_root: Path = Path(__file__).resolve().parents[1]

    # URL Base da Receita (sem a data)
    rfb_base_url: str = (
        "https://arquivos.receitafederal.gov.br/dados/cnpj/dados_abertos_cnpj/"
    )

    # Esta variável será preenchida dinamicamente pelo script de update
    # Default vazio ou uma data específica se for rodar manual
    target_date: str = ""

    """
    Banco de dados
    """

    # Caminho do arquivo DuckDB. DuckDB e embutido: nao ha servidor nem porta.
    duckdb_path: str = "data/cnpj.duckdb"
    # Threads de execucao; 0 deixa o DuckDB decidir.
    duckdb_threads: int = 0
    # Limite de memoria do buffer manager. Espaco excedente vai para disco.
    duckdb_memory_limit: str = ""
    # Diretorio temporario para spill de operacoes grandes.
    duckdb_temp_directory: str = ""

    """
    Configurações de Download
    """

    # Número de downloads simultâneos (não exagere para não tomar block)
    max_workers: int = 4
    extract_workers: int = 2
    # Tamanho do pedaço lido na memória durante download (8KB)
    download_chunk_size: int = 8192
    rate_limit_per_sec: int = 0
    verify_zip_integrity: bool = True

    # Configurações Stealth/Async
    max_concurrent_requests: Optional[int] = None  # Se None, usa max_workers * 2
    retry_max_attempts: int = 10
    retry_backoff_factor: float = 0.5
    
    # Proxy Configuration
    proxies: Optional[list[str]] = None
    proxy_rotation_strategy: Literal["round_robin", "random"] = "round_robin"
    
    # Browser Impersonation
    impersonate: Literal["chrome", "chrome110", "edge99", "safari15_3"] = "chrome110"

    """
    Comportamento do schema

    DuckDB e sempre duravel (WAL-backed) e nao tem tabela nao duravel, CLUSTER
    nem particionamento declarativo. As tabelas nascem com seus indices e
    constraints em schema.sql, entao nao existe uma segunda fase de "aplicar
    constraints" depois da carga.
    """

    # CRLF is handled by the CSV reader itself; only the BOM needs a setting.
    strip_bom: bool = True

    # Rebuild the schema from scratch. Off by default: the drop happens before any
    # table is read, so a run that cannot finish leaves an empty database behind.
    allow_drop: bool = False

    # Cria as linhas pai que faltam antes de carregar os filhos. Sem isso a
    # carga aborta na primeira referencia orfa, porque a FK e verificada no
    # INSERT e nao pode ser adicionada depois.
    enable_constraints_backfill: bool = True

    """
    Configurações da migração de dados, caso tenha mais memória
    você pode aumentar, o padrão é 200_000 o que da um consumo de
    200-500 megas de memória. Essa variação ocorre por conta do tamanho
    das tabelas
    """

    file_encoding: str = "latin1"
    chunk_size: int = 200_000

    # Set log level
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    strict_fk_validation: bool = True
    auto_repair_level: Literal["none", "basic", "aggressive"] = "basic"
    telemetry_max_bytes: int = 10_000_000
    quarantine_max_bytes: int = 10_000_000
    telemetry_rotate_daily: bool = True
    quarantine_rotate_daily: bool = True
    enable_cep_enrichment: bool = False
    cep_map_path: Optional[Path] = None
    enable_quality_gates: bool = True
    gate_max_changed_ratio: float = 0.3
    gate_max_null_delta_ratio: float = 0.3
    gate_log_level: Literal["INFO", "WARNING"] = "WARNING"
    gate_min_rows: int = 20
    enable_metrics_prometheus: bool = False
    prometheus_metrics_path: Optional[Path] = None
    enable_prometheus_push: bool = False
    prometheus_push_url: Optional[str] = None
    prometheus_job: str = "cnpj_auto_repair"
    prometheus_instance: str = "local"
    enable_otlp_push: bool = False
    otlp_endpoint: Optional[str] = None
    skip_invalid_estabelecimentos_cnpj: bool = False
    cep_correct_uf_only_if_null: bool = True
    municipio_name_map_path: Optional[Path] = None
    
    @property
    def download_url(self) -> str:
        """Monta a URL completa baseada na data alvo."""
        if not self.target_date:
            return ""
        return f"{self.rfb_base_url}{self.target_date}/"

    @property
    def state_file(self) -> Path:
        base = self.data_dir
        return base / "last_version_processed.txt"

    @property
    def data_dir(self) -> Path:
        return self.project_root / "data"

    @property
    def log_dir(self) -> Path:
        return self.project_root / "logs"

    @property
    def compressed_dir(self) -> Path:
        base = self.data_dir
        return base / "compressed_files"

    @property
    def extracted_dir(self) -> Path:
        base = self.data_dir
        return base / "extracted_files"

    @property
    def queries_dir(self) -> Path:
        return self.project_root / "queries"

    @property
    def telemetry_dir(self) -> Path:
        base = self.log_dir
        return base / "telemetry"

    @property
    def quarantine_dir(self) -> Path:
        base = self.log_dir
        return base / "quarantine"

    @property
    def duckdb_file(self) -> Path:
        """Absolute path to the DuckDB file, resolved against project_root."""
        p = Path(self.duckdb_path)
        return p if p.is_absolute() else self.project_root / p

    def create_dirs(self):
        """Garante que a estrutura de pastas exista."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.compressed_dir.mkdir(parents=True, exist_ok=True)
        self.extracted_dir.mkdir(parents=True, exist_ok=True)
        self.queries_dir.mkdir(parents=True, exist_ok=True)
        self.telemetry_dir.mkdir(parents=True, exist_ok=True)
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)


# Conjunto de UF único para todo o projeto
UF_SET: set[str] = {
    'AC','AL','AP','AM','BA','CE','DF','ES','GO','MA','MT','MS','MG','PA','PB','PR','PE','PI','RJ','RN','RO','RS','RR','SC','SE','SP','TO'
}


# Instancia e cria diretórios
settings = Settings()
settings.create_dirs()


def setup_logging():
    """Configura o logger raiz."""
    log_file = settings.log_dir / "cnpj.log"
    level = getattr(logging, settings.log_level)
    # Garante UTF-8 no console mesmo em ambientes Windows com CP1252
    console_stream = sys.stdout
    try:
        if hasattr(console_stream, "reconfigure"):
            console_stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        try:
            console_stream = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        except Exception:
            pass

    logging.basicConfig(
        level=level,
        format="%(levelname)s - %(asctime)s - [%(name)s] - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(log_file, mode="w", encoding="utf-8"),
            logging.StreamHandler(console_stream),
        ],
    )


__all__ = [
    "Settings",
    "settings",
    "setup_logging",
]
