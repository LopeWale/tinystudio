"""Discovery is a normal operator-bound Lab engine, not a host-authority grant."""
from copy import deepcopy

from tinystudio.lab.contracts import ExperimentDefinition
from tinystudio.lab import service, preflight, execution
from tinystudio.lab.runner import capabilities


def definition():
    return ExperimentDefinition(name='Inspect selected target', engine='hardware.discover',
        domain='hardware', operating_mode='acquisition', parameters={
            'method': 'serial.enumerate', 'binding_id': 'owned-inventory'},
        execution={'profile': 'trusted_native', 'backend': 'native'}).model_dump()


def profile():
    return {'schema_version': 1, 'backends': {}, 'engine_grants': {'hardware.discover': {'discovery': {
        'binding_id': 'owned-inventory', 'read_only': True, 'methods': ['serial.enumerate'],
        'credential_sentinel': 'must-never-be-advertised'}}}}


def test_catalog_and_plugins_expose_selection_separately_from_physical_qualification():
    catalog = service.catalog()
    engine = next(row for row in catalog['methods'] if row['id'] == 'hardware.discover')
    assert engine['implemented'] and engine['available']
    assert engine['qualification'] == 'unqualified'
    assert engine['manifest']['kinds'] == ['source', 'evaluator']
    assert {method['id'] for method in catalog['hardware_discovery_methods']['methods']} == {
        'serial.enumerate', 'can.enumerate', 'sunspec.scan'}
    assert next(row for row in catalog['adapters'] if row['id'] == 'acquisition.can')['implemented']


def test_preflight_uses_only_public_host_binding_declaration():
    doc = ExperimentDefinition.model_validate(definition())
    advertised = capabilities(profile())
    assert 'must-never-be-advertised' not in repr(advertised)
    permitted = preflight.execution_checks(doc, advertised)
    assert next(check for check in permitted if check['code'] == 'hardware_binding')['passed']
    assert all(check['passed'] for check in permitted), permitted
    absent = capabilities()
    denied = preflight.execution_checks(doc, absent)
    assert not next(check for check in denied if check['code'] == 'hardware_binding')['passed']


def test_discovery_dispatch_replaces_authored_runtime_authority(tmp_path, monkeypatch):
    from tinystudio.lab import hardware_discovery_engine
    observed = []
    trusted = profile()['engine_grants']['hardware.discover']
    forged = {'discovery': {'binding_id': 'forged'}}
    document = {'definition': definition(), '_runtime_grants': forged}
    before = deepcopy(document)
    def capture(spec, output_dir, emit, cancelled):
        observed.append(spec)
        return {'records': 0, 'artifacts': []}
    monkeypatch.setattr(hardware_discovery_engine, 'execute_hardware_discovery', capture)
    execution.execute(document, tmp_path, lambda _: None, lambda: False,
                      context={'_runtime_grants': trusted})
    assert observed[0]['_runtime_grants'] == trusted
    assert document == before
