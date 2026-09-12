"""Shared Images transport, mounted explicitly by the personal host."""
from django.urls import path
from .image_views import OpenAIImagesView

urlpatterns = [path(f"openai/v1/images/{endpoint}{slash}", OpenAIImagesView.as_view(operation=operation), name=name)
    for endpoint, operation, name in [("generations", "images.generate", "personal-images-generate"),
        ("edits", "images.edit", "personal-images-edit"), ("variations", "images.variation", "personal-images-variation")]
    for slash in ("", "/")]
