"""Bounded, immutable Community assets; never serve workload storage or Cloud UI.

The installer supplies an operator-controlled web directory. Each process loads
one verified snapshot; replacing a release requires restarting the web workers.
The inventory is corruption/edition protection, not publisher authentication.
"""
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from django.conf import settings
from django.http import HttpResponse, HttpResponseNotModified, JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie


MANIFEST = 'community-assets.json'
MAX_FILE = 32 * 1024**2
MAX_TOTAL = 64 * 1024**2
_PATH = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_][A-Za-z0-9_.-]*)*\Z')
_PAGES = re.compile(r'/(?:login|gateway|playground|model-pool|models|deployments|routers|agents|data-assets|remote-workspaces|mobile|openwrt-routers|providers|observability|inbox|audit|settings)/?\Z')
_DETAIL = re.compile(r'/(?:agents/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)?|agent-runs/[A-Za-z0-9_-]+/display)/?\Z')
_MIME = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
         '.mjs': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
         '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
         '.webp': 'image/webp', '.ico': 'image/x-icon', '.woff': 'font/woff', '.woff2': 'font/woff2',
         '.ttf': 'font/ttf', '.py': 'text/plain; charset=utf-8', '.txt': 'text/plain; charset=utf-8'}


def _read(root, name, limit):
    if not _PATH.fullmatch(name) or name.endswith('.map'):
        raise ValueError()
    target = root
    for part in name.split('/'):
        target = target / part
        if target.is_symlink() or (hasattr(target, 'is_junction') and target.is_junction()):
            raise ValueError()
    if not target.resolve(strict=True).is_relative_to(root):
        raise ValueError()
    if not stat.S_ISREG(target.lstat().st_mode):
        raise ValueError()
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    with os.fdopen(os.open(target, flags), 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError()
        body = stream.read(limit + 1)
    if len(body) > limit:
        raise ValueError()
    return body


@lru_cache(maxsize=1)
def load_bundle(index_path):
    index = Path(index_path)
    root = index.parent
    if not index.is_absolute() or index.name != 'index.html':
        raise ValueError()
    # Reject redirected roots and ancestors (including Windows junctions).
    for path in (root, *root.parents):
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise ValueError()
    root = root.resolve(strict=True)
    manifest = json.loads(_read(root, MANIFEST, 2 * 1024**2))
    if (not isinstance(manifest, dict) or set(manifest) != {'schema_version', 'distribution', 'files'}
            or type(manifest['schema_version']) is not int or manifest['schema_version'] != 1
            or manifest['distribution'] != 'community'):
        raise ValueError()
    files = manifest['files']
    if not isinstance(files, dict) or not 1 <= len(files) <= 4096 or 'index.html' not in files or MANIFEST in files:
        raise ValueError()
    result, total = {}, 0
    for name, entry in files.items():
        if (not isinstance(entry, dict) or set(entry) != {'size', 'sha256'}
                or type(entry['size']) is not int or not 0 <= entry['size'] <= MAX_FILE
                or not isinstance(entry['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', entry['sha256'])):
            raise ValueError()
        total += entry['size']
        if total > MAX_TOTAL:
            raise ValueError()
        body = _read(root, name, entry['size'])
        if len(body) != entry['size'] or hashlib.sha256(body).hexdigest() != entry['sha256']:
            raise ValueError()
        result[name] = (body, '"' + entry['sha256'] + '"')
    return result


@ensure_csrf_cookie
def _html(request, body):
    return HttpResponse(body, content_type=_MIME['.html'])


class PersonalFrontendMiddleware:
    """Before fixed-owner DB resolution, after HTTPS/Host security middleware.

    Only known UI/asset URLs are handled. API/WSS and unknown paths keep the
    existing handlers and authorization; missing scripts never return HTML.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info
        page = path == '/' or bool(_PAGES.fullmatch(path) or _DETAIL.fullmatch(path))
        if page:
            name = 'index.html'
        elif path.startswith('/static/web/'):
            name = path[len('/static/web/'):]
        elif path.startswith(('/brand/', '/examples/')) or path == '/nexus-service-worker.js':
            name = path[1:]
        else:
            return self.get_response(request)
        request.get_host()  # Do not bypass Django's ALLOWED_HOSTS on public assets.
        if request.method not in ('GET', 'HEAD'):
            response = HttpResponse(status=405)
            response['Allow'] = 'GET, HEAD'
        elif not _PATH.fullmatch(name) or name == MANIFEST:
            response = HttpResponse(status=404)
        else:
            try:
                bundle = load_bundle(getattr(settings, 'NEXUS_WEB_INDEX_PATH', ''))
            except (OSError, ValueError, TypeError, RecursionError):
                response = JsonResponse({'code': 'PERSONAL_FRONTEND_UNAVAILABLE',
                    'message': 'Install the verified Community frontend bundle and restart the web service.'}, status=503)
            else:
                item = bundle.get(name)
                if item is None:
                    response = HttpResponse(status=404)
                else:
                    body, etag = item
                    if page:
                        response = _html(request, b'' if request.method == 'HEAD' else body)
                    elif request.headers.get('If-None-Match') == etag:
                        response = HttpResponseNotModified()
                    else:
                        response = HttpResponse(b'' if request.method == 'HEAD' else body,
                            content_type=_MIME.get(Path(name).suffix, 'application/octet-stream'))
                    if response.status_code == 200:
                        response['Content-Length'] = str(len(body))
                    response['ETag'] = etag
                    if path == '/nexus-service-worker.js':
                        response['Service-Worker-Allowed'] = '/'
        response['Cache-Control'] = 'no-store' if page or response.status_code >= 400 or name == 'nexus-service-worker.js' else 'public, max-age=0, must-revalidate'
        response['X-Content-Type-Options'] = 'nosniff'
        response['Referrer-Policy'] = 'same-origin'
        response['X-Frame-Options'] = 'DENY'
        return response
