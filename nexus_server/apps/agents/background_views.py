import secrets
from pathlib import Path
from django.http import HttpResponse, FileResponse, StreamingHttpResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET
from rest_framework.permissions import AllowAny
from rest_framework.parsers import MultiPartParser
from rest_framework.negotiation import DefaultContentNegotiation
from rest_framework.response import Response
from rest_framework import exceptions
from .credential_views import NoStoreView
from .delegate_tokens import interaction_token
from . import background_jobs as jobs


@require_GET
@ensure_csrf_cookie
def job_page(request, job_id):
    nonce=secrets.token_urlsafe(24)
    html=Path(__file__).with_name('background_page.html').read_text(encoding='utf-8').replace('__NONCE__',nonce)
    response=HttpResponse(html)
    response['Cache-Control']='no-store, private';response['Referrer-Policy']='no-referrer'
    response['X-Frame-Options']='DENY';response['X-Content-Type-Options']='nosniff';response['X-Robots-Tag']='noindex, nofollow'
    response['Content-Security-Policy']=f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    return response


def sse(iterator):
    response=StreamingHttpResponse(iterator,content_type='text/event-stream')
    response['Cache-Control']='no-store, private';response['X-Accel-Buffering']='no'
    response['Referrer-Policy']='no-referrer'
    return response


class StreamNegotiation(DefaultContentNegotiation):
    def select_renderer(self,request,renderers,format_suffix=None):
        # StreamingHttpResponse supplies its own media type. Retain JSON for
        # pre-stream permission/validation errors instead of rejecting SSE Accept.
        if 'text/event-stream' in request.headers.get('Accept',''):
            return renderers[0],renderers[0].media_type
        return super().select_renderer(request,renderers,format_suffix)


class RegisterJobView(NoStoreView):
    authentication_classes=[]
    permission_classes=[AllowAny]
    def post(self,request,run_id):
        return Response(jobs.register(run_id,interaction_token(request),request.data))


class ProducerView(NoStoreView):
    authentication_classes=[]
    permission_classes=[AllowAny]
    @staticmethod
    def token(request):return request.headers.get('X-Nexus-Job-Token','')


class IngestView(ProducerView):
    def post(self,request,job_id):
        return Response(jobs.ingest(job_id,self.token(request),request.data.get('events')))


class CommandView(ProducerView):
    content_negotiation_class=StreamNegotiation
    def get(self,request,job_id):
        jobs.producer(job_id,self.token(request))
        return sse(jobs.stream(job_id,commands=True))


class UploadView(ProducerView):
    parser_classes=[MultiPartParser]
    def post(self,request,job_id):
        # Authenticate before reading the multipart body.
        jobs.producer(job_id,self.token(request))
        return Response(jobs.upload(job_id,self.token(request),request.data.get('path'),request.FILES.get('file')))


class JobView(NoStoreView):
    def get(self,request,job_id):return Response(jobs.snapshot(jobs.owner(request,job_id)))
    def post(self,request,job_id):
        if request.data!={'action':'cancel'}:raise exceptions.ValidationError('Only cancellation is supported.')
        job=jobs.owner(request,job_id)
        if job.state.get('status') not in jobs.TERMINAL:
            job.cancel_requested=True;job.save(update_fields=['cancel_requested'])
        jobs.notify(job.id)
        return Response(jobs.snapshot(job))


class EventsView(NoStoreView):
    content_negotiation_class=StreamNegotiation
    def get(self,request,job_id):
        job=jobs.owner(request,job_id)
        try:after=int(request.headers.get('Last-Event-ID',request.query_params.get('after','0')))
        except ValueError:raise exceptions.ValidationError('Invalid event cursor.') from None
        if not 0<=after<=job.last_seq:raise exceptions.ValidationError('Invalid event cursor.')
        return sse(jobs.stream(job_id,after))


class DownloadView(NoStoreView):
    def get(self,request,job_id):
        job=jobs.owner(request,job_id)
        path=request.query_params.get('path','');item=job.artifacts.get(path)
        if not item:raise exceptions.NotFound()
        response=FileResponse(jobs.storage().open(item['storage_name'],'rb'),as_attachment=True,filename=Path(path).name,content_type='application/octet-stream')
        response['X-Content-Type-Options']='nosniff';response['Cache-Control']='no-store, private'
        response['X-Artifact-SHA256']=item['sha256']
        return response
