"""Project diagnostics remain readable when independent capacity is disabled."""
from tests.test_lab_foundation import database, setup
from tests.test_lab_transfer_api import fixture, request
from tinystudio.lab.operation_runtime import OperationRuntimeConfiguration, register_operation_runtime


def test_project_pending_transfer_and_operation_diagnostics_do_not_require_capacity(fixture):
    factory,project,other,client,base,app,store=fixture
    response=client.post(base+'/transfers',json=request())
    assert response.status_code==201,response.text
    identifier=response.json()['id']
    with factory() as db:
        from tinystudio.lab.models import LabTransfer
        row=db.get(LabTransfer,identifier);upload=row.upload_id
    store.uploads[upload]['parts'][1]=b'\x00\xff\n'
    response=client.post(base+'/transfers/'+identifier+'/complete')
    assert response.status_code==202,response.text
    operation=response.json()['operation_id']
    register_operation_runtime(app,OperationRuntimeConfiguration(mode='disabled'))
    assert client.get(base+'/transfers/'+identifier).status_code==200
    response=client.get(base+'/operations/'+operation)
    assert response.status_code==200 and response.json()['state']=='queued'
