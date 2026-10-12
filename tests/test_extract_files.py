import io
import zipfile
from pathlib import Path

from src.settings import settings
import src.extract_files as ex


def make_zip(path: Path, files: dict[str, bytes]):
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, mode="w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    path.write_bytes(bio.getvalue())


def test_get_file_base_name():
    assert ex.get_file_base_name(Path("Empresas4.zip")) == "Empresas"
    assert ex.get_file_base_name(Path("Socios.zip")) == "Socios"
    assert ex.get_file_base_name(Path("123.zip")) == "desconhecido"


def test_group_files_orders_and_groups():
    paths = [Path("Empresas2.zip"), Path("Empresas1.zip"), Path("Socios.zip")]
    groups = list(ex.group_files(paths))
    keys = [k for k, _ in groups]
    assert keys == ["Empresas", "Socios"]


def test_run_extraction(tmp_path, monkeypatch):
    settings.project_root = Path(tmp_path)
    settings.create_dirs()
    make_zip(settings.compressed_dir / "Empresas1.zip", {"a.txt": b"x"})
    make_zip(settings.compressed_dir / "Empresas2.zip", {"b.txt": b"y"})
    make_zip(settings.compressed_dir / "Socios.zip", {"c.txt": b"z"})
    ex.run_extraction()
    emp = settings.extracted_dir / "empresas"
    soc = settings.extracted_dir / "socios"
    assert (emp / "a.txt").exists()
    assert (emp / "b.txt").exists()
    assert (soc / "c.txt").exists()
