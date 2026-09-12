from rest_framework import exceptions
from rest_framework.response import Response
from rest_framework.views import APIView
from .manifests import public_entry
from .pagination import page, legacy_page
from .services import get_dataset


def manifest_page(request, version, serialize):
    if not version:
        return {"items": [], "total": 0, "next_cursor": None, "summary": {}}
    if version.snapshot_json.get("format") == 2:
        return page(request, version.entries.all(), lambda entry: serialize(entry.payload))
    legacy = version.snapshot_json.get("files", [])
    valid = [row for row in legacy if isinstance(row, dict) and row.get("file_id")]
    result = legacy_page(request, valid, serialize)
    result["unavailable_entries"] = len(legacy) - len(valid)
    if result["unavailable_entries"]:
        result["warning_code"] = "LEGACY_MANIFEST_INCOMPLETE"
    return result


class DatasetVersionFilesView(APIView):
    def get(self, request, dataset_id, version_id):
        dataset = get_dataset(request=request, dataset_id=dataset_id)
        version = dataset.versions.filter(pk=version_id, status="active").first()
        if not version:
            raise exceptions.NotFound()
        return Response(manifest_page(request, version, lambda item: public_entry(version, item)))


from .presentation import view_export


def __getattr__(name):
    if name == "DatasetAcquisitionFilesView":
        return view_export(name)
    raise AttributeError(name)
