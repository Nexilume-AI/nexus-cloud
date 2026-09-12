"""One operational URL inventory, without reporting/export/financial endpoints."""
from apps.metrics.operational_urls import urlpatterns as operational
from apps.metrics.operational_catalog_urls import urlpatterns as catalogs

urlpatterns = [*operational, *catalogs]
