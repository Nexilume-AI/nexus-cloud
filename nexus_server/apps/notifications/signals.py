from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender="agents.AgentDisplayRun")
def run_changed(sender, instance, raw=False, update_fields=None, **kwargs):
    if raw or (update_fields is not None and not {"status", "completed_at"}.intersection(update_fields)):
        return
    from .sources import record_run
    record_run(instance)


@receiver(post_save, sender="agents.AgentRunInteraction")
def question_changed(sender, instance, raw=False, **kwargs):
    if not raw:
        from .sources import record_interaction
        record_interaction(instance)


@receiver(post_save, sender="agents.AgentPythonBuild")
def build_changed(sender, instance, raw=False, **kwargs):
    if not raw:
        from .sources import record_build
        record_build(instance)


@receiver(post_save, sender="jobs.Job")
def job_changed(sender, instance, raw=False, **kwargs):
    if not raw:
        from .sources import record_job
        record_job(instance)


@receiver(post_save, sender="datasets.DatasetImportJob")
def dataset_import_changed(sender, instance, raw=False, **kwargs):
    if not raw:
        from .sources import record_dataset_import
        record_dataset_import(instance)
