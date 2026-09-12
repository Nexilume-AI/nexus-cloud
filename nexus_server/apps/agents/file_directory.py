"""Caller-bound file directory pages. Reads never materialize output events."""
from django.db.models import Q
from rest_framework import serializers
from .observability import page


def reference_output(run, artifact_id):
    from rest_framework import exceptions
    artifact = run.output_artifacts.filter(pk=artifact_id, snapshot_status="ready").exclude(status="deleted").first()
    if (not artifact or not artifact.snapshot_object_key or artifact.scan_status != "passed"
            or artifact.policy_status != "approved"):
        raise exceptions.NotFound("Output is unavailable or has not passed security checks.")
    return artifact


def validate_reference(row):
    if row.source_kind == "run_output":
        from rest_framework import exceptions
        artifact = reference_output(row.run, row.source_label)
        if (artifact.snapshot_object_key != row.object_key or artifact.sha256 != row.sha256
                or artifact.snapshot_storage_backend != row.storage_backend):
            raise exceptions.NotFound("Output snapshot has changed. Select it again.")


class FileQuery(serializers.Serializer):
    cursor = serializers.CharField(required=False, allow_blank=True, max_length=2048)
    limit = serializers.IntegerField(default=50, min_value=1, max_value=100)
    q = serializers.CharField(required=False, allow_blank=True, max_length=160)
    turn = serializers.RegexField(r"^(all|unknown|[1-9][0-9]{0,8})$", default="all")


def directory_page(request, run, queryset, kind, serialize):
    query = FileQuery(data=request.query_params)
    query.is_valid(raise_exception=True)
    params = query.validated_data
    text = params.get("q", "")
    if text:
        name = "name" if kind == "input" else "original_file_name"
        source = "source_label" if kind == "input" else "producer_step"
        queryset = queryset.filter(Q(**{name + "__icontains": text}) | Q(content_type__icontains=text) | Q(**{source + "__icontains": text}))
    turn = params["turn"]
    if turn == "unknown":
        queryset = queryset.filter(turn_index__isnull=True)
    elif turn != "all":
        if kind == "input":
            # Reuse is persisted in messages; preserve the original first-use
            # turn on the immutable file while supporting later-turn filtering.
            from .file_turn_query import input_turn_queryset
            queryset = input_turn_queryset(queryset, run, int(turn))
        else:
            queryset = queryset.filter(turn_index=int(turn))
    rows, cursor = page(queryset, params,
        ["private-files", str(run.consumer_tenant_id), str(run.consumer_project_id),
         run.caller_subject_hash, str(run.pk), kind])
    return {"items": [serialize(row) for row in rows], "next_cursor": cursor}
