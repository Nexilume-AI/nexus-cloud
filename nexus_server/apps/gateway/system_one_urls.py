from django.urls import path
from .system_one_views import SystemOneView

urlpatterns = [path("openai/v1/systemone" + suffix, SystemOneView.as_view(), name="gateway-systemone" + ("-slash" if suffix else ""))
               for suffix in ("", "/")]
