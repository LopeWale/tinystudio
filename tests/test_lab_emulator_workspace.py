"""Owned asset custody and derived launch plans use no processes or providers."""
import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

from tinystudio.config import RuntimeTools
from tinystudio.lab.emulator_workspace import EmulatorWorkspace, EmulatorWorkspaceError
from tinystudio.lab.hardware_emulator_selection import EmulatorProcessBinding, EmulatorSelection


@pytest.fixture
def owned(tmp_path):
    original = tmp_path / 'downloaded.bin'
    original.write_bytes(b'\x00\xfforiginal bytes\n')
    definition = {'engine': 'hardware.session', 'inputs': [{'artifact_id': 'asset', 'sha256': hashlib.sha256(original.read_bytes()).hexdigest()}],
        'parameters': {'emulator': {'binding_id': 'selected', 'assets': [{'name': 'firmware', 'artifact_id': 'asset', 'destination': 'project/input.bin'}],
            'argv': [{'asset': 'firmware'}, '', {'workspace': 'outputs'}], 'cwd': 'project',
            'readiness': {'kind': 'command', 'argv': [sys.executable, {'asset': 'firmware'}, {'workspace': '.'}], 'cwd': '.'}}}}
    selection = EmulatorSelection.from_definition(definition)
    workspace = EmulatorWorkspace(tmp_path / 'work', selection)
    binding = EmulatorProcessBinding.from_grants({'native_execution': True, 'emulator_bindings': {
        'selected': {'executable': {'runtime_tool': 'python'}, 'public_env': {'PUBLIC': 'chosen'}}}}, 'selected')
    return original, selection, workspace, binding


def test_constructor_is_pure_and_stream_copy_never_rewrites_downloaded_original(owned):
    original, selection, workspace, binding = owned
    assert not workspace.root.exists()
    original.chmod(0o400)
    before = original.stat()
    prepared = workspace.prepare({'asset': str(original)})
    copied = prepared.path('firmware')
    assert copied.read_bytes() == original.read_bytes()
    assert (original.stat().st_ino, original.stat().st_mode, original.stat().st_mtime_ns) == (before.st_ino, before.st_mode, before.st_mtime_ns)
    assert not copied.is_relative_to(workspace.evidence_directory)
    assert prepared.as_dict()['assets']['firmware']['initial_copy']['sha256'] == selection.assets[0].sha256


def test_launch_tokens_cwd_and_readiness_are_derived_without_private_env_in_public_plans(owned):
    original, selection, workspace, binding = owned
    prepared = workspace.prepare({'asset': original})
    secret = 'private-runtime-sentinel'
    launch = workspace.launch_selection(binding, tools=RuntimeTools(python=sys.executable),
        base_env={'BASE': secret}, runtime_secrets={'environment': {'TOKEN': secret, 'PATH': secret}})
    assert launch.argv == (sys.executable, str(prepared.path('firmware')), '', str(workspace.root / 'outputs'))
    assert launch.cwd == workspace.root / 'project'
    assert launch.readiness.argv == (sys.executable, str(prepared.path('firmware')), str(workspace.root))
    assert launch.readiness.cwd == workspace.root
    assert dict(launch.public_env) == {'PUBLIC': 'chosen'}
    public = json.dumps(launch.as_dict()) + json.dumps(selection.as_dict()) + json.dumps(binding.as_dict())
    assert secret not in public
    assert workspace.executable_observation['status'] == 'observed'


def test_writable_copy_changes_are_observations_and_original_changes_remain_provenance_failure(owned):
    original, selection, workspace, binding = owned
    prepared = workspace.prepare({'asset': original})
    prepared.path('firmware').write_bytes(b'expected vendor project edit')
    report = workspace.seal()
    facts = report['assets']['firmware']
    assert facts['original_status'] == 'matched' and facts['copy_status'] == 'changed'
    assert report['provenance']['originals_match_expected'] is True
    assert report['provenance']['copy_changes'] == 'observed_only'
    assert facts['initial_copy']['sha256'] == selection.assets[0].sha256
    assert facts['final_copy']['sha256'] == hashlib.sha256(b'expected vendor project edit').hexdigest()
    assert original.read_bytes() == b'\x00\xfforiginal bytes\n'


def test_changed_downloaded_original_is_retained_as_explicit_provenance_failure(owned):
    original, selection, workspace, binding = owned
    workspace.prepare({'asset': original})
    original.write_bytes(b'changed download')
    report = workspace.seal()
    assert report['assets']['firmware']['original_status'] == 'sha256_mismatch'
    assert report['provenance']['originals_match_expected'] is False


def test_sealing_after_authority_revocation_needs_no_new_live_authority_and_exports_only_metadata(owned, tmp_path):
    original, selection, workspace, binding = owned
    workspace.prepare({'asset': original})
    (workspace.root / 'private-model.bin').write_bytes(b'not an automatic output')
    report = workspace.seal()
    artifacts = workspace.retain_artifacts(tmp_path / 'retained', visibility='truth')
    assert len(artifacts) == 1 and artifacts[0]['visibility'] == 'truth'
    assert json.loads(Path(artifacts[0]['path']).read_text()) == report
    assert str(tmp_path) not in json.dumps(report)
    assert b'not an automatic output' not in Path(artifacts[0]['path']).read_bytes()


@pytest.mark.parametrize('kind', ['symlink', 'fifo', 'directory'])
def test_original_nonregular_files_are_rejected_without_following_or_waiting(owned, tmp_path, kind):
    original, selection, workspace, binding = owned
    source = tmp_path / 'special'
    if kind == 'symlink': source.symlink_to(original)
    elif kind == 'fifo': os.mkfifo(source)
    else: source.mkdir()
    with pytest.raises(EmulatorWorkspaceError): workspace.prepare({'asset': source})
    report = workspace.seal()
    assert report['provenance']['originals_match_expected'] is False


@pytest.mark.parametrize('kind', ['symlink', 'fifo', 'missing'])
def test_final_copy_replacement_is_classified_without_repair_or_following(owned, tmp_path, kind):
    original, selection, workspace, binding = owned
    prepared = workspace.prepare({'asset': original})
    copied = prepared.path('firmware')
    copied.unlink()
    if kind == 'symlink': copied.symlink_to(original)
    elif kind == 'fifo': os.mkfifo(copied)
    report = workspace.seal()
    assert report['assets']['firmware']['copy_status'] == {'symlink': 'symlink', 'fifo': 'not_regular', 'missing': 'missing'}[kind]
    assert original.read_bytes() == b'\x00\xfforiginal bytes\n'


def test_original_hash_mismatch_retains_partial_custody_instead_of_fixing_input(owned):
    original, selection, workspace, binding = owned
    original.write_bytes(b'wrong source bytes')
    with pytest.raises(EmulatorWorkspaceError): workspace.prepare({'asset': original})
    report = workspace.seal()
    assert report['assets']['firmware']['initial_original']['sha256'] == hashlib.sha256(b'wrong source bytes').hexdigest()
    assert report['provenance']['originals_match_expected'] is False


def test_exclusive_workspace_allocation_and_authority_loss_do_not_overwrite_existing_files(owned):
    original, selection, workspace, binding = owned
    prepared = workspace.prepare({'asset': original})
    with pytest.raises(EmulatorWorkspaceError): workspace.prepare({'asset': original})
    assert prepared.path('firmware').read_bytes() == original.read_bytes()
    second = EmulatorWorkspace(workspace.work / 'other', selection)
    def revoked(): raise RuntimeError('authority revoked')
    with pytest.raises(RuntimeError, match='revoked'): second.prepare({'asset': original}, check_authority=revoked)
    assert not second.root.exists()


def test_interrupted_stream_copy_retains_partial_facts_and_can_be_sealed_locally(owned):
    original, selected, workspace, binding = owned
    original.write_bytes(b'x' * (3 * 1024 * 1024))
    definition = {'engine': 'hardware.session', 'inputs': [{'artifact_id': 'asset', 'sha256': hashlib.sha256(original.read_bytes()).hexdigest()}],
        'parameters': {'emulator': selected.as_dict()}}
    workspace = EmulatorWorkspace(workspace.work, EmulatorSelection.from_definition(definition))
    copied = workspace.root / 'project/input.bin'
    def revoke_after_first_copy_block():
        if copied.exists() and copied.stat().st_size:
            raise RuntimeError('ordinary authority revoked')
    with pytest.raises(RuntimeError, match='revoked'):
        workspace.prepare({'asset': original}, check_authority=revoke_after_first_copy_block)
    report = workspace.seal()
    assert report['status'] == 'partial_preparation' and report['prepare_error']['type'] == 'RuntimeError'
    assert 0 < report['assets']['firmware']['final_copy']['size'] < original.stat().st_size
    assert report['assets']['firmware']['original_status'] == 'matched'


def test_internal_parent_symlink_is_not_followed_during_sealing(owned, tmp_path):
    original, selected, workspace, binding = owned
    workspace.prepare({'asset': original})
    outside = tmp_path / 'outside'
    outside.mkdir()
    sentinel = outside / 'input.bin'
    sentinel.write_bytes(b'must remain untouched')
    (workspace.root / 'project').rename(workspace.root / 'saved-project')
    (workspace.root / 'project').symlink_to(outside, target_is_directory=True)
    report = workspace.seal()
    assert report['assets']['firmware']['copy_status'] == 'unreadable'
    assert sentinel.read_bytes() == b'must remain untouched'


def test_operator_ancestor_alias_is_allowed_but_final_work_symlink_is_rejected(owned, tmp_path):
    original, selected, workspace, binding = owned
    actual = tmp_path / 'actual'
    actual.mkdir()
    alias = tmp_path / 'alias'
    alias.symlink_to(actual, target_is_directory=True)
    accepted = EmulatorWorkspace(alias / 'work', selected)
    accepted.prepare({'asset': original})
    assert accepted.root == actual / 'work/parent-emulator-work'
    final = tmp_path / 'final'
    final.symlink_to(actual, target_is_directory=True)
    rejected = EmulatorWorkspace(final, selected)
    with pytest.raises(EmulatorWorkspaceError): rejected.prepare({'asset': original})


def test_metadata_collision_tampering_and_export_directory_symlinks_are_rejected(owned, tmp_path):
    original, selected, workspace, binding = owned
    workspace.prepare({'asset': original})
    workspace.seal()
    retained = tmp_path / 'retained'
    retained.mkdir()
    target = retained / 'emulator-assets.json'
    target.write_bytes(b'existing unrelated bytes')
    with pytest.raises(EmulatorWorkspaceError): workspace.retain_artifacts(retained)
    assert target.read_bytes() == b'existing unrelated bytes'
    alias = tmp_path / 'output-alias'
    alias.symlink_to(retained, target_is_directory=True)
    with pytest.raises(EmulatorWorkspaceError): workspace.retain_artifacts(alias)
    workspace.report_path.write_bytes(b'altered custody metadata')
    with pytest.raises(EmulatorWorkspaceError, match='changed'): workspace.retain_artifacts(tmp_path / 'other')


def test_metadata_write_failure_retains_sanitized_report_and_no_private_diagnostics(owned, monkeypatch):
    original, selected, workspace, binding = owned
    workspace.prepare({'asset': original})
    def failed(report): raise OSError('private-credential-and-path-sentinel')
    monkeypatch.setattr(workspace, '_write_report', failed)
    with pytest.raises(EmulatorWorkspaceError) as caught: workspace.seal()
    assert caught.value.report['assets']['firmware']['original_status'] == 'matched'
    assert 'private-credential' not in str(caught.value) + json.dumps(caught.value.report)


@pytest.mark.parametrize('search_path', ['bin', '', ':bin'])
def test_executable_observation_uses_selected_cwd_and_private_overlay_without_publishing_it(owned, tmp_path, monkeypatch, search_path):
    original, selected, workspace, binding = owned
    workspace.prepare({'asset': original})
    cwd = workspace.root / 'project'
    controller = tmp_path / 'controller'
    controller.mkdir()
    monkeypatch.chdir(controller)
    for root, payload in ((cwd, b'selected executable'), (controller, b'wrong controller executable')):
        (root / 'bin').mkdir()
        for path in (root / 'tool', root / 'bin/tool'):
            path.write_bytes(payload)
            path.chmod(0o700)
    binding = EmulatorProcessBinding.from_grants({'native_execution': True, 'emulator_bindings': {
        'selected': {'executable': {'argv_prefix': ['tool']}, 'public_env': {'PUBLIC': 'chosen'}}}}, 'selected')
    launch = workspace.launch_selection(binding, base_env={'PATH': 'not-selected'},
        runtime_secrets={'environment': {'PATH': search_path, 'TOKEN': 'private-runtime-sentinel'}})
    assert launch.argv[0] == 'tool'
    assert workspace.executable_observation['sha256'] == hashlib.sha256(b'selected executable').hexdigest()
    assert dict(launch.public_env) == {'PUBLIC': 'chosen'}
    assert 'private-runtime-sentinel' not in json.dumps(launch.as_dict()) + json.dumps(workspace.seal())


def test_executable_fifo_is_rejected_without_reading_it_and_missing_program_diagnostics_are_private(owned, tmp_path):
    original, selected, workspace, binding = owned
    workspace.prepare({'asset': original})
    fifo = tmp_path / 'program-fifo'
    os.mkfifo(fifo, mode=0o700)
    for program in (str(fifo), str(tmp_path / 'private-path-sentinel')):
        binding = EmulatorProcessBinding.from_grants({'native_execution': True, 'emulator_bindings': {
            'selected': {'executable': {'argv_prefix': [program]}}}}, 'selected')
        with pytest.raises(EmulatorWorkspaceError) as caught:
            workspace.launch_selection(binding)
        assert 'private-path-sentinel' not in str(caught.value)


def test_metadata_replaced_by_fifo_between_check_and_copy_cannot_block_retention(owned, tmp_path, monkeypatch):
    original, selected, workspace, binding = owned
    workspace.prepare({'asset': original})
    workspace.seal()
    opened = os.open
    calls = []
    def replace_before_copy(path, flags, *args, **kwargs):
        if Path(path) == workspace.report_path:
            calls.append(flags)
            if len(calls) == 2:
                workspace.report_path.unlink()
                os.mkfifo(workspace.report_path)
                assert flags & os.O_NONBLOCK and flags & os.O_NOFOLLOW
        return opened(path, flags, *args, **kwargs)
    monkeypatch.setattr(os, 'open', replace_before_copy)
    with pytest.raises(EmulatorWorkspaceError, match='regular'):
        workspace.retain_artifacts(tmp_path / 'retained')
    assert len(calls) == 2
