import hashlib
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.core.exceptions import DisallowedHost
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from nexus_personal.frontend import PersonalFrontendMiddleware, load_bundle


@override_settings(ALLOWED_HOSTS=['testserver'])
class PersonalFrontendTests(SimpleTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nexus-community-assets-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.files = {'index.html': b'<html>Nexus Community</html>', 'assets/app-a.js': b'console.log("personal");',
            'brand/icon.svg': b'<svg/>', 'examples/agent.py': b'print("example")',
            'pdfjs/cmaps/UniGB-UTF16-H.bcmap': b'font-fixture', 'nexus-service-worker.js': b'/* worker */'}
        for name, body in self.files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        self.manifest = {'schema_version': 1, 'distribution': 'community', 'files': {
            name: {'size': len(body), 'sha256': hashlib.sha256(body).hexdigest()} for name, body in self.files.items()}}
        self.write_manifest()
        self.factory = RequestFactory()
        self.settings_override = override_settings(NEXUS_WEB_INDEX_PATH=str(self.root / 'index.html'))
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        load_bundle.cache_clear()
        self.addCleanup(load_bundle.cache_clear)
        self.forwarded = []
        def downstream(request):
            self.forwarded.append(request.path_info)
            return HttpResponse(status=418)
        self.app = PersonalFrontendMiddleware(downstream)

    def write_manifest(self):
        (self.root / 'community-assets.json').write_text(json.dumps(self.manifest), encoding='utf-8')

    def get(self, path, **headers):
        return self.app(self.factory.get(path, **headers))

    def test_owner_login_and_refreshed_deep_links_have_shell_and_csrf_without_database(self):
        for path in ['/', '/login', '/providers?view=all', '/agents/agent-one/private-display', '/agent-runs/run-one/display']:
            with self.subTest(path=path):
                response = self.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, self.files['index.html'])
                self.assertIn('csrftoken', response.cookies)
                self.assertEqual(response['Cache-Control'], 'no-store')
                self.assertEqual(response['X-Frame-Options'], 'DENY')
        self.assertEqual(self.forwarded, [])

    def test_assets_mime_head_etag_pdf_and_service_worker(self):
        response = self.get('/static/web/assets/app-a.js')
        self.assertEqual(response['Content-Type'], 'text/javascript; charset=utf-8')
        self.assertNotIn('csrftoken', response.cookies)
        cached = self.get('/static/web/assets/app-a.js', HTTP_IF_NONE_MATCH=response['ETag'])
        self.assertEqual(cached.status_code, 304)
        self.assertNotIn('Content-Type', cached)
        self.assertEqual(cached.content, b'')
        head = self.app(self.factory.head('/static/web/assets/app-a.js'))
        self.assertEqual(head.content, b'')
        self.assertEqual(int(head['Content-Length']), len(response.content))
        for name in ['brand/icon.svg', 'examples/agent.py', 'pdfjs/cmaps/UniGB-UTF16-H.bcmap']:
            asset = self.get('/static/web/' + name)
            self.assertEqual(asset.content, self.files[name])
            self.assertEqual(asset['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(self.get('/brand/icon.svg').content, b'<svg/>')
        worker = self.get('/nexus-service-worker.js')
        self.assertEqual(worker['Service-Worker-Allowed'], '/')
        self.assertEqual(worker['Cache-Control'], 'no-store')

    def test_api_websocket_and_private_routes_never_become_html(self):
        for path in ['/api/v1/personal/context/', '/api/v1/missing/', '/ws/computer-runtime/v1/connect/',
                     '/access', '/billing', '/marketplace/agents', '/unknown', '/.env']:
            self.assertEqual(self.get(path).status_code, 418)
        self.assertEqual(len(self.forwarded), 8)

    def test_missing_assets_traversal_and_unlisted_files_are_not_served(self):
        (self.root / 'host.json').write_text('never-expose-secret', encoding='utf-8')
        for path in ['/static/web/assets/missing.js', '/static/web/host.json', '/static/web/community-assets.json',
                     '/static/web/../host.json', '/static/web/%252e%252e/host.json', '/static/web/C:/host.json',
                     '/static/web/assets\\host.json', '/static/web/.env', '/static/web/']:
            response = self.get(path)
            self.assertEqual(response.status_code, 404, path)
            self.assertNotIn(b'never-expose-secret', response.content)
        self.assertEqual(self.app(self.factory.post('/providers')).status_code, 405)
        with self.assertRaises(DisallowedHost):
            self.get('/static/web/assets/app-a.js', HTTP_HOST='foreign.invalid')

    def test_missing_foreign_or_corrupt_bundle_fails_closed_without_paths(self):
        for mutate in ['missing', 'foreign', 'digest', 'boolean', 'oversize', 'traversal']:
            original = json.loads(json.dumps(self.manifest))
            load_bundle.cache_clear()
            if mutate == 'missing':
                (self.root / 'community-assets.json').unlink()
            else:
                if mutate == 'foreign': self.manifest['distribution'] = 'enterprise'
                if mutate == 'digest': self.manifest['files']['index.html']['sha256'] = '0' * 64
                if mutate == 'boolean': self.manifest['schema_version'] = True
                if mutate == 'oversize': self.manifest['files']['index.html']['size'] = 32 * 1024**2 + 1
                if mutate == 'traversal': self.manifest['files']['../secret'] = self.manifest['files']['index.html']
                self.write_manifest()
            response = self.get('/login')
            self.assertEqual(response.status_code, 503, mutate)
            self.assertEqual(json.loads(response.content)['code'], 'PERSONAL_FRONTEND_UNAVAILABLE')
            self.assertNotIn(str(self.root).encode(), response.content)
            self.manifest = original
            self.write_manifest()

    def test_snapshot_is_verified_once_and_a_restart_revalidates_changed_files(self):
        first = self.get('/static/web/assets/app-a.js')
        with patch('nexus_personal.frontend._read', side_effect=AssertionError('Repeated disk read')):
            self.assertEqual(self.get('/static/web/assets/app-a.js').content, first.content)
        (self.root / 'assets/app-a.js').write_bytes(b'changed')
        self.assertEqual(self.get('/static/web/assets/app-a.js').content, first.content)
        load_bundle.cache_clear()  # Equivalent to starting a fresh web process.
        self.assertEqual(self.get('/login').status_code, 503)

    def test_bounded_manifest_and_snapshot_reject_excessive_inputs(self):
        with patch('nexus_personal.frontend.MAX_TOTAL', 1):
            self.assertEqual(self.get('/login').status_code, 503)
        entry = self.manifest['files']['index.html']
        self.manifest['files'].update({f'assets/extra-{i}.js': entry for i in range(4096)})
        self.write_manifest()
        self.assertEqual(self.get('/login').status_code, 503)
        (self.root / 'community-assets.json').write_bytes(b'x' * (2 * 1024**2 + 1))
        self.assertEqual(self.get('/login').status_code, 503)

    def test_linked_bundle_and_asset_are_rejected(self):
        with patch.object(Path, 'is_symlink', return_value=True):
            self.assertEqual(self.get('/login').status_code, 503)
        # Actual POSIX symlink test; Windows requires separately granted privilege.
        if __import__('os').name != 'nt':
            target = self.root / 'assets/app-a.js'
            target.unlink()
            target.symlink_to(self.root / 'index.html')
            self.assertEqual(self.get('/login').status_code, 503)
