"""Personal collections only; publishing and paid acquisition are not installed."""
from types import SimpleNamespace
from rest_framework import exceptions
from .resource_catalog import PersonalResourceCatalog


class PersonalDatasetPolicy:
    def ensure_operational_alerts(self, *, tenant):
        from .monitoring_defaults import ensure_operational_alerts
        return ensure_operational_alerts(tenant)

    def maintain_data_assets(self):
        from .monitoring_defaults import maintain_data_assets
        return maintain_data_assets()

    def dataset_retention_status(self, *, dataset):
        from .models import PersonalInstallation
        installation = PersonalInstallation.objects.select_related("owner").get(slot=1)
        request = SimpleNamespace(user=installation.owner, META={}, query_params={})
        current, _ = PersonalResourceCatalog()._resource(
            request=request, resource_type="dataset", obj=dataset)
        return {"mode": "preserve", "physical_deletion_enabled": False,
            "version_count": current.versions.exclude(status="deleted").count(),
            "has_acquisitions": False,
            "active_imports": current.import_jobs.filter(state__in=["queued", "running"]).count(),
            "retained_bytes": current.size_bytes,
            "reason": "Versioned objects and files are retained. Deleting a collection only removes it from the catalog."}

    def dataset_related_fields(self):
        return ("quota",)

    def require_dataset_admin(self, *, request, tenant):
        PersonalResourceCatalog()._context(request, tenant)

    def visible_datasets(self, *, user, tenant, dataset_id=None):
        from apps.datasets.models import Dataset
        request = SimpleNamespace(user=user, META={}, query_params={})
        queryset = Dataset.objects.all()
        if dataset_id is not None:
            queryset = queryset.filter(pk=dataset_id)
        return PersonalResourceCatalog().discoverable_resource_queryset(queryset,
            request=request, tenant=tenant, resource_type="dataset")

    def can_read_dataset(self, *, request, dataset):
        try:
            PersonalResourceCatalog()._resource(request=request, resource_type="dataset", obj=dataset)
        except exceptions.APIException:
            return False
        return True

    def can_manage_dataset(self, *, user, dataset):
        return self.can_read_dataset(request=SimpleNamespace(user=user, META={}, query_params={}), dataset=dataset)

    def set_visibility(self, *, request, dataset_id, visibility):
        if visibility != "private":
            raise exceptions.NotFound("Dataset publishing is not available in this distribution.")
        from apps.datasets.services import get_mutable_dataset, log_write
        dataset = get_mutable_dataset(request=request, dataset_id=dataset_id)
        dataset.visibility = "private"
        dataset.save(update_fields=["visibility", "updated_at"])
        log_write(request=request, action="datasets.visibility.set", dataset=dataset, metadata={"visibility": "private"})
        return dataset

    def _unavailable(self, **kwargs):
        # Never simulate a purchase, price, entitlement or successful download.
        raise exceptions.NotFound("Dataset publishing and acquisition are not available in this distribution.")

    search_public_datasets = _unavailable
    list_marketplace_datasets = _unavailable
    get_marketplace_dataset = _unavailable
    get_marketplace_dataset_version = _unavailable
    pull_marketplace_dataset = _unavailable
    acquisition_queryset = _unavailable
    list_dataset_acquisitions = _unavailable
    get_dataset_acquisition = _unavailable
    dataset_acquisition_payload = _unavailable
    set_pricing = _unavailable
    effective_dataset_pricing = _unavailable
    calculate_dataset_pull_cost = _unavailable
    dataset_pull_currency = _unavailable
    download_marketplace_dataset_file = _unavailable
    download_dataset_acquisition_file = _unavailable
    has_marketplace_dataset_entitlement = _unavailable
    validate_dataset_publication = _unavailable
    validate_dataset_file_publication = _unavailable
