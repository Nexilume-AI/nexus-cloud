"""Device-scoped outbound model transport, with staged binding and cutover."""
import hashlib
import json
from urllib.parse import urlsplit
from django.conf import settings
from django.db import transaction
from django.utils.crypto import constant_time_compare
from rest_framework import exceptions, serializers
from rest_framework.response import Response
from apps.common.crypto import decrypt_secret
from apps.providers.connection_services import connection_runtime, get_provider_connection, update_provider_connection, create_provider_connection
from apps.providers.connection_inputs import ProviderConnectionCreateSerializer
from apps.deployments.models import Deployment
from apps.gateway.provider_http import open_provider_url
from .models import RelayModelBinding
from .relay_devices import DeviceNative, device_auth, scope_request, audit, active, validate


def endpoint(binding):
    return str(settings.NEXUS_PUBLIC_BASE_URL).rstrip('/') + '/api/model-relay/bindings/' + str(binding.pk) + '/v1'


def describe(binding):
    return {'binding_id': str(binding.pk), 'provider_id': str(binding.provider_id),
            'upstream_endpoint': binding.upstream_url, 'endpoint': endpoint(binding),
            'models': binding.models, 'protocol': binding.protocol, 'status': binding.status}


def prepare(request, device, spec, key):
    from apps.providers.models import ProviderAccount
    group = [s for s in device.manifest if s.get('transport') == 'outbound' and
             (s.get('provider_id') == spec['provider_id'] if spec.get('provider_id') else s['model'] == spec['model'])]
    if any(s['endpoint'] != spec['endpoint'] or s['protocol'] != spec['protocol'] for s in group):
        raise exceptions.ValidationError('A Provider binding must use one approved upstream and protocol.')
    if spec.get('provider_id'):
        account = get_provider_connection(request=request, account_id=spec['provider_id'])
    else:
        tag = 'relay-sdk-' + hashlib.sha256(f"{device.id}:{spec['model']}".encode()).hexdigest()[:24]
        account = ProviderAccount.objects.filter(tenant_id=device.tenant_id, account_id=tag).first()
        if account is None:
            if not key:raise exceptions.ValidationError('Import the upstream credential first.')
            data = ProviderConnectionCreateSerializer(data={'name': spec['name'], 'account_id': tag,
                'engine': 'direct_api', 'upstream_provider': spec['protocol'], 'url': spec['endpoint'], 'key': key})
            data.is_valid(raise_exception=True)
            account = create_provider_connection(request=request, data=dict(data.validated_data))
    if str(account.tenant_id) != device.tenant_id or account.status != 'active':
        raise exceptions.PermissionDenied('Provider is unavailable in this device Organization.')
    if key and not constant_time_compare(decrypt_secret(account.encrypted_key), key):
        raise exceptions.ValidationError('Existing Provider credential cannot be replaced by a device.')
    runtime = connection_runtime(account)
    if not runtime or runtime.runtime_type != 'direct_api' or runtime.status not in {'active','created'}:
        raise exceptions.ValidationError('Provider runtime must be an enabled Direct API connection.')
    # Account row locks serialize two devices attempting to claim the same Provider.
    ProviderAccount.objects.select_for_update().get(pk=account.pk)
    binding = RelayModelBinding.objects.filter(provider=account).first()
    models = sorted(s['model'] for s in group)
    if binding:
        if binding.device_id != device.pk or binding.models != models or binding.upstream_url != spec['endpoint'] or binding.status == 'revoked':
            raise exceptions.ValidationError('Provider already has another binding; detach it explicitly first.')
    else:
        sources = Deployment.objects.filter(provider_runtime=runtime).exclude(status='deleted')
        if any(d.upstream_model_id not in models for d in sources):
            raise exceptions.ValidationError('Approve every existing Source model before changing its Provider transport.')
        binding = RelayModelBinding.objects.create(device=device, provider=account, upstream_url=spec['endpoint'],
            models=models, protocol=spec['protocol'], original={'account_url': account.url, 'runtime_url': runtime.internal_api_url,
                'runtime_id': str(runtime.pk), 'sources': {str(d.pk): d.endpoint for d in sources}})
    receipt = {'model': spec['model'], 'transport': 'outbound', **describe(binding)}
    device.registrations[spec['model']] = receipt
    device.save(update_fields=['registrations','updated_at'])
    return receipt


def allowed_binding(device, ident):
    binding = RelayModelBinding.objects.select_related('provider').filter(pk=ident, device=device, status__in=['prepared','active']).first()
    if not binding or str(binding.provider.tenant_id) != device.tenant_id or binding.provider.status != 'active':
        raise exceptions.PermissionDenied('Relay binding is unavailable.')
    runtime = connection_runtime(binding.provider)
    if not runtime or runtime.status not in {'active','created'}:
        raise exceptions.PermissionDenied('Provider is disabled.')
    expected = endpoint(binding) if binding.status == 'active' else binding.original['account_url']
    if binding.provider.url.rstrip('/') != expected.rstrip('/'):
        raise exceptions.PermissionDenied('Provider transport changed. Review the binding.')
    return binding


class RelayTransportSession(DeviceNative):
    def get(self, request):
        # Read-only introspection must not wait on registration's device lock;
        # otherwise a catalog request during cutover deadlocks the broker.
        device = device_auth(request, lock=False)
        bindings = []
        for binding in device.model_bindings.filter(status__in=['prepared','active']):
            try:bindings.append(describe(allowed_binding(device, binding.pk)))
            except exceptions.PermissionDenied:continue
        return Response({'device_id': str(device.pk), 'bindings': bindings,
            'websocket_url': str(settings.NEXUS_PUBLIC_BASE_URL).rstrip('/').replace('https://','wss://',1) + '/api/model-relay/worker'})


class Invocation(serializers.Serializer):
    binding_id = serializers.UUIDField()
    endpoint_api_key = serializers.CharField(max_length=8192, write_only=True, trim_whitespace=False)


class RelayTransportAuthorize(DeviceNative):
    def post(self, request):
        data = validate(Invocation, request.data)
        device = device_auth(request, lock=False)
        binding = allowed_binding(device, data['binding_id'])
        if not constant_time_compare(decrypt_secret(binding.provider.encrypted_key), data['endpoint_api_key']):
            raise exceptions.AuthenticationFailed('Invalid Provider credential.')
        return Response(describe(binding))


class BindingInput(serializers.Serializer):
    binding_id = serializers.UUIDField()


class RelayTransportActivate(DeviceNative):
    @transaction.atomic
    def post(self, request):
        data = validate(BindingInput, request.data)
        device = device_auth(request)
        binding = allowed_binding(device, data['binding_id'])
        delegated = scope_request(request, device)
        account = binding.provider
        runtime = connection_runtime(account)
        if binding.status != 'active':
            # Verify the prepared tunnel before changing any existing URL.
            try:
                with open_provider_url(endpoint(binding)+'/models', method='GET',
                        headers={'Authorization':'Bearer '+decrypt_secret(account.encrypted_key)}, timeout=20) as response:
                    raw=response.read(1024*1024+1)
                if len(raw)>1024*1024:raise ValueError()
                ids={m['id'] for m in json.loads(raw)['data']}
                if not set(binding.models).issubset(ids):raise ValueError()
            except Exception:
                raise exceptions.ValidationError('Relay catalog verification failed. Start nexus-relay serve before activating.') from None
            active(device)
            # Verification performs network I/O. Re-read under locks afterwards
            # so a concurrent operator edit cannot be overwritten by a stale copy.
            from apps.providers.models import ProviderAccount, ProviderRuntimeAccount
            account = ProviderAccount.objects.select_for_update().get(pk=account.pk)
            runtime = ProviderRuntimeAccount.objects.select_for_update().get(pk=runtime.pk)
            if account.status != 'active' or runtime.status not in {'active', 'created'}:
                raise exceptions.PermissionDenied('Provider was disabled during verification.')
            sources=list(Deployment.objects.select_for_update().filter(provider_runtime=runtime).exclude(status='deleted'))
            if any(d.upstream_model_id not in binding.models for d in sources):
                raise exceptions.ValidationError('Source model scope changed; obtain a new approval.')
            if account.url != binding.original['account_url'] or runtime.internal_api_url != binding.original['runtime_url'] or any(binding.original['sources'].get(str(d.pk)) != d.endpoint for d in sources):
                raise exceptions.ValidationError('Provider or Source changed since preparation; no URLs were overwritten.')
            update_provider_connection(request=delegated,account_id=str(account.pk),data={'url':endpoint(binding)})
            runtime.internal_api_url=endpoint(binding);runtime.save(update_fields=['internal_api_url','updated_at'])
            Deployment.objects.filter(pk__in=[d.pk for d in sources]).update(endpoint=endpoint(binding))
            binding.status='active';binding.save(update_fields=['status','updated_at'])
        # New endpoints reuse existing model/Pool/Router provisioning. Existing
        # Provider adoption never creates or modifies Router bindings.
        for spec in device.manifest:
            if spec['model'] not in binding.models:continue
            if not spec.get('provider_id'):
                from .relay_registration import register
                receipt=register(delegated,device,{**spec,'endpoint':endpoint(binding)},decrypt_secret(account.encrypted_key))
            else:receipt=device.registrations.get(spec['model'], {'model': spec['model']})
            device.registrations[spec['model']]={**receipt,'transport':'outbound',**describe(binding)}
        device.save(update_fields=['registrations','updated_at']);audit(request,device,'transport.activate')
        return Response(describe(binding))
