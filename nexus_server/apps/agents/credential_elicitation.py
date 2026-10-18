"""Non-secret choices, bound to the authenticated MCP session and a pending grant."""
from .credential_config import PROVIDERS


def options(row):
    from .credential_router import available
    from .credential_services import saved_grants
    choices = {}
    if row.provider == 'nexus' and available(row):
        for router in available(row):
            for model in router['models']:
                label = f"{router['name']} / {model} [{router['router_id']}]"
                choices[label] = {'provider':'nexus','router_id':router['router_id'],
                                 'model':model,'base_url':PROVIDERS['nexus']['base_url']}
                if len(choices)>=100:break
            if len(choices)>=100:break
    else:
        for source in saved_grants(row).filter(provider=row.provider, base_url=row.base_url):
            # In-band confirmation only reuses already-approved model scope.
            if not source.model_name or (row.model_name and row.model_name!=source.model_name):continue
            choices[f"Saved {source.model_name} [{source.id}]"] = {
                'provider':source.provider,'base_url':source.base_url,'model':source.model_name,
                'saved_authorization_id':str(source.id)}
            if len(choices)>=100:break
    return choices


def form(row, choices):
    return {'mode':'form', 'message':
        f"Authorize {row.run.agent.name} to use the selected model at {row.base_url}. "
        f"Organization: {row.run.consumer_tenant.name}. Model usage may incur charges. "
        "Choose a Router/model or an existing authorization, select a lifetime, and explicitly confirm. "
        "No API key is requested. To enter a new key or change provider, choose Configure securely.",
        'requestedSchema':{'type':'object','properties':{
            'selection':{'type':'string','title':'Router / model or saved authorization',
                         'enum':list(choices)+['Configure securely']},
            'lifetime':{'type':'string','title':'Lifetime (seconds)', 'enum':['300','3600','86400'],'default':'3600'},
            'consent':{'type':'boolean','title':'I authorize the selected access and any model usage charges','default':False},
        },'required':['selection','lifetime','consent']}}
