"""Real authenticated Lab/native discovery against an owned Modbus emulator."""
import hashlib
import json

import pytest

from tests.test_lab_native_workflow import native_http, posted
from tests.test_lab_analysis_http import wait_advertisement
from tests.test_lab_device_discovery import mapping, owned_register_server
from tinystudio.lab.runner import Runner


def test_native_discovery_records_dynamic_models_and_exact_wire_provenance(native_http):
    pytest.importorskip('pymodbus.client')
    f = native_http
    words = mapping(1750, [(64009, list(range(130))), (777, [31, 32])])
    with owned_register_server(words) as (port, requests):
        binding = {'read_only': True, 'methods': ['sunspec.scan'], 'device_id': 255,
            'base_addresses': [1750], 'endpoint': {'host': '127.0.0.1', 'port': port,
                'framing': 'tcp', 'allow_dns': False}, 'limits': {'max_timeout': 1, 'max_frame_bytes': 256}}
        operator = {'schema_version': 1, 'backends': {}, 'engine_grants': {
            'hardware.discover': {'bindings': {'owned-device': binding}}}}
        target = posted(f.http, f.prefix + '/targets', json={'name': 'owned-modbus-discovery'})
        runner = Runner(f.url, target['id'], target['token'], profile_config=operator, heartbeat_interval=.1)
        try:
            runner.advertise()
            wait_advertisement(f, target)
            methods = f.http.get(f.prefix + '/hardware/discovery-methods')
            assert methods.status_code == 200
            assert {item['id'] for item in methods.json()['methods']} == {'serial.enumerate', 'can.enumerate', 'sunspec.scan'}
            assert requests == [], 'catalog reads must not probe the instrument'
            definition = {'name': 'Read the actual model chain', 'engine': 'hardware.discover', 'domain': 'hardware',
                'mode': 'online', 'operating_mode': 'acquisition',
                'parameters': {'method': 'sunspec.scan', 'binding_id': 'owned-device', 'timeout': .5,
                    'max_frame_bytes': 16, 'scan': {'base_addresses': [1750], 'device_id': 255, 'read_model_data': True}},
                'execution': {'profile': 'trusted_native', 'backend': 'native', 'max_seconds': 30}}
            saved = posted(f.http, f.prefix + '/definitions', json={'definition': definition})
            report = posted(f.http, f.prefix + '/preflight', json={'definition': saved['definition'], 'target_id': target['id']})
            assert report['compatible'], json.dumps(report, indent=2)
            assert report['qualification'] == 'unqualified'
            run = posted(f.http, f.prefix + '/runs', json={'definition_id': saved['id'], 'revision': 1,
                'target_id': target['id'], 'idempotency_key': 'discover-owned-models'})
            assert runner.run_once(run_id=run['id'])
        finally:
            runner.client.close()
        completed = f.http.get(f.prefix + '/runs/' + run['id']).json()
        assert completed['state'] == 'succeeded', completed.get('error')
        artifacts = {item['role']: item for item in completed['result']['artifacts']}
        response = f.http.get(f.prefix + '/artifacts/' + artifacts['device_discovery']['id'])
        assert hashlib.sha256(response.content).hexdigest() == artifacts['device_discovery']['sha256']
        observed = response.json()
        assert observed['status'] == 'complete' and observed['qualification']['qualified'] is False
        assert observed['qualification']['physical_validation'] is False
        models = observed['discovery']['trials'][0]['models']
        assert [(item['model_id'], item['payload_address'], item['length']) for item in models] == [
            (64009, 1754, 130), (777, 1886, 2)]
        assert all(unit == 255 and function in (3, 4) for unit, function, *_ in requests)
        assert (255, 3, 1754, 125) in requests and (255, 3, 1879, 5) in requests
        wire = f.http.get(f.prefix + '/artifacts/' + artifacts['discovery_wire']['id'])
        assert hashlib.sha256(wire.content).hexdigest() == artifacts['discovery_wire']['sha256']
        packets = [json.loads(line) for line in wire.content.splitlines()]
        assert packets and any(packet['truncated'] for packet in packets)
        assert all(packet['captured_bytes'] <= 16 for packet in packets)
        assert f.http.get(f.restored + '/runs/' + run['id']).status_code == 404
