"""Executable-file observations follow the owned process cwd and exact PATH."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import time

import pytest

from tests.test_lab_managed_emulator import control
from tinystudio.lab.evidence_io import RecordWritePolicy
from tinystudio.lab.managed_emulator import ManagedEmulatorProcess
from tinystudio.lab.managed_process_configuration import (
    ManagedProcessPaths, ManagedProcessSelection, ReadinessSelection,
)


def program(path, marker):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('#!' + sys.executable + '\nimport os,time\nos.write(1,' + repr(marker) + ')\ntime.sleep(30)\n')
    path.chmod(0o700)


@pytest.mark.parametrize('choice', [
    'relative_path', 'relative_cwd', 'empty_path', 'leading_empty', 'middle_empty',
    'mixed_relative_absolute', 'absolute_path', 'direct_relative', 'absolute_executable', 'symlink_executable',
])
def test_observed_hash_names_selected_executable_and_never_a_controller_cwd_decoy(tmp_path, monkeypatch, choice):
    controller, selected, alternate = (tmp_path / name for name in ('controller', 'selected', 'alternate'))
    filename = 'owned-executable'
    actual = selected / 'bin' / filename
    decoy = controller / 'bin' / filename
    program(actual, b'selected-program')
    program(decoy, b'controller-decoy')
    program(alternate / filename, b'alternate-decoy')
    program(selected / filename, b'selected-program')
    program(controller / filename, b'controller-decoy')
    monkeypatch.chdir(controller)
    argv, cwd, search_path = (filename,), selected, 'bin'
    if choice == 'relative_cwd':
        cwd = Path('../selected')
    elif choice == 'empty_path':
        actual, decoy, search_path = selected / filename, controller / filename, ''
    elif choice == 'leading_empty':
        actual, decoy = selected / filename, controller / filename
        search_path = os.pathsep + str(alternate)
    elif choice == 'middle_empty':
        actual, decoy = selected / filename, controller / filename
        search_path = str(tmp_path / 'missing') + os.pathsep + os.pathsep + str(alternate)
    elif choice == 'mixed_relative_absolute':
        search_path = 'missing' + os.pathsep + 'bin' + os.pathsep + str(alternate)
    elif choice == 'absolute_path':
        search_path = str(selected / 'bin')
    elif choice == 'direct_relative':
        argv = ('./bin/' + filename,)
    elif choice == 'absolute_executable':
        argv = (str(actual),)
    elif choice == 'symlink_executable':
        alias = selected / 'executable-alias'
        alias.symlink_to(actual)
        argv = (str(alias),)
    plan = ManagedProcessSelection(argv, cwd,
        ReadinessSelection('log_bytes', stream='stdout', marker=b'selected-program'),
        public_env={'PATH': search_path}, startup_timeout_seconds=3, shutdown_grace_seconds=.03)
    authored = plan.as_dict()
    launched = []

    def launch(arguments, **kwargs):
        from tinystudio.lab.runner import ProcessGroup
        launched.append({'argv': tuple(arguments), 'cwd': kwargs['cwd'], 'env': dict(kwargs['env'])})
        return ProcessGroup(arguments, **kwargs)

    owned = ManagedEmulatorProcess(plan, control(), ManagedProcessPaths(tmp_path / 'work'),
        process_factory=launch, evidence_policy=RecordWritePolicy(fsync=False))
    try:
        owned.start()
        assert owned.wait_ready()['ready']
    finally:
        closed = owned.close('completed')
    assert owned.paths.private_log('stdout').read_bytes() == b'selected-program'
    expected = hashlib.sha256(actual.read_bytes()).hexdigest()
    unrelated = hashlib.sha256(decoy.read_bytes()).hexdigest()
    assert expected != unrelated
    observed = closed['executable_identity']
    assert observed['status'] == 'observed' and observed['sha256'] == expected
    assert observed['size'] == actual.stat().st_size
    assert observed['scope'] == 'observed_file_not_execution_attestation'
    assert closed['qualification']['executed_binary_attestation'] is False
    assert closed['cleanup_confirmed']
    assert launched == [{'argv': argv, 'cwd': cwd, 'env': {'PATH': search_path}}]
    assert plan.as_dict() == authored


@pytest.mark.skipif(sys.platform != 'linux', reason='bounded controller-loss descendant proof uses the Linux owned reaper')
def test_replaced_executable_fifo_cannot_block_the_owned_controller_observation(tmp_path):
    from tinystudio.lab.runner import ProcessGroup
    controller = tmp_path / 'controller.py'
    controller.write_text('''from pathlib import Path
import json,os,sys,time
from tinystudio.lab.attempt_control import AttemptControl,AttemptIdentity
from tinystudio.lab.evidence_io import RecordWritePolicy
from tinystudio.lab.managed_emulator import ManagedEmulatorProcess
from tinystudio.lab.managed_process_configuration import ManagedProcessSelection,ManagedProcessPaths,ReadinessSelection
from tinystudio.lab.runner import ProcessGroup
root=Path(sys.argv[1]); program=root/'program'; running=root/'program-running'
program.write_text('#!'+sys.executable+'\\nfrom pathlib import Path\\nimport time\\nPath('+repr(str(running))+').write_text("running")\\ntime.sleep(30)\\n');program.chmod(0o700)
control=AttemptControl(AttemptIdentity('http://127.0.0.1','run','attempt','target','a'*64));control.renew(30,sent_at=time.monotonic())
def launch(*args,**kwargs):
    group=ProcessGroup(*args,**kwargs)
    deadline=time.monotonic()+5
    while not running.exists() and time.monotonic()<deadline:time.sleep(.01)
    assert running.read_text()=='running'
    program.unlink();os.mkfifo(program,mode=0o700)
    (root/'launched.json').write_text(json.dumps({'main_pid':group.process.pid,'helper_pid':group.process.owner.pid}))
    return group
owned=ManagedEmulatorProcess(ManagedProcessSelection((str(program),),root,ReadinessSelection('process_running'),startup_timeout_seconds=0,shutdown_grace_seconds=.03),control,ManagedProcessPaths(root/'work'),process_factory=launch,evidence_policy=RecordWritePolicy(fsync=False))
try:
    observed=owned.start()
    (root/'returned.json').write_text(json.dumps(observed))
finally:
    owned.close('completed')
''')
    environment = {'PATH': os.defpath, 'PYTHONPATH': str(Path(__file__).resolve().parents[1]),
        'TINYSTUDIO_SKIP_DOTENV': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
    outer = ProcessGroup([sys.executable, str(controller), str(tmp_path)], grace=.1, env=environment,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pids = [outer.process.pid, outer.process.owner.pid]
    blocked = False
    exit_code = None
    try:
        deadline = time.monotonic() + 5
        while not (tmp_path / 'launched.json').exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert (tmp_path / 'launched.json').exists(), 'owned process did not reach the replacement boundary'
        pids.extend(json.loads((tmp_path / 'launched.json').read_text()).values())
        try: exit_code = outer.process.wait(timeout=2)
        except subprocess.TimeoutExpired: blocked = True
    finally:
        outer.close()
    alive = []
    for pid in pids:
        try: os.kill(pid, 0)
        except ProcessLookupError: continue
        alive.append(pid)
    assert not alive, 'owned controller/main/helper cleanup left live PIDs: ' + repr(alive)
    assert not blocked, 'post-launch executable observation blocked on the replaced FIFO'
    assert exit_code == 0
    result = json.loads((tmp_path / 'returned.json').read_text())
    assert result['executable_identity']['status'] == 'observation_failed'
    assert result['executable_identity']['error_type'] == 'ValueError'
    assert result['qualification']['executed_binary_attestation'] is False
