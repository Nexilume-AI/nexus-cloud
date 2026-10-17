from django.urls import path
from .training_views import TrainingSamplesView, TrainingSampleView, TrainingRunImportView, TrainingExportsView

urlpatterns = [
    path("datasets/<uuid:dataset_id>/training/samples/", TrainingSamplesView.as_view()),
    path("datasets/<uuid:dataset_id>/training/samples/<uuid:sample_id>/", TrainingSampleView.as_view()),
    path("datasets/<uuid:dataset_id>/training/import-runs/", TrainingRunImportView.as_view()),
    path("datasets/<uuid:dataset_id>/training/exports/", TrainingExportsView.as_view()),
]
