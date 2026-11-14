"""Explicit operator credential inputs never disappear into a silent fallback."""
from copy import deepcopy
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from tests.test_lab_gateway_api import provision
from tests.test_lab_gateway import binding as legacy_binding
from tests.test_lab_native_command_transports import binding,request
from tinystudio.lab.gateway import transport_for,GatewayError
from tinystudio.lab.gateway_api import create_gateway_app
from tinystudio.lab.gateway_configuration import GatewayRequestPolicy
from tinystudio.lab.native_command_transports import NativeCommandTransport


def credentials(tmp_path):
    file=tmp_path/'device-secrets.json'
    file.write_text(json.dumps({'username':'owned-user','password':'private-runtime-sentinel'}))
    file.chmod(0o600)
    return file


def test_private_runtime_file_reaches_an_explicit_operator_factory_without_entering_journal(tmp_path):
    _,device,headers,bound,token=provision(tmp_path)
    file=credentials(tmp_path);seen=[]
    def factory(binding,*,runtime_secrets):
        seen.append((deepcopy(binding),deepcopy(runtime_secrets)))
        return device
    app=create_gateway_app(bound,tmp_path/'journal',token,factory,runtime_secrets_file=file)
    with TestClient(app) as client:
        assert client.get('/state',headers=headers).status_code==200
        assert len(seen)==1 and seen[0][1]['password']=='private-runtime-sentinel'
        assert 'private-runtime-sentinel' not in json.dumps(app.state.gateway._binding)
        for path in (tmp_path/'journal').iterdir():
            if path.is_file():assert b'private-runtime-sentinel' not in path.read_bytes()


def test_explicit_credential_file_cannot_be_ignored_by_an_incompatible_factory(tmp_path):
    _,device,_,bound,token=provision(tmp_path)
    with pytest.raises(ValueError,match='runtime_secrets'):
        create_gateway_app(bound,tmp_path/'journal',token,lambda binding:device,
            runtime_secrets_file=credentials(tmp_path))


def test_default_legacy_driver_rejects_unused_runtime_credentials_before_io():
    selected=legacy_binding()
    selected['transport']={'kind':'serial','port':'owned-unused-port','baudrate':9600}
    with pytest.raises(GatewayError,match='runtime_secrets'):
        transport_for(selected,runtime_secrets={'password':'private-sentinel'})


@pytest.mark.parametrize('bad',[[],False,'',0])
def test_native_credential_argument_is_an_object_or_none_without_falsey_coercion(bad):
    with pytest.raises(ValueError,match='runtime_secrets'):
        NativeCommandTransport(binding('serial'),runtime_secrets=bad)


def test_operator_adapter_can_report_an_unconfirmed_application_response():
    class Adapter:
        def __init__(self,**kwargs):pass
        def exchange(self,selected,*,timeout,cancelled,authorize):
            assert authorize()==selected
            return {'status':'application_reported','device_application_confirmed':False,
                    'reason':'Owned simulator explicitly rejected the application request'}
    selected=binding('custom',entrypoint='owned_adapter:Adapter',identity='fixture')
    driver=NativeCommandTransport(selected,libraries={'owned_adapter':SimpleNamespace(Adapter=Adapter)})
    command=request(selected,'custom.operation',value='exact authored payload')
    result=driver.exchange(command,timeout=1,cancelled=lambda:False,authorize=lambda:command)
    assert result['status']=='application_reported'
    assert result['application_confirmation_scope']=='adapter_claim'
    assert result['device_application_confirmed'] is False and result['physical_outcome']=='unverified'


def test_host_http_budget_is_advertised_separately_from_device_payload_schema(tmp_path):
    _,device,headers,bound,token=provision(tmp_path)
    app=create_gateway_app(bound,tmp_path/'journal',token,lambda binding:device,
        request_policy=GatewayRequestPolicy(0))
    with TestClient(app) as client:
        profile=client.get('/command-profile',headers=headers).json()
        assert profile['gateway_http']['max_body_bytes']==0
        assert profile['gateway_http']['json_in_memory'] is True
        response=client.post('/acquire',headers=headers,json={'endpoint_id':'device-a','owner':'run-one'})
        assert response.status_code==413 and device.effects==[]
