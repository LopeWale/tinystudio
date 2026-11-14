"""Pure admission checks and private SQLite submission; no device access."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import socket

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from tinystudio.db.models import Base, Project, User
from tinystudio.lab import service
from tinystudio.lab.contracts import ExperimentDefinition, checksum
from tinystudio.lab.hardware_session_configuration import HardwareSessionBinding
from tinystudio.lab.hardware_session_engine import validate_selection
from tinystudio.lab.models import (
    LabArtifact, LabAttempt, LabDefinition, LabDispatch, LabEvent, LabRevision,
    LabRun, LabTarget,
)
from tinystudio.lab.preflight import execution_checks, mismatch


PARAMETERS_SCHEMA = 'tinystudio.lab.hardware-session-parameters.v1'
PROVENANCE_SCHEMA = 'tinystudio.lab.hardware-session-provenance.v1'


def definition(**changes):
    value = {'name': 'Owned session', 'engine': 'hardware.session',
        'operating_mode': 'physical_control',
        'execution': {'backend': 'native', 'profile': 'trusted_native'},
        'inputs': [{'artifact_id': 'supervisor', 'sha256': 'a'*64, 'role': 'source'}],
        'parameters': {'binding_id': 'selected',
            'supervisor': {'kind': 'recorded', 'artifact_id': 'supervisor'}}}
    value.update(changes)
    return ExperimentDefinition.model_validate(value)


def public_binding():
    return HardwareSessionBinding.from_grants({'native_execution': True, 'bindings': {'selected': {
        'gateway_binding': {'endpoint_id': 'owned-endpoint', 'operator_id': 'operator',
            'transport': {'kind': 'serial', 'port': 'loop://', 'baudrate': 9600},
            'command_profile': {'format': 'tinystudio.native-command-profile.v1'}},
        'state_directory': '/private-fixture/unused-session-state'}}}, 'selected').public_description()


def capabilities(**changes):
    value = {'engines': ['hardware.session'], 'runtime': {'python': 'fixture'},
        'profiles_by_backend': {'native': ['trusted_native']},
        'hardware_session_native_execution': True,
        'hardware_session_bindings': [public_binding()]}
    value.update(changes)
    return value


def checks(body=None, caps=None):
    return {row['code']: row for row in execution_checks(
        body if body is not None else definition(), caps if caps is not None else capabilities())}


@pytest.mark.parametrize('mode', ['physical_control', 'shadow', 'acquisition', 'simulation'])
def test_bound_native_session_is_admitted_without_claiming_physical_qualification(mode):
    body, caps = definition(operating_mode=mode), capabilities()
    assert all(row['passed'] for row in checks(body, caps).values())
    assert mismatch(body.model_dump(), caps) is None
    encoded = json.dumps(caps)
    assert 'state_directory' not in encoded and '/private-fixture/' not in encoded
    assert caps['hardware_session_bindings'][0]['qualification']['physical_outcome'] == 'unverified'
    assert caps['hardware_session_bindings'][0]['profile']['qualification']['physical_outcome'] == 'unverified'


@pytest.mark.parametrize('grant', [None, False, 1, 'true', {}, []])
def test_native_execution_requires_the_exact_public_boolean_grant(grant):
    result = checks(caps=capabilities(hardware_session_native_execution=grant))
    assert result['hardware_session_native_execution']['passed'] is False
    assert mismatch(definition().model_dump(), capabilities(hardware_session_native_execution=grant))


@pytest.mark.parametrize('bindings', [None, {}, 'selected', [], [None], ['selected'],
    [{'binding_id': 'neighbor'}], {'selected': {'binding_id': 'selected'}}])
def test_binding_admission_requires_the_exact_selected_id_in_a_public_list(bindings):
    result = checks(caps=capabilities(hardware_session_bindings=bindings))
    assert result['hardware_session_binding']['passed'] is False


def test_binding_qualification_claim_is_not_a_physical_admission_gate():
    caps = capabilities(hardware_session_bindings=[{'binding_id': 'selected',
        'qualification': {'physical_outcome': 'unverified'}}])
    assert all(row['passed'] for row in checks(caps=caps).values())


@pytest.mark.parametrize('execution', [
    {'backend': 'native', 'profile': 'isolated'},
    {'backend': 'container', 'profile': 'trusted_native'},
    {'backend': 'container', 'profile': 'isolated'},
])
def test_host_advertisements_cannot_bypass_the_implemented_native_profile(execution):
    caps = capabilities(engines_by_backend={execution['backend']: ['hardware.session']},
        profiles_by_backend={execution['backend']: [execution['profile']]},
        runtime_by_backend={execution['backend']: {'python': 'fixture'}})
    result = checks(definition(execution=execution), caps)
    assert result['hardware_session_selection']['passed'] is False
    assert 'native' in result['hardware_session_selection']['message']


@pytest.mark.parametrize('change', [
    lambda value: value['parameters'].pop('binding_id'),
    lambda value: value['parameters'].update(unused_control={'limit': 1}),
    lambda value: value['parameters']['supervisor'].update(artifact_id='unbound'),
    lambda value: value['parameters']['supervisor'].update(kind='unimplemented'),
    lambda value: value['parameters'].update(evidence={'flush_records': 0}),
])
def test_preflight_uses_the_frozen_supervisor_selection_contract(change):
    value = definition().model_dump()
    change(value)
    result = checks(ExperimentDefinition.model_validate(value))
    assert result['hardware_session_selection']['passed'] is False
    assert mismatch(value, capabilities())


@pytest.mark.parametrize('engine', ['synthetic.signal', 'hardware.discover', 'acquisition.serial'])
def test_other_engines_cannot_use_the_session_physical_control_exception(engine):
    result = checks(definition(engine=engine), capabilities(engines=[engine]))
    assert result['physical_control']['passed'] is False


def test_pure_selection_and_preflight_open_no_device_or_ipc_socket(monkeypatch):
    caps = capabilities()
    monkeypatch.setattr(socket, 'socket', lambda *args, **kwargs: pytest.fail('preflight must not open a socket'))
    assert all(row['passed'] for row in checks(caps=caps).values())


def session_row():
    return next(row for row in service.catalog()['methods'] if row['id'] == 'hardware.session')


def test_catalog_registers_the_native_bridge_and_pure_source_provenance(monkeypatch):
    monkeypatch.setattr(socket, 'socket', lambda *args, **kwargs: pytest.fail('catalog must not open a socket'))
    row = session_row()
    assert row['implemented'] is True and row['available'] is True
    assert row['requires_operator_binding'] is True
    assert row['execution_profiles'] == {'native': ['trusted_native']}
    assert row['qualification'] == row['manifest']['qualification'] == 'unqualified'
    requirements = ' '.join(row['manifest']['targets']['additional_requirements'])
    assert 'trusted_native' in requirements and 'physical' in requirements
    provenance = row['provenance']
    assert provenance['format'] == PROVENANCE_SCHEMA
    assert row['manifest']['implementation_version'] == 'sha256:' + provenance['implementation_sha256']
    root = Path(service.__file__).parent
    assert {'hardware_session_engine.py', 'hardware_session_bridge.py', 'attempt_bound_transport.py'} <= provenance['files'].keys()
    assert all(hashlib.sha256((root/name).read_bytes()).hexdigest() == digest
               for name, digest in provenance['files'].items())
    assert {'tests/test_lab_hardware_session_engine.py', 'tests/test_lab_hardware_session_preflight.py'} <= {
        item['path'] for item in row['manifest']['fixtures']}


def test_changed_bridge_source_changes_the_catalog_implementation_identity(monkeypatch):
    before = session_row()['manifest']['implementation_version']
    read_bytes = Path.read_bytes
    monkeypatch.setattr(Path, 'read_bytes', lambda path: read_bytes(path) + b'\n# controlled source change\n'
        if path.name == 'attempt_bound_transport.py' else read_bytes(path))
    assert session_row()['manifest']['implementation_version'] != before


@pytest.mark.parametrize('kind', ['recorded', 'python'])
@pytest.mark.parametrize('flush', [None, 'on_close', 1, 10**30])
def test_advertised_parameter_schema_preserves_supported_supervisors_and_explicit_policy(kind, flush):
    # JSON Schema is already present in scientific runtimes; it is not needed by admission.
    validator = pytest.importorskip('jsonschema').Draft202012Validator
    portfolio = service.catalog()
    row = next(row for row in portfolio['methods'] if row['id'] == 'hardware.session')
    assert row['parameters_schema_ref'] == PARAMETERS_SCHEMA
    schema = portfolio['contract_schemas'][PARAMETERS_SCHEMA]
    parameters = deepcopy(definition().parameters)
    parameters['supervisor']['kind'] = kind
    if kind == 'python':
        parameters['supervisor']['callable'] = 'operator.rule'
    parameters.update(supervisor_parameters={'authored': [None, True, 9007199254740993, {'units': 'operator-defined'}]},
        evidence={'flush_records': flush, 'fsync': False}, ipc_timeout_seconds=.00000001,
        artifact_visibility='truth')
    validator.check_schema(schema)
    validator(schema).validate(parameters)
    selected = validate_selection({**definition().model_dump(), 'parameters': parameters})
    assert selected.to_dict()['supervisor_parameters'] == parameters['supervisor_parameters']
    assert not any(key.startswith('max_') for key in schema['properties'])
    validator(portfolio['contract_schemas'][PROVENANCE_SCHEMA]).validate(row['provenance'])


@pytest.mark.parametrize('change', [
    lambda parameters: parameters.pop('binding_id'),
    lambda parameters: parameters['supervisor'].update(callable='extra'),
    lambda parameters: parameters['supervisor'].update(kind='python'),
    lambda parameters: parameters.update(ipc_timeout_seconds=0),
    lambda parameters: parameters.update(evidence={'flush_records': False}),
    lambda parameters: parameters.update(evidence={'fsync': 'false'}),
    lambda parameters: parameters.update(unused_parameter={}),
])
def test_parameter_schema_rejects_unimplemented_or_ambiguous_choices(change):
    jsonschema = pytest.importorskip('jsonschema')
    schema = service.catalog()['contract_schemas'][PARAMETERS_SCHEMA]
    parameters = deepcopy(definition().parameters)
    change(parameters)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(schema).validate(parameters)
    with pytest.raises(ValueError):
        validate_selection({**definition().model_dump(), 'parameters': parameters})


@pytest.fixture
def database(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + str(tmp_path/'admission.sqlite3'))
    tables = [User.__table__, Project.__table__, LabDefinition.__table__, LabRevision.__table__,
        LabTarget.__table__, LabRun.__table__, LabAttempt.__table__, LabEvent.__table__,
        LabArtifact.__table__, LabDispatch.__table__]
    Base.metadata.create_all(engine, tables=tables)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as db:
        db.add(Project(id='p', slug='owned-session', name='Owned session'))
        db.add(LabTarget(id='target', project_id='p', name='Owned target', enabled=True,
            capabilities=capabilities(), last_seen=service.now()))
        db.add(LabArtifact(id='supervisor', project_id='p', name='decisions.jsonl',
            sha256='a'*64, size=0, media_type='application/x-ndjson',
            storage_key='unused-admission-fixture', role='source', visibility='model'))
        db.flush()
    # The SQL checks exercise admission only, not source snapshot collection or execution.
    monkeypatch.setattr(service, 'implementation_manifest', lambda: {'version': 1, 'files': {'fixture.py': 'b'*64}})
    yield factory
    engine.dispose()


def test_actual_submit_admits_bound_session_and_freezes_selected_definition(database):
    with database.begin() as db:
        authored = definition().model_dump()
        saved = service.save_definition(db, 'p', authored)
        report = service.preflight(db, 'p', authored, 'target')
        assert report['compatible'] and report['qualification'] == 'unqualified'
        assert report['execution_started'] is False
        run = service.submit(db, 'p', saved['id'], 1, 'target', 'session-request')
        assert run.state == 'queued' and run.spec['definition'] == authored
        assert run.spec_digest == checksum(run.spec)
        assert db.get(LabDispatch, run.id) is not None
        assert service.submit(db, 'p', saved['id'], 1, 'target', 'session-request').id == run.id


@pytest.mark.parametrize('changes', [
    {'hardware_session_native_execution': False},
    {'hardware_session_bindings': [{'binding_id': 'neighbor'}]},
])
def test_actual_submit_defers_session_authority_to_binding_preflight(database, changes):
    with database.begin() as db:
        target = db.get(LabTarget, 'target')
        target.capabilities = {**target.capabilities, **changes}
        authored = definition().model_dump()
        saved = service.save_definition(db, 'p', authored)
        with pytest.raises(service.LabError, match='hardware.session|binding') as caught:
            service.submit(db, 'p', saved['id'], 1, 'target', 'denied-request')
        assert caught.value.status == 409
        assert db.scalar(select(LabRun.id)) is None


def test_actual_submit_still_rejects_other_physical_control_engines(database):
    with database.begin() as db:
        authored = definition(engine='synthetic.signal').model_dump()
        saved = service.save_definition(db, 'p', authored)
        with pytest.raises(service.LabError, match='physical control'):
            service.submit(db, 'p', saved['id'], 1, 'target', 'unsupported-request')
        assert db.scalar(select(LabRun.id)) is None
