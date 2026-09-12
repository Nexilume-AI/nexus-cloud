"""Personal factory selection must be explicit and fail closed."""
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings
from django.core.handlers.asgi import ASGIHandler
from apps.workspaces.asgi import WorkspaceASGIProxy
from nexus_personal.application import create_application


class PersonalApplicationTests(SimpleTestCase):
    @override_settings(ROOT_URLCONF="nexus_personal.urls")
    def test_factory_composes_real_http_and_workspace_asgi_handlers(self):
        application = create_application()
        self.assertIsInstance(application, WorkspaceASGIProxy)
        self.assertIsInstance(application.django_app, ASGIHandler)

    def test_test_urls_cannot_accidentally_become_the_product_host(self):
        with self.assertRaises(ImproperlyConfigured):
            create_application()

    @override_settings(ROOT_URLCONF="nexus_personal.urls")
    def test_wrong_or_missing_distribution_and_authority_fail_closed(self):
        for values in ({"NEXUS_DISTRIBUTION": "enterprise"}, {"NEXUS_DISTRIBUTION": ""},
                       {"NEXUS_IDENTITY_BACKEND": ""}, {"NEXUS_AUTHORIZATION_BACKEND": ""}):
            with override_settings(**values), self.assertRaises(ImproperlyConfigured):
                create_application()
