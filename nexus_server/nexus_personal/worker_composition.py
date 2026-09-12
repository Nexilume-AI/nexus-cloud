"""Explicit operational task inventory, not discovery of the mixed source tree."""

TASK_MODULES = (
    "apps.providers.tasks",
    "apps.datasets.tasks",
    "apps.deployments.tasks",
    "apps.gateway.tasks",
    "apps.notifications.tasks",
    "apps.agents.tasks",
    "apps.metrics.operational_tasks",
    "nexus_personal.tasks",
)

BEAT_SCHEDULE = {
    "personal-monitor-collector": {"task": "apps.metrics.operational_tasks.collect_snapshots", "schedule": 300.0},
    "personal-monitor-evaluator": {"task": "apps.metrics.operational_tasks.evaluate_rules", "schedule": 60.0},
    "personal-monitor-delivery": {"task": "apps.metrics.operational_tasks.deliver_notifications", "schedule": 30.0},
    "personal-monitor-cleanup": {"task": "apps.metrics.operational_tasks.cleanup_monitoring", "schedule": 86400.0},
    "personal-provider-import-cleanup": {"task": "apps.providers.tasks.clear_expired_provider_imports", "schedule": 60.0},
    "personal-provider-catalog": {"task": "apps.providers.tasks.refresh_active_provider_runtime_models", "schedule": 60.0},
    "personal-provider-health": {"task": "apps.providers.tasks.reconcile_provider_runtime_health_task", "schedule": 60.0},
    "personal-source-health": {"task": "apps.deployments.tasks.check_all_active_deployments", "schedule": 300.0},
    "personal-dataset-dispatch": {"task": "apps.datasets.tasks.dispatch_dataset_imports", "schedule": 10.0},
    "personal-dataset-cleanup": {"task": "apps.datasets.tasks.cleanup_dataset_transfers", "schedule": 300.0},
    "personal-agent-file-transfers": {"task": "apps.datasets.tasks.process_agent_file_transfers", "schedule": 5.0,
                                     "options": {"queue": "dataset-imports"}},
    "personal-image-expiry": {"task": "apps.gateway.tasks.expire_image_operations", "schedule": 60.0},
    "personal-inbox-reconcile": {"task": "apps.notifications.tasks.reconcile_work_inbox", "schedule": 60.0},
    "personal-agent-reconcile": {"task": "apps.agents.tasks.reconcile_docker_agent_runtimes", "schedule": 60.0},
    "expire-edge-router-presence": {"task": "apps.agents.tasks.expire_edge_node_presence_task", "schedule": 30.0},
    "personal-invocation-expiry": {"task": "nexus_personal.tasks.expire_invocation_leases", "schedule": 60.0},
}

# Agent invocation execution uses run_agent_tasks and its PostgreSQL lease
# protocol, not Celery. Push delivery and remaining lifecycle maintenance are still
# explicit release gates, never substituted with a successful no-op task.
