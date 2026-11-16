from django.db import migrations


def exports(apps, schema_editor):
    Router = apps.get_model("routers", "Router")
    Output = apps.get_model("routers", "RouterOutput")
    Binding = apps.get_model("routers", "RouterChildBinding")
    for router in Router.objects.filter(router_type="aggregation").exclude(status="deleted").iterator():
        names = list(dict.fromkeys(Binding.objects.filter(router=router, status="active", enabled=True)
                                  .order_by("priority", "created_at").values_list("exposed_model_name", flat=True)))
        if router.strategy in ("task_type", "system_one"):
            names.append("auto")
        Output.objects.filter(router=router).exclude(model_name__in=names).update(enabled=False, is_default=False)
        for index, name in enumerate(names):
            Output.objects.update_or_create(router=router, model_name=name, defaults={
                "model_group": None, "enabled": True, "status": "active", "deleted_at": None,
                "is_default": index == 0,
            })


class Migration(migrations.Migration):
    dependencies = [("routers", "0003_router_system_one")]
    operations = [migrations.RunPython(exports, migrations.RunPython.noop)]
