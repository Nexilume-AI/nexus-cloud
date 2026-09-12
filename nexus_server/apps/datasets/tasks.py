from celery import shared_task


@shared_task(acks_late=True, reject_on_worker_lost=True, time_limit=14400)
def process_dataset_import(job_id):
    from .import_jobs import run_job
    run_job(job_id)


@shared_task
def dispatch_dataset_imports():
    from .import_jobs import pending_jobs, claim_dispatch
    from .models import DatasetImportJob
    # Broker failure leaves the job queued; the next Beat tick retries dispatch.
    for job_id in pending_jobs():
        dispatch_id = claim_dispatch(job_id)
        if dispatch_id:
            try:
                process_dataset_import.apply_async(args=[str(job_id)], queue="dataset-imports")
            except Exception:
                DatasetImportJob.objects.filter(pk=job_id, state="queued", lease_id=dispatch_id).update(lease_id=None, lease_expires_at=None)
                raise


@shared_task
def cleanup_dataset_transfers():
    from apps.agents.file_transfers import cleanup
    cleanup()
    from .operations import maintain_data_assets
    from .models import DatasetTransfer
    from datetime import timedelta
    from django.utils import timezone

    cleaned = maintain_data_assets()["cleaned"]
    # Logical-download deduplication lasts 24h. Retain records for 48h, then
    # prune bounded batches; financial/capability ledger entries are untouched.
    stale = list(DatasetTransfer.objects.filter(kind="export", created_at__lt=timezone.now() - timedelta(hours=48),
        expires_at__lt=timezone.now()).values_list("pk", flat=True)[:1000])
    removed, _ = DatasetTransfer.objects.filter(pk__in=stale).delete()
    return {"cleaned": cleaned, "expired_download_sessions": removed}


@shared_task(acks_late=True, reject_on_worker_lost=True, time_limit=14400)
def process_agent_file_transfers():
    from apps.agents.file_transfers import process_one
    process_one()
