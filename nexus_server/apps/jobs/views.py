from __future__ import annotations

from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import JobEventSerializer, JobSerializer
from .services import get_job, list_job_events, list_jobs


class JobListView(APIView):
    def get(self, request):
        return Response(JobSerializer(list_jobs(request=request), many=True).data)


class JobDetailView(APIView):
    def get(self, request, job_id):
        return Response(JobSerializer(get_job(request=request, job_id=job_id)).data)


class JobEventListView(APIView):
    def get(self, request, job_id):
        return Response(JobEventSerializer(list_job_events(request=request, job_id=job_id), many=True).data)
