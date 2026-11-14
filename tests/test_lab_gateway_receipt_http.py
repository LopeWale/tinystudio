"""Receipt redelivery is a read, including after command/lease expiry."""
from fastapi.testclient import TestClient

from tests.test_lab_gateway_api import provision,command
from tests.test_lab_native_gateway import Clock
from tinystudio.lab import gateway_api


def test_completed_receipt_redelivery_after_expiry_does_not_repeat_or_renew(tmp_path,monkeypatch):
    factory,device,headers,*_=provision(tmp_path)
    clock=Clock()
    original=gateway_api.CommandGateway
    monkeypatch.setattr(gateway_api,'CommandGateway',lambda *args,**kwargs:original(*args,**kwargs,clock=clock))
    with TestClient(factory()) as client:
        lease=client.post('/acquire',headers=headers,json={'endpoint_id':'device-a','owner':'run-a','ttl':20}).json()
        body=command(lease['epoch']);body['expires_at']=1010.
        first=client.post('/commands',headers=headers,json=body)
        assert first.status_code==200,first.text
        clock.value=1040.
        replay=client.post('/commands',headers=headers,json=body)
        assert replay.status_code==200 and replay.json()==first.json()
        assert len(device.effects)==1
        assert client.get('/state',headers=headers).json()['state']=='recovery_required'
        changed={**body,'command_id':'new-command'}
        assert client.post('/commands',headers=headers,json=changed).status_code in {409,410}
        assert len(device.effects)==1
