import argparse
import logging
import sys
import time
from enum import Enum

from . import extract_files, downloader, database_loader, check_update
from .settings import setup_logging, settings
from .state import is_month_complete, stages_not_completed, start_run, state


class PipelineStep(Enum):
    CHECK = "check"
    DOWNLOAD = "download"
    EXTRACT = "extract"
    LOAD = "load"
    CONSTRAINTS = "constraints"


def main():
    setup_logging()
    logger = logging.getLogger("orchestrator")

    PIPELINE_MAP = {
        PipelineStep.DOWNLOAD: downloader.run_download,
        PipelineStep.EXTRACT: extract_files.run_extraction,
        PipelineStep.LOAD: database_loader.run_loader,
        PipelineStep.CONSTRAINTS: database_loader.run_constraints,
    }

    def run_pipeline(args):
        start_time = time.time()
        logger.info("🚀 Iniciando Pipeline CNPJ...")

        # Update settings from args
        if args.rate_limit_per_sec is not None:
            settings.rate_limit_per_sec = args.rate_limit_per_sec
        if args.skip_zip_verify:
            settings.verify_zip_integrity = False
        if args.max_workers is not None:
            settings.max_workers = args.max_workers

        # Check updates first. A dry run must not touch anything, and
        # check_updates deletes the downloaded and extracted trees when it finds
        # a newer release, so it has to run with skip_clean.
        date = check_update.check_updates(skip_clean=args.resume or args.dry_run)
        if date is None and not args.resume:
            logger.info("Dados já estão atualizados ou não foi possível verificar.")
            if not args.force:
                 return
        
        # The resume ledger must be keyed on the folder being processed now, not
        # on the one recorded by the previous run. The module-level state object
        # was built at import from last_version_processed.txt, so without this a
        # resume would skip stages belonging to the previous month and then
        # reload that month's data under the new month's name.
        if date and date != state.date:
            state.date = date
            start_run(date)

        # Dry Run Check
        if args.dry_run:
            logger.info(f"Dry-run ativo. Etapas que seriam executadas (Resume={args.resume}):")
            logger.info(f"  - {PipelineStep.CHECK.value}")
            for step_name in PIPELINE_MAP:
                if not args.force and not args.resume:
                    # Default run all
                     logger.info(f"  - {step_name.value}")
                elif args.resume and state.should_skip(step_name.value):
                     logger.info(f"  - {step_name.value} [SKIP]")
                else:
                     logger.info(f"  - {step_name.value}")
            return

        for step_name, step_func in PIPELINE_MAP.items():
            # Special handling for LOAD arguments
            kwargs = {}
            if step_name == PipelineStep.LOAD:
                if args.only:
                    kwargs['only'] = args.only
                if args.exclude:
                    kwargs['exclude'] = args.exclude

            # Resume logic: if resume is ON, skip completed.
            # If force is ON, never skip.
            # If neither, run all (standard behavior for a full run usually implies running what's needed, but let's stick to existing logic).
            # Existing logic in old main.py: if not resume, run all. if resume, check status.
            
            should_run = True
            if args.resume:
                if state.should_skip(step_name.value):
                    should_run = False
            
            # Force overrides resume skip
            if args.force:
                should_run = True

            if not should_run:
                logger.info(f"⏭️  [SKIP] {step_name.value.upper()}")
                continue

            logger.info(f"▶️  [RUN] {step_name.value.upper()}...")
            state.update(step_name.value, "running")
            try:
                if step_name == PipelineStep.LOAD:
                    step_func(**kwargs)
                else:
                    step_func()
                
                state.update(step_name.value, "completed")
                logger.info(f"✅ [OK] {step_name.value.upper()}")
            except KeyboardInterrupt:
                logger.warning("\n⚠️ Interrompido pelo usuário.")
                state.update(step_name.value, "failed")
                sys.exit(130)
            except SystemExit:
                raise
            except Exception as e:
                logger.error(f"❌ [ERRO] {step_name.value.upper()}: {e}", exc_info=True)
                state.update(step_name.value, "failed")
                sys.exit(1)
        
        # Post-pipeline actions
        if args.run_queries:
             logger.info("Executando queries customizadas...")
             database_loader.run_queries_in_dir(settings.queries_dir)

        # Only a fully complete run may stamp the month. A run that failed a
        # stage, or that was restricted with --only/--exclude and left most
        # tables unloaded, must stay unstamped so the next run retries instead
        # of treating a partial month as finished.
        partial_load = bool(args.only or args.exclude)
        outstanding = stages_not_completed(date) if date else []
        if date and not partial_load and is_month_complete(date) and not outstanding:
            check_update.update_local_version(date)
        elif date:
            if partial_load:
                logger.warning(
                    f"Carga parcial (--only/--exclude): {date} não foi marcado como processado."
                )
            else:
                logger.warning(
                    f"{date} NÃO foi marcado como processado; etapas pendentes: "
                    f"{', '.join(outstanding)}. A próxima execução tentará de novo."
                )

        elapsed = time.time() - start_time
        logger.info(f"🏁 Pipeline Finalizado com Sucesso em {elapsed:.2f}s")

    parser = argparse.ArgumentParser(description="CNPJ ETL Pipeline")
    parser.add_argument("--force", action="store_true", help="Ignora o histórico e roda tudo.")
    parser.add_argument("--resume", action="store_true", help="Resume de onde parou (baseado no estado).")
    parser.add_argument("--dry-run", action="store_true", help="Simula a execução.")
    parser.add_argument("--step", choices=[s.value for s in PipelineStep], help="Executa apenas uma etapa.")
    
    # Load specific args
    parser.add_argument("--only", nargs="*", help="Apenas tabelas específicas no LOAD")
    parser.add_argument("--exclude", nargs="*", help="Excluir tabelas no LOAD")
    parser.add_argument("--run-queries", action="store_true", help="Rodar queries SQL após o load")
    
    # Settings overrides
    parser.add_argument("--rate-limit-per-sec", type=int, help="Limite de taxa de download")
    parser.add_argument("--skip-zip-verify", action="store_true", help="Pular verificação de zip")
    parser.add_argument("--max-workers", type=int, help="Máximo de workers")

    args = parser.parse_args()

    if args.step:
        # Single step execution
        step_value = args.step
        if step_value == PipelineStep.CHECK.value:
            # A lone check is a query. Cleaning here would delete every archive
            # and every extracted volume just for asking whether a new month
            # exists; the pipeline cleans on its own when it starts a new month.
            check_update.check_updates(skip_clean=True)
        else:
            try:
                func = PIPELINE_MAP.get(PipelineStep(step_value))
                if func:
                    # Update settings for single step too
                    if args.rate_limit_per_sec is not None:
                        settings.rate_limit_per_sec = args.rate_limit_per_sec
                    if args.skip_zip_verify:
                        settings.verify_zip_integrity = False
                    if args.max_workers is not None:
                        settings.max_workers = args.max_workers

                    state.update(step_value, "running")
                    kwargs = {}
                    if PipelineStep(step_value) == PipelineStep.LOAD:
                        if args.only:
                            kwargs['only'] = args.only
                        if args.exclude:
                            kwargs['exclude'] = args.exclude
                        
                    func(**kwargs)
                    state.update(step_value, "completed")
                else:
                    logger.error(f"Etapa desconhecida: {step_value}")
                    sys.exit(2)
            except KeyboardInterrupt:
                logger.warning("Interrompido.")
                state.update(step_value, "failed")
                sys.exit(130)
            except Exception as e:
                logger.error(f"Erro no step {step_value}: {e}")
                state.update(step_value, "failed")
                sys.exit(1)
    else:
        run_pipeline(args)


if __name__ == "__main__":
    main()
