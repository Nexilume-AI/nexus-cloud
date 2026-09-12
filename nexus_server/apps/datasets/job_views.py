from rest_framework import exceptions
from rest_framework.response import Response
from rest_framework.views import APIView

from .import_jobs import change_job, enqueue, owned_jobs, payload


class DatasetImportJobsView(APIView):
    def get(self, request, dataset_id):
        from .pagination import page
        return Response(page(request, owned_jobs(request, dataset_id), payload))

    def post(self, request, dataset_id):
        job = enqueue(request, dataset_id, request.data.get("kind"), request.data.get("inputs", {}),
            str(request.data.get("request_key") or ""))
        return Response(payload(job), status=202 if job.state in {"queued", "running"} else 200)


class DatasetRetentionView(APIView):
    def get(self, request, dataset_id):
        from .services import get_mutable_dataset
        from .operations import retention_status
        return Response(retention_status(get_mutable_dataset(request=request, dataset_id=dataset_id)))


class DatasetImportJobView(APIView):
    def get(self, request, dataset_id, job_id):
        job = owned_jobs(request, dataset_id).filter(pk=job_id).first()
        if not job:
            raise exceptions.NotFound()
        return Response(payload(job))

    def post(self, request, dataset_id, job_id):
        return Response(payload(change_job(request, dataset_id, job_id, request.data.get("action"))))
