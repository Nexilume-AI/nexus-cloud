from django.db.models import Count, Q
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView
from .pagination import page
from .services import get_mutable_dataset
from .training_services import sample_rows, sample_summary, sample_file, read_sample, ingest, annotate, import_run, export_sft


class IngestSerializer(serializers.Serializer):
    trajectory = serializers.JSONField()
    request_key = serializers.CharField(max_length=128)
    rights_confirmed = serializers.BooleanField()
    correction_of = serializers.UUIDField(required=False)


class AnnotationSerializer(serializers.Serializer):
    revision = serializers.IntegerField(min_value=1)
    decision = serializers.ChoiceField(choices=["pending", "approved", "excluded"])
    outcome = serializers.ChoiceField(choices=["unknown", "success", "failure"])
    score = serializers.FloatField(min_value=0, max_value=1, allow_null=True, default=None)
    feedback = serializers.CharField(max_length=2000, allow_blank=True, default="")
    feedback_source = serializers.ChoiceField(choices=["human", "evaluator", "user_feedback"], default="human")
    trainable_assistant_indices = serializers.ListField(child=serializers.IntegerField(min_value=0, max_value=999), max_length=1000, allow_null=True, default=None)


class RunImportSerializer(serializers.Serializer):
    agent_id = serializers.UUIDField()
    run_ids = serializers.ListField(child=serializers.UUIDField(), min_length=1, max_length=20)
    request_key = serializers.CharField(max_length=80)
    rights_confirmed = serializers.BooleanField()


class ExportSerializer(serializers.Serializer):
    request_key = serializers.CharField(max_length=128)
    seed = serializers.CharField(max_length=64, default="nexus-training-v1")
    validation_percent = serializers.IntegerField(min_value=0, max_value=99, default=10)
    test_percent = serializers.IntegerField(min_value=0, max_value=99, default=10)
    include_failures = serializers.BooleanField(default=False)


def validated(cls, data):
    serializer = cls(data=data)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


class TrainingSamplesView(APIView):
    def get(self, request, dataset_id):
        dataset = get_mutable_dataset(request=request, dataset_id=dataset_id)
        rows = sample_rows(dataset)
        summary = rows.aggregate(samples=Count("pk"), approved=Count("pk", filter=Q(metadata_json__training__annotation__decision="approved")),
            failed=Count("pk", filter=Q(metadata_json__training__execution_status="failed")),
            success=Count("pk", filter=Q(metadata_json__training__annotation__outcome="success")))
        for field in ("decision", "outcome"):
            if request.query_params.get(field):
                rows = rows.filter(**{f"metadata_json__training__annotation__{field}": request.query_params[field]})
        if request.query_params.get("q"):
            rows = rows.filter(metadata_json__training__context__task_id__icontains=request.query_params["q"][:256])
        return Response(page(request, rows, sample_summary, summary=summary))

    def post(self, request, dataset_id):
        return Response(sample_summary(ingest(request, dataset_id, **validated(IngestSerializer, request.data))), status=201)


class TrainingSampleView(APIView):
    def get(self, request, dataset_id, sample_id):
        _, file = sample_file(request, dataset_id, sample_id)
        return Response({**sample_summary(file), "trajectory": read_sample(file)})

    def patch(self, request, dataset_id, sample_id):
        file = annotate(request, dataset_id, sample_id, validated(AnnotationSerializer, request.data))
        return Response({**sample_summary(file), "trajectory": read_sample(file)})


class TrainingRunImportView(APIView):
    def post(self, request, dataset_id):
        from rest_framework.exceptions import APIException
        args = validated(RunImportSerializer, request.data)
        get_mutable_dataset(request=request, dataset_id=dataset_id)
        results = []
        for run_id in dict.fromkeys(args.pop("run_ids")):
            try:
                sample = import_run(request, dataset_id, run_id=run_id,
                    **{**args, "request_key": f"{args['request_key']}:{run_id}"})
                results.append({"run_id": str(run_id), "sample": sample_summary(sample)})
            except APIException as error:
                results.append({"run_id": str(run_id), "error": error.detail})
        return Response({"results": results})


class TrainingExportsView(APIView):
    def get(self, request, dataset_id):
        dataset = get_mutable_dataset(request=request, dataset_id=dataset_id)
        return Response(page(request, dataset.versions.filter(snapshot_json__training__isnull=False), export_summary))

    def post(self, request, dataset_id):
        version = export_sft(request, dataset_id, **validated(ExportSerializer, request.data))
        return Response(export_summary(version), status=201)


def export_summary(version):
    base = f"/api/v1/datasets/{version.dataset_id}/versions/{version.id}"
    return {"id": str(version.id), "version": version.version, "created_at": version.created_at.isoformat(),
        "training": {k: v for k, v in version.snapshot_json["training"].items() if k != "request_key"},
        "files": [{"id": str(row.file_id), "file_name": row.payload["file_name"], "size_bytes": row.payload["size_bytes"],
                   "download_url": f"{base}/files/{row.file_id}/download/"} for row in version.entries.all()]}
