# Design: run manifest and the completeness contract

Status: **implemented**. The manifest writer (`src/manifest.py`), the verification
in `extract` (`src/extract_files.py`), the write in `download`
(`src/downloader.py`) and the completion contract (`src/state.py`,
`src/__main__.py`) are in the code and covered by `tests/test_manifest_contract.py`.
Where this document and the code disagree, **the code wins**. Two items below are
still open work and are marked as such.

## The problem that motivated the design (state before the implementation)

A stage could lose work and the next stage could not tell. A corrupt zip was
logged and skipped while `run_extraction` printed success. A changed directory
listing yielded zero links and `run_download` logged an error without raising.
Either way `update_local_version` stamped the month as processed, and the next run
skipped it.

All of that is fixed: `extract_files.py` collects the corrupt archives and raises
`RuntimeError` at the end of the stage, `downloader.py` treats an empty listing as
a hard failure and raises if any download failed, and `__main__.py` stamps the
month only when every stage completed.

`process_and_load_file` still accepts any non-empty set of volumes —
`_volumes_available` in `src/database_loader.py` rejects only an empty directory or
a zero-byte volume. Per-volume completeness is guaranteed in `extract`, against the
manifest, not in `load`.

`pd.read_csv` on a truncated file succeeds and simply yields fewer rows; the
`rows_read` accounting below is what makes that visible.

## Why counting files does not fix it

The Receita ships a different number of volumes every month. Any hardcoded
expected count is wrong within a month, so the check either cries wolf or is
disabled. The expectation has to come from what *this run* observed, not from a
constant.

The deeper gap was that the per-table summary had no denominator. `rows_total`
counts rows that landed; nothing counted rows read from the CSV. "The table
finished" and "the table was abandoned after chunk 3" produced the same summary.
The denominator now exists: `rows_read` and `volumes_read` are counted before any
gate or filter is applied, in `src/database_loader.py`.

## Design

Three parts. Each stage records what it was given and what it produced; the next
stage checks.

### 1. The manifest: what this run expected

`download` writes `data/manifest.json` after a successful run — only when no
download failed — recording for each table the archives found **on disk** after the
run (`compressed_dir/*.zip`), not the ones seen in the directory listing. That
deliberate choice also covers archives a previous run left behind:

```json
{
  "target_date": "2025-11",
  "generated_at": "2026-10-07T22:10:00+00:00",
  "tables": {
    "Empresas":   [{"name": "Empresas-01.zip", "bytes": 13144999}],
    "Estabelecimentos": [{"name": "Estabelecimentos-01.zip", "bytes": 46642384}]
  }
}
```

Written to a temporary file and renamed, so an interrupted download leaves no
partial manifest. **The absence of a manifest is itself a signal** rather than
something to be treated as an empty expectation.

A run that downloads zero volumes is a hard failure, not a warning. There is
nothing to extract and nothing to load.

### 2. Verification at each boundary

`extract` reads the manifest and, for every expected archive, confirms the CSV
it should have produced. A missing one names the file and fails. `extract_single_zip`
stops swallowing `BadZipFile`: failures are collected and reported at the end of
the stage instead of disappearing.

`load` counts rows as they stream out of each volume and reports `rows_read`
alongside `rows_total`, so the summary finally reconciles against the source:

| Field | Meaning |
|---|---|
| `rows_read` | rows the extracted CSVs contained |
| `rows_total` | rows that landed in the table |
| `rows_discarded` | the difference, itemised by reason |
| `volumes_read` | how many volumes were opened |

**Not implemented — still open work.** The per-reason counters
(`quality_gate_rows_discarded`, `invalid_cnpj_rows_skipped`,
`critical_key_null_rows_skipped`, `duplicate_key_rows_dropped`) are written to the
summary, but nothing compares their sum against `rows_discarded`. Until that check
exists, rows could in principle vanish without a recorded reason.

### 3. The completion contract

The month is stamped processed only when every stage reported complete **and
verified**. If one table's extraction failed, the run ends in a partial state and
`update_local_version` is not called, so the next run retries instead of treating
a partial month as finished.

Failure granularity today is **per stage, not per table**: `run_extraction`
accumulates failures across all groups and raises `RuntimeError` if there is even
one, and `__main__.py` marks the stage as `failed` and exits non-zero. One corrupt
`Estabelecimentos` volume therefore discards the whole stage and leaves the month
unstamped. Per-table isolation is still open work.

## What this does not solve

- **Truncated CSVs that still parse.** Row counts catch a crash mid-write only
  when it drops rows entirely. A half-written last line usually reads as a short
  final row. Catching that needs a checksum per volume in the manifest.
- **The Receita genuinely changing the schema.** A new column in a volume would
  surface as a parse error rather than a count mismatch, which is the right
  behaviour, but it is a different failure.
- **`pd.read_csv` being strict about types.** A garbage value in a typed column
  still aborts the whole volume before any of this machinery runs. Already tracked
  as an xfail.

## Work it implies

| Piece | Touches | Status |
|---|---|---|
| Manifest writer | `src/downloader.py` | done |
| Manifest reader + verification | `src/extract_files.py` | done |
| `rows_read` / `volumes_read` accounting | `src/database_loader.py` | done |
| Completion contract | `src/__main__.py`, `src/state.py` | done |
| Status per **stage** in the run ledger (`data/runs.json`) | `src/state.py` | done |
| Reconcile `rows_discarded` against the per-reason counters | `src/database_loader.py` | open |
| Isolate failures per table instead of per stage | `src/extract_files.py` | open |