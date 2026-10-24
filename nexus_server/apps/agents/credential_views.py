from django.utils.decorators import method_decorator
from django.views.decorators.debug import sensitive_post_parameters
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from .delegate_tokens import interaction_token
from . import credential_services as credentials
from django.http import HttpResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET
from pathlib import Path
import secrets


@require_GET
@ensure_csrf_cookie
def authorization_page(request, authorization_id):
    nonce = secrets.token_urlsafe(24)
    html = Path(__file__).with_name('credential_page.html').read_text(encoding='utf-8').replace('__NONCE__', nonce)
    response = HttpResponse(html, content_type='text/html; charset=utf-8')
    response['Cache-Control'] = 'no-store, private'
    response['Referrer-Policy'] = 'no-referrer'
    response['X-Frame-Options'] = 'DENY'
    response['X-Content-Type-Options'] = 'nosniff'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    response['Content-Security-Policy'] = f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    return response


class NoStoreView(APIView):
    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'no-store, private'
        response['Referrer-Policy'] = 'no-referrer'
        return response


class InternalCredentialRequestView(NoStoreView):
    permission_classes = [AllowAny]
    authentication_classes = []
    def post(self, request, run_id):
        return Response(credentials.request_authorization(run_id, interaction_token(request), request.data.get('provider'), request.data.get('base_url'), request.data.get('model'), request.data.get('force_new',False), request.data.get('reuse_existing',False)))


class InternalCredentialDetailView(NoStoreView):
    permission_classes = [AllowAny]
    authentication_classes = []
    def get(self, request, run_id, authorization_id):
        return Response(credentials.internal_status(run_id, interaction_token(request), authorization_id))
    def post(self, request, run_id, authorization_id):
        return Response(credentials.internal_status(run_id, interaction_token(request), authorization_id, resolve=True))


@method_decorator(sensitive_post_parameters('secret'), name='dispatch')
class CredentialAuthorizationView(NoStoreView):
    def get(self, request, authorization_id):
        return Response(credentials.browser_details(credentials.browser_row(request, authorization_id)))
    def post(self, request, authorization_id):
        return Response(credentials.decide(request, authorization_id))
