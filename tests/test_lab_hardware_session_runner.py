"""Actual frozen child and native loopback, with a controlled Lab transport."""
import hashlib
import json

import httpx
import pytest

from tinystudio.lab.contracts import ExperimentDefinition,checksum
from tinystudio.lab.runner import Runner
from tinystudio.lab.service import implementation_manifest,runtime_versions
from tests.test_lab_runner import Transport


def test_runner_owns_gateway_credentials_and_retains_parent_and_child_evidence(tmp_path):
    pytest.importorskip('serial')
    state=tmp_path/'private-state';state.mkdir(mode=0o700)
    content=(json.dumps({'decision_id':'loop-query','payload':{'operation':'serial.query',
        'data':{'encoding':'hex','value':'00ff0a'}},'valid_for_seconds':5})+'\n').encode()
    digest=hashlib.sha256(content).hexdigest()
    definition=ExperimentDefinition(name='Owned complete session',engine='hardware.session',operating_mode='physical_control',
        inputs=[{'artifact_id':'supervisor','sha256':digest}],parameters={'binding_id':'loop',
            'supervisor':{'kind':'recorded','artifact_id':'supervisor'}},
        execution={'backend':'native','profile':'trusted_native','max_seconds':30,'max_output_bytes':4*1024**2}).model_dump()
    spec={'schema_version':1,'definition':definition,'implementation':implementation_manifest(),'runtime':runtime_versions(),
          'input_manifest':[{'id':'supervisor','name':'decisions.jsonl','sha256':digest,'size':len(content)}]}
    attempt={'run_id':'run-one','attempt_id':'attempt-one','lease_token':'never-child-lab-token','spec':spec,'spec_digest':checksum(spec)}
    gateway={'endpoint_id':'loop-one','operator_id':'operator',
        'transport':{'kind':'serial','port':'loop://','baudrate':9600,'write_timeout':1,'read':{'kind':'fixed_bytes','count':3}},
        'command_profile':{'format':'tinystudio.native-command-profile.v1'},'send_timeout':1}
    entry={'state_directory':str(state),'gateway_binding':gateway}
    profile={'schema_version':1,'engine_grants':{'hardware.session':{'native_execution':True,'bindings':{'loop':entry}}},
        'engine_secrets':{'hardware.session':{'bindings':{'unselected':{'password':'never-child-device-secret'}}}}}
    class SessionTransport(Transport):
        def post(self,path,**kwargs):
            if path.endswith('/heartbeat'):
                self.beats.append((self.downloading.is_set(),self.uploading.is_set()))
                return httpx.Response(200,json={'cancel_requested':False,'lease_seconds':60},request=httpx.Request('POST','http://127.0.0.1'+path))
            return super().post(path,**kwargs)
    transport=SessionTransport(blocks=[content])
    root=tmp_path/'attempt';root.mkdir()
    runner=Runner('http://127.0.0.1','target-one','never-child-target-token',client=transport,
                  profile_config=profile,heartbeat_interval=.05,process_grace=.5)
    runner.execute_attempt(attempt,root)
    assert transport.finish['state']=='succeeded',transport.finish
    assert transport.finish['result']['records']==1
    roles=[item['role'] for item in transport.upload_params]
    assert 'hardware_session_parent' in roles and 'hardware_session_receipts' in roles
    context=(root/'context.json').read_text()
    assert 'never-child-device-secret' not in context and 'never-child-lab-token' not in context
    assert json.loads(context)['_runtime_secrets']=={}
    assert all(b'never-child-device-secret' not in blob and b'never-child-lab-token' not in blob for blob in transport.uploads)
    parent=[blob for blob,params in zip(transport.uploads,transport.upload_params) if params['role']=='hardware_session_parent']
    assert any(b'AP8K' in blob for blob in parent)
    assert transport.finish['result']['parent_session']['physical_outcome']=='unverified'
