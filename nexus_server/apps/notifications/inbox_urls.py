"""Shared current Inbox API; legacy enterprise notification URLs stay separate."""
from django.urls import path
from .inbox_views import (
    InboxArchiveView, InboxListView, InboxOpenView, InboxPreferenceView, InboxReadAllView,
    InboxReadView, InboxSnoozeView, InboxStreamView, InboxSummaryView, InboxDetailView,
    PushSubscriptionTestView, PushSubscriptionView,
)

urlpatterns = [
    path("inbox/summary/", InboxSummaryView.as_view()),
    path("inbox/items/", InboxListView.as_view()),
    path("inbox/items/<uuid:item_id>/", InboxDetailView.as_view()),
    path("inbox/read-all/", InboxReadAllView.as_view()),
    path("inbox/items/<uuid:item_id>/read/", InboxReadView.as_view()),
    path("inbox/items/<uuid:item_id>/snooze/", InboxSnoozeView.as_view()),
    path("inbox/items/<uuid:item_id>/archive/", InboxArchiveView.as_view()),
    path("inbox/items/<uuid:item_id>/open/", InboxOpenView.as_view()),
    path("inbox/preferences/", InboxPreferenceView.as_view()),
    path("inbox/push-subscriptions/", PushSubscriptionView.as_view()),
    path("inbox/push-subscriptions/test/", PushSubscriptionTestView.as_view()),
    path("inbox/stream/", InboxStreamView.as_view()),
]
