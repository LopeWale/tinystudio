"""An optional process exposes authored lifecycle choices, not scientific gates."""
from copy import deepcopy
import json
from pathlib import Path

import jsonschema
import pytest

from tinystudio.lab.hardware_emulator_selection import EmulatorProcessBinding, EmulatorSelection
from tinystudio.lab.hardware_session_engine import validate_selection
from tinystudio.lab.hardware_session_configuration import HardwareSessionRuntimePaths
from tinystudio.lab.emulator_workspace import EmulatorWorkspace
from tinystudio.lab.plugins import SCHEMAS
from tests.test_lab_hardware_emulator_selection import definition, grants


def session_definition(**changes):
    body = definition(**changes)
    body.update(operating_mode='simulation', execution={'backend': 'native', 'profile': 'trusted_native'})
    body['parameters'].update(binding_id='device', supervisor={'kind': 'recorded', 'artifact_id': 'input'})
    return body


def test_exit_is_observed_by_default_and_failure_reaction_is_an_authored_choice():
    authored = session_definition()
    before = deepcopy(authored)
    selected = validate_selection(authored)
    assert selected.emulator.on_exit == 'record'
    assert selected.to_dict()['emulator']['on_exit'] == 'record'
    explicit = validate_selection(session_definition(on_exit='fail_session'))
    assert explicit.emulator.on_exit == 'fail_session'
    assert explicit.emulator.digest != selected.emulator.digest
    assert authored == before
    for invalid in (True, None, 'proves_model_failure'):
        with pytest.raises(ValueError):
            validate_selection(session_definition(on_exit=invalid))


def test_absent_or_explicitly_disabled_process_preserves_legacy_supervisor_selection():
    authored = session_definition()
    authored['parameters']['emulator'] = None
    disabled = validate_selection(authored)
    assert disabled.emulator is None and 'emulator' not in disabled.to_dict()
    del authored['parameters']['emulator']
    assert validate_selection(authored).to_dict() == disabled.to_dict()


def test_private_process_environment_is_selected_separately_without_public_serialization():
    binding = EmulatorProcessBinding.from_grants(grants(), 'selected')
    private = {'bindings': {'device': {'password': 'private-device-secret'}},
               'emulator_bindings': {'selected': {'environment': {'ARBITRARY_TOKEN': 'private-process-secret', 'EMPTY': ''}}}}
    values = binding.select_runtime_secrets(private)
    assert values == private['emulator_bindings']['selected']
    values['environment']['ARBITRARY_TOKEN'] = 'changed'
    assert private['emulator_bindings']['selected']['environment']['ARBITRARY_TOKEN'] == 'private-process-secret'
    assert 'private-process-secret' not in json.dumps(binding.as_dict())
    assert binding.select_runtime_secrets(None) == {}
    for invalid in ({'unexpected': {}}, {'emulator_bindings': []},
                    {'emulator_bindings': {'selected': {'environment': {'TOKEN': 1}}}}):
        with pytest.raises(ValueError):
            binding.select_runtime_secrets(invalid)


def test_catalog_schema_describes_the_same_optional_portable_process_contract():
    schema = SCHEMAS['tinystudio.lab.hardware-session-parameters.v1']
    for reaction in ('record', 'fail_session'):
        parameters = validate_selection(session_definition(on_exit=reaction)).to_dict()
        jsonschema.validate(parameters, schema)
        assert parameters['emulator']['on_exit'] == reaction
    jsonschema.validate(session_definition()['parameters'] | {'emulator': None}, schema)
    malformed = session_definition(readiness={'kind': 'command', 'argv': []})
    with pytest.raises(ValueError):
        validate_selection(malformed)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(malformed['parameters'], schema)


def test_child_accepts_selected_parent_directories_without_exporting_their_contents(tmp_path, monkeypatch):
    from tests.test_lab_hardware_session_engine import selected, install_client, module
    emulator = {'binding_id': 'model', 'assets': [], 'argv': [], 'readiness': {'kind': 'process_running'}}
    spec = selected(tmp_path, '', emulator=emulator)
    install_client(monkeypatch, spec)
    work = tmp_path / 'work'
    paths = HardwareSessionRuntimePaths(work)
    workspace = EmulatorWorkspace(work, validate_selection(spec['definition']).emulator)
    for directory in (paths.bridge, paths.evidence, paths.emulator.evidence_directory, workspace.evidence_directory):
        directory.mkdir(parents=True)
        (directory / 'private-parent-marker').write_text('private-parent-observation')
    result = module().execute_hardware_session(spec, work / 'evidence', lambda event: None, lambda: False)
    assert result['qualification']['complete'] is True
    assert all('private-parent-marker' not in item['path'] for item in result['artifacts'])
    assert all(b'private-parent-observation' not in Path(item['path']).read_bytes()
               for item in result['artifacts'])
