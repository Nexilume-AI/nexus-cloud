from django.urls import path
from .views import NotificationListView, NotificationSummaryView, NotificationReadView, NotificationReadAllView, NotificationOpenView
from .inbox_views import (
    InboxArchiveView, InboxListView, InboxOpenView, InboxPreferenceView, InboxReadAllView,
    InboxReadView, InboxSnoozeView, InboxStreamView, InboxSummaryView, InboxDetailView,
    PushSubscriptionTestView, PushSubscriptionView,
)

urlpatterns = [
    path("notifications/", NotificationListView.as_view()),
    path("notifications/summary/", NotificationSummaryView.as_view()),
    path("notifications/read-all/", NotificationReadAllView.as_view()),
    path("notifications/<uuid:notification_id>/read/", NotificationReadView.as_view()),
    path("notifications/<uuid:notification_id>/open/", NotificationOpenView.as_view()),
]

from .inbox_urls import urlpatterns as inbox_urlpatterns
urlpatterns = inbox_urlpatterns + urlpatterns
