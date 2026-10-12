import pytest
import io
import zipfile
from unittest.mock import AsyncMock, patch
from curl_cffi.requests import AsyncSession
from src.downloader import AsyncDownloader
from src.settings import settings

# --- Mocks ---

class MockResponse:
    def __init__(self, content=b"", status_code=200, headers=None):
        self.content = content
        self.status_code = status_code
        self.headers = headers or {}
        self.text = content.decode("utf-8", errors="ignore")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP Error {self.status_code}")

    async def aiter_content(self, chunk_size=None):
        yield self.content

class MockStreamContext:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, exc_type, exc_value, traceback):
        pass

# --- Tests ---

@pytest.mark.asyncio
async def test_fetch_file_list():
    html_content = """
    <html>
        <body>
            <a href="file1.zip">File 1</a>
            <a href="other.txt">Text File</a>
            <a href="http://external.com/file2.zip">External Zip</a>
        </body>
    </html>
    """
    
    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.get = AsyncMock(return_value=MockResponse(content=html_content.encode()))
    
    # Mock context manager for session
    mock_session.__aenter__.return_value = mock_session
    mock_session.__aexit__.return_value = None

    with patch("src.downloader.AsyncDownloader._get_session", return_value=mock_session):
        downloader = AsyncDownloader()
        downloader.base_url = "http://test.com/"
        links = await downloader.fetch_file_list()
        
        assert len(links) == 2
        assert "http://test.com/file1.zip" in links
        assert "http://external.com/file2.zip" in links

@pytest.mark.asyncio
async def test_rate_limiter_bucket_can_always_pay_for_a_chunk():
    """A per-second budget smaller than one chunk used to stall the download.

    Tokens were capped at ``rate_limit`` but spent in ``chunk_size`` units, so
    any limit below the chunk size could never accumulate enough tokens and
    ``_wait_for_token`` looped forever.
    """
    settings.rate_limit_per_sec = 1
    settings.download_chunk_size = 8192

    downloader = AsyncDownloader()
    assert downloader.bucket_capacity >= settings.download_chunk_size

    # With a full bucket the chunk is affordable without any sleeping at all.
    await downloader._wait_for_token(settings.download_chunk_size)

    settings.rate_limit_per_sec = 0


@pytest.mark.asyncio
async def test_rate_limit_below_chunk_size_warns_about_the_unit(caplog):
    """RATE_LIMIT_PER_SEC is bytes/second, which reads like requests/second.

    Someone setting 1 to be polite to the Receita would end up at 1 B/s and
    wait hours per file without any indication why.
    """
    settings.rate_limit_per_sec = 1
    settings.download_chunk_size = 8192

    with caplog.at_level("WARNING"):
        AsyncDownloader()

    assert any("BYTES" in r.message for r in caplog.records), (
        f"expected a bytes-per-second warning, got {[r.message for r in caplog.records]}"
    )

    settings.rate_limit_per_sec = 0


@pytest.mark.asyncio
async def test_rate_limit_above_chunk_size_does_not_warn(caplog):
    settings.rate_limit_per_sec = 10 * 1024 * 1024
    settings.download_chunk_size = 8192

    with caplog.at_level("WARNING"):
        AsyncDownloader()

    assert not any("BYTES" in r.message for r in caplog.records)

    settings.rate_limit_per_sec = 0


@pytest.mark.asyncio
async def test_download_file_success(tmp_path):
    # Create a valid zip file in memory
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, mode="w") as z:
        z.writestr("test.txt", "hello world")
    zip_data = bio.getvalue()

    mock_session = AsyncMock(spec=AsyncSession)
    
    # Mock HEAD request
    mock_session.head = AsyncMock(return_value=MockResponse(headers={"content-length": str(len(zip_data))}))
    
    # Mock GET request (stream)
    mock_response = MockResponse(content=zip_data, headers={"content-length": str(len(zip_data))})
    mock_session.get = AsyncMock(return_value=mock_response)

    mock_session.__aenter__.return_value = mock_session
    mock_session.__aexit__.return_value = None

    with patch("src.downloader.AsyncDownloader._get_session", return_value=mock_session):
        settings.verify_zip_integrity = True
        
        downloader = AsyncDownloader()
        
        # Monkey-patch the retry policy on the bound method to stop immediately
        import tenacity
        downloader.download_file.retry.stop = tenacity.stop_after_attempt(1)
        downloader.download_file.retry.wait = tenacity.wait_none()
        
        downloader.dest_dir = tmp_path
        
        success = await downloader.download_file("http://test.com/test.zip")
        
        assert success
        assert (tmp_path / "test.zip").exists()

@pytest.mark.asyncio
async def test_download_file_integrity_failure(tmp_path):
    # Create invalid data
    zip_data = b"this is not a zip file"

    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.head = AsyncMock(return_value=MockResponse(headers={"content-length": str(len(zip_data))}))
    mock_response = MockResponse(content=zip_data)
    mock_session.get = AsyncMock(return_value=mock_response)
    mock_session.__aenter__.return_value = mock_session
    mock_session.__aexit__.return_value = None

    with patch("src.downloader.AsyncDownloader._get_session", return_value=mock_session):
        settings.verify_zip_integrity = True
        
        downloader = AsyncDownloader()
        
        # Monkey-patch the retry policy on the bound method to stop immediately
        import tenacity
        downloader.download_file.retry.stop = tenacity.stop_after_attempt(1)
        downloader.download_file.retry.wait = tenacity.wait_none()
        
        downloader.dest_dir = tmp_path
        
        # Since we are using tenacity, the original exception is wrapped in RetryError
        # after max retries are exhausted.
        try:
            await downloader.download_file("http://test.com/bad.zip")
        except Exception as e:
            # Check if it's a RetryError (which wraps the underlying exception)
            # or if tenacity was configured to reraise the original exception
            import tenacity
            if isinstance(e, tenacity.RetryError):
                # If wrapped, we can inspect the last attempt's exception if needed,
                # but simply catching it confirms that retries failed as expected.
                pass
            elif "Integrity check failed" in str(e):
                pass
            else:
                pytest.fail(f"Unexpected exception raised: {e}")
        
        assert not (tmp_path / "bad.zip").exists()


# --- run(): the gate that decides whether the month may be stamped ---


@pytest.mark.asyncio
async def test_run_writes_the_manifest_when_every_download_lands(tmp_path):
    """A successful run must record what landed, or the next stage sees nothing."""
    dl = AsyncDownloader()
    dl.dest_dir = tmp_path

    async def fake_list():
        return ["http://test.com/Empresas.zip", "http://test.com/Socios.zip"]

    async def fake_download(link, pbar_pos=0):
        (tmp_path / link.rsplit("/", 1)[-1]).write_bytes(b"PK\x03\x04fake")
        return True

    dl.fetch_file_list = fake_list
    dl.download_file = fake_download
    written = []
    dl._write_manifest = lambda: written.append(True)

    await dl.run()

    assert written, "the manifest was not written after a clean run"


@pytest.mark.asyncio
async def test_run_refuses_an_empty_listing(tmp_path):
    """An empty listing is a failure, not a quiet exit.

    Returning cleanly here would let the pipeline load an empty month and stamp
    it as processed.
    """
    dl = AsyncDownloader()
    dl.dest_dir = tmp_path

    async def empty_list():
        return []

    dl.fetch_file_list = empty_list
    dl._write_manifest = lambda: pytest.fail("manifest written for an empty month")

    with pytest.raises(RuntimeError, match="Nenhum arquivo encontrado"):
        await dl.run()


@pytest.mark.asyncio
async def test_run_does_not_write_the_manifest_when_a_download_fails(tmp_path):
    """One failed archive must keep the month unstamped."""
    dl = AsyncDownloader()
    dl.dest_dir = tmp_path

    async def fake_list():
        return ["http://test.com/Empresas.zip", "http://test.com/Socios.zip"]

    async def fake_download(link, pbar_pos=0):
        if link.endswith("Socios.zip"):
            raise RuntimeError("connection reset")
        (tmp_path / link.rsplit("/", 1)[-1]).write_bytes(b"PK\x03\x04fake")
        return True

    dl.fetch_file_list = fake_list
    dl.download_file = fake_download
    dl._write_manifest = lambda: pytest.fail("manifest written despite a failed download")

    with pytest.raises(RuntimeError, match="1 downloads failed"):
        await dl.run()


@pytest.mark.asyncio
async def test_run_counts_every_failure_in_the_summary(tmp_path):
    """All three files fail, so the count has to say three, not one."""
    dl = AsyncDownloader()
    dl.dest_dir = tmp_path

    async def fake_list():
        return [f"http://test.com/{n}.zip" for n in ("A", "B", "C")]

    async def fake_download(link, pbar_pos=0):
        raise RuntimeError("boom")

    dl.fetch_file_list = fake_list
    dl.download_file = fake_download
    dl._write_manifest = lambda: pytest.fail("manifest written despite failures")

    with pytest.raises(RuntimeError, match="3 downloads failed"):
        await dl.run()


# --- _write_manifest: what the next two stages trust ---


def test_write_manifest_groups_archives_by_table(tmp_path):
    """The table name comes from the alphabetic prefix of the file stem.

    Receita names archives like ``Empresas10.zip`` and ``Empresas_MM_2025.zip``;
    both have to land under the same table, or the extraction stage reads the
    manifest, finds the table empty, and calls the download corrupt.
    """
    compressed = tmp_path / "data" / "compressed_files"
    compressed.mkdir(parents=True)
    (compressed / "Empresas10.zip").write_bytes(b"x" * 10)
    (compressed / "Empresas_MM_2025.zip").write_bytes(b"x" * 20)
    (compressed / "Socios10.zip").write_bytes(b"x" * 30)
    (compressed / "nao_e_zip.txt").write_bytes(b"ignorado")

    dl = AsyncDownloader()
    dl.dest_dir = compressed

    original = settings.project_root
    try:
        settings.project_root = tmp_path
        dl._write_manifest()
    finally:
        settings.project_root = original

    import json

    data = json.loads((tmp_path / "data" / "manifest.json").read_text(encoding="utf-8"))
    assert set(data["tables"]) == {"Empresas", "Socios"}
    assert sorted(n["name"] for n in data["tables"]["Empresas"]) == [
        "Empresas10.zip",
        "Empresas_MM_2025.zip",
    ]
    assert data["tables"]["Socios"][0]["bytes"] == 30
    assert data["target_date"] == settings.target_date


def test_write_manifest_ignores_non_zip_files(tmp_path):
    """A stray file in the directory must not become a phantom table."""
    compressed = tmp_path / "data" / "compressed_files"
    compressed.mkdir(parents=True)
    (compressed / "Empresas10.zip").write_bytes(b"x")
    (compressed / "notas.txt").write_bytes(b"x")
    (compressed / "Empresas10.zip.part").write_bytes(b"x")

    dl = AsyncDownloader()
    original = settings.project_root
    try:
        settings.project_root = tmp_path
        dl._write_manifest()
    finally:
        settings.project_root = original

    import json

    data = json.loads((tmp_path / "data" / "manifest.json").read_text(encoding="utf-8"))
    assert list(data["tables"]) == ["Empresas"]


# --- resume: the paths where a file can be silently corrupted ---


def _session(content: bytes, total: int, get_status: int = 200):
    sess = AsyncMock(spec=AsyncSession)
    sess.head = AsyncMock(
        return_value=MockResponse(headers={"content-length": str(total)})
    )
    resp = MockResponse(content=content)
    resp.status_code = get_status
    sess.get = AsyncMock(return_value=resp)
    sess.__aenter__.return_value = sess
    sess.__aexit__.return_value = None
    return sess


def _respostas(content: bytes, total: int, respostas):
    """Sessao cujo GET devolve uma sequencia de respostas.

    Um 416 seguido de um 200 sem Range e o caminho de "o servidor nao tinha o
    que eu pedi, baixa de novo".
    """
    sess = AsyncMock(spec=AsyncSession)
    sess.head = AsyncMock(
        return_value=MockResponse(headers={"content-length": str(total)})
    )
    objs = []
    for dados, status in respostas:
        r = MockResponse(content=dados)
        r.status_code = status
        objs.append(r)
    sess.get = AsyncMock(side_effect=objs)
    sess.__aenter__.return_value = sess
    sess.__aexit__.return_value = None
    return sess


def _downloader(tmp_path):
    import tenacity

    dl = AsyncDownloader()
    dl.dest_dir = tmp_path
    dl.download_file.retry.stop = tenacity.stop_after_attempt(1)
    dl.download_file.retry.wait = tenacity.wait_none()
    return dl


def _zip_bytes(payload: bytes = b"conteudo") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("dados.csv", payload)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_complete_valid_archive_is_skipped(tmp_path):
    """A finished, valid archive must not be downloaded again."""
    settings.verify_zip_integrity = True
    payload = _zip_bytes()
    (tmp_path / "Empresas.zip").write_bytes(payload)

    sess = _session(payload, len(payload))
    dl = _downloader(tmp_path)
    with patch("src.downloader.AsyncDownloader._get_session", return_value=sess):
        assert await dl.download_file("http://test.com/Empresas.zip") is True

    sess.get.assert_not_called()
    assert (tmp_path / "Empresas.zip").read_bytes() == payload


@pytest.mark.asyncio
async def test_complete_corrupt_archive_is_downloaded_again(tmp_path):
    """Skipping on size alone would keep a truncated file forever."""
    settings.verify_zip_integrity = True
    corrupt = b"PK\x03\x04 but this is not a real zip"
    (tmp_path / "Empresas.zip").write_bytes(corrupt)

    good = _zip_bytes(b"corrigido")
    sess = _session(good, len(good))
    dl = _downloader(tmp_path)
    with patch("src.downloader.AsyncDownloader._get_session", return_value=sess):
        assert await dl.download_file("http://test.com/Empresas.zip") is True

    assert sess.get.called, "the corrupt archive was not re-downloaded"
    assert (tmp_path / "Empresas.zip").read_bytes() == good


@pytest.mark.asyncio
async def test_partial_archive_resumes_with_a_range_request(tmp_path):
    """A half-written archive continues from where it stopped."""
    settings.verify_zip_integrity = True
    payload = _zip_bytes(b"completo")
    head = len(payload) // 2
    (tmp_path / "Empresas.zip").write_bytes(payload[:head])

    # Um servidor que honra o Range responde 206 com SO o rabo.
    sess = _session(payload[head:], len(payload), get_status=206)
    dl = _downloader(tmp_path)
    with patch("src.downloader.AsyncDownloader._get_session", return_value=sess):
        await dl.download_file("http://test.com/Empresas.zip")

    headers = sess.get.call_args.kwargs.get("headers") or {}
    assert headers.get("Range") == f"bytes={head}-", headers
    assert (tmp_path / "Empresas.zip").read_bytes() == payload


@pytest.mark.asyncio
async def test_range_not_satisfiable_restarts_without_appending(tmp_path):
    """416 means the server has nothing to give; appending would corrupt the file.

    The partial prefix has to be discarded, otherwise the archive ends up with
    two copies of the same bytes and fails the integrity check forever.
    """
    settings.verify_zip_integrity = True
    payload = _zip_bytes(b"completo")
    partial = payload[: len(payload) // 2]
    (tmp_path / "Empresas.zip").write_bytes(partial)

    sess = _respostas(payload, len(payload), [(b"", 416), (payload, 200)])
    dl = _downloader(tmp_path)
    with patch("src.downloader.AsyncDownloader._get_session", return_value=sess):
        await dl.download_file("http://test.com/Empresas.zip")

    assert (tmp_path / "Empresas.zip").read_bytes() == payload, (
        "the partial prefix was appended to instead of being replaced"
    )
    assert sess.get.call_count == 2, "expected a retry without the Range header"
    assert "headers" not in sess.get.call_args_list[1].kwargs, (
        "the retry must not send Range again"
    )
