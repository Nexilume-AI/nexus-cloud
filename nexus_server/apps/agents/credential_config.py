"""Non-secret configuration for caller-approved model destinations."""
import ipaddress
import re
from urllib.parse import urlsplit, urlunsplit
from rest_framework import exceptions

PROVIDERS = {
    'nexus': {'name':'Nexus Cloud','base_url':'https://cloud.nexilume.com/api/v1/openai/v1','protocol':'openai'},
    'openai': {'name':'OpenAI','base_url':'https://api.openai.com/v1','protocol':'openai'},
    'anthropic': {'name':'Anthropic','base_url':'https://api.anthropic.com','protocol':'anthropic'},
    'custom_openai': {'name':'Custom OpenAI-compatible','base_url':'','protocol':'openai'},
    'custom_anthropic': {'name':'Custom Anthropic-compatible','base_url':'','protocol':'anthropic'},
}


def configuration(provider, base_url=None, model=None, *, require_model=False, resolve=False):
    if not isinstance(provider,str) or provider not in PROVIDERS:
        raise exceptions.ValidationError('Choose a supported provider.')
    model = '' if model is None else model
    if not isinstance(model,str) or (model and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./:-]{0,127}',model)) or (require_model and not model):
        raise exceptions.ValidationError('Enter an exact model identifier.')
    default=PROVIDERS[provider]['base_url']
    url=default if base_url is None else base_url
    if not isinstance(url,str) or len(url)>2048 or any(ord(c)<=32 or ord(c)==127 for c in url):
        raise exceptions.ValidationError('Enter a valid HTTPS Base URL.')
    try:
        parsed=urlsplit(url)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.port==0:
            raise ValueError()
        host=parsed.hostname.encode('idna').decode().lower()
        if host=='localhost' or host.endswith(('.localhost','.local')) or '%' in host:
            raise ValueError()
        try: address=ipaddress.ip_address(host)
        except ValueError: address=None
        if address is not None and not address.is_global:raise ValueError()
        if not re.fullmatch(r'[A-Za-z0-9._~/-]*',parsed.path) or '..' in parsed.path.split('/'):
            raise ValueError()
        authority=('['+host+']') if ':' in host else host
        if parsed.port and parsed.port!=443:authority+=':'+str(parsed.port)
        normalized=urlunsplit(('https',authority,parsed.path.rstrip('/'),'',''))
        if default and normalized!=default:raise ValueError()
    except (ValueError,UnicodeError):
        raise exceptions.ValidationError('Use a public HTTPS Base URL without credentials, query parameters or fragments; choose a custom provider to change its address.') from None
    if resolve and not default:
        from apps.gateway.provider_http import resolve_provider_endpoint, ProviderEndpointRejected
        try:resolve_provider_endpoint(normalized,public_https_only=True)
        except (ProviderEndpointRejected,OSError):
            raise exceptions.ValidationError('The endpoint must resolve exclusively to public HTTPS addresses.') from None
    return provider,normalized,model
