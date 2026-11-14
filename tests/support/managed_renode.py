"""Owned optional Renode fixture; importing this module starts no resources.

The guest is a 32-byte, directly encoded RV32I subset on a RISC-V CPU model
with an NS16550 UART. It transforms each received byte by XOR0xa5. No C build,
physical device, model fidelity, real-time or integrated Runner lease is claimed.
Renode's wildcard TCP terminal is deliberately unused: an owned Python bridge
connects its UART PTY to a listener bound only to127.0.0.1.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time


@dataclass(frozen=True)
class ManagedRenodePaths:
    root: Path

    def __post_init__(self):
        object.__setattr__(self, 'root', Path(self.root).absolute())

    @property
    def firmware(self): return self.root / 'firmware.bin'
    @property
    def assembly(self): return self.root / 'firmware.s'
    @property
    def listing(self): return self.root / 'firmware-listing.json'
    @property
    def platform(self): return self.root / 'owned.repl'
    @property
    def script(self): return self.root / 'owned.resc'
    @property
    def provenance(self): return self.root / 'source-provenance.json'
    @property
    def pty(self): return self.root / 'uart-pty'
    @property
    def pid(self): return self.root / 'renode.pid'
    @property
    def config(self): return self.root / 'renode.config'
    @property
    def bridge_identity(self): return self.root / 'bridge-identity.json'
    @property
    def request(self): return self.root / 'uart-request.bin'
    @property
    def response(self): return self.root / 'uart-response.bin'
    @property
    def client_result(self): return self.root / 'uart-client-result.json'
    @property
    def result(self): return self.root / 'result.json'
    @property
    def home(self): return self.root / 'home'
    @property
    def temporary(self): return self.root / 'tmp'
    @property
    def managed(self): return self.root / 'managed'
    @property
    def retained(self): return self.root / 'retained-parent'

    def helper(self, kind):
        if kind not in {'bridge', 'client'}: raise ValueError('unknown owned fixture helper')
        return self.root / (kind + '.py')

    def log(self, role, stream):
        if role not in {'bridge', 'client', 'version'} or stream not in {'stdout', 'stderr'}:
            raise ValueError('unknown owned fixture log')
        return self.root / (role + '.' + stream + '.bin')

    @property
    def frozen_sources(self):
        return (self.firmware, self.assembly, self.listing, self.platform, self.script,
                self.helper('bridge'), self.helper('client'))


def _sha(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def _json(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n')
    path.chmod(0o600)


def _monitor_path(path):
    # Renode PathToken uses backslash-space, rather than @"quoted paths".
    return '@' + str(path).replace(' ', '\\ ')


def encode_firmware():
    """Encode only the listed instructions, with no installed cross compiler."""
    def immediate(opcode, funct3, rd, rs1, value):
        assert -2048 <= value <= 2047
        return (value & 0xfff) << 20 | rs1 << 15 | funct3 << 12 | rd << 7 | opcode

    def branch(rs1, rs2, displacement):
        value = displacement & 0x1fff
        return ((value >> 12) & 1) << 31 | ((value >> 5) & 63) << 25 | rs2 << 20 | rs1 << 15 | ((value >> 1) & 15) << 8 | ((value >> 11) & 1) << 7 | 0x63

    def jump(displacement):
        value = displacement & 0x1fffff
        return ((value >> 20) & 1) << 31 | ((value >> 1) & 1023) << 21 | ((value >> 11) & 1) << 20 | ((value >> 12) & 255) << 12 | 0x6f

    instructions = [
        ('lui x5, 0x10000', 0x10000 << 12 | 5 << 7 | 0x37),
        ('lbu x6, 5(x5)', immediate(0x03, 4, 6, 5, 5)),
        ('andi x6, x6, 1', immediate(0x13, 7, 6, 6, 1)),
        ('beq x6, x0, poll', branch(6, 0, 4 - 12)),
        ('lbu x6, 0(x5)', immediate(0x03, 4, 6, 5, 0)),
        ('xori x6, x6, 0xa5', immediate(0x13, 4, 6, 6, 0xa5)),
        ('sb x6, 0(x5)', 6 << 20 | 5 << 15 | 0x23),
        ('jal x0, poll', jump(4 - 28)),
    ]
    binary = b''.join(struct.pack('<I', word) for _, word in instructions)
    listing = [{'address': hex(0x80000000 + 4 * index), 'mnemonic': mnemonic, 'word': f'{word:08x}'}
               for index, (mnemonic, word) in enumerate(instructions)]
    return binary, listing


# These helpers import only stdlib, are written unchanged into the owned root,
# and execute in isolated mode in separate owned ProcessGroups.
_BRIDGE = '''import json,os,select,socket,sys,time,tty
from pathlib import Path
pty_path,identity_path=map(Path,sys.argv[1:])
with socket.socket() as server:
    server.bind(('127.0.0.1',0))
    identity_path.write_text(json.dumps({'pid':os.getpid(),'address':server.getsockname()}))
    deadline=time.monotonic()+30
    while not pty_path.exists():
        if time.monotonic()>=deadline: raise TimeoutError('owned UART PTY did not appear')
        time.sleep(.01)
    fd=os.open(pty_path,os.O_RDWR|os.O_NOCTTY|os.O_NONBLOCK)
    try:
        tty.setraw(fd)
        server.listen(1)
        while True:
            connection,_=server.accept()
            with connection:
                while True:
                    ready,_,_=select.select([fd,connection],[],[])
                    if connection in ready:
                        data=connection.recv(4096)
                        if not data: break
                        view=memoryview(data)
                        while view:
                            try: view=view[os.write(fd,view):]
                            except BlockingIOError: select.select([],[fd],[])
                    if fd in ready:
                        try: data=os.read(fd,4096)
                        except BlockingIOError: continue
                        if data: connection.sendall(data)
    finally: os.close(fd)
'''

_CLIENT = '''import hashlib,json,os,socket,sys
from pathlib import Path
port=int(sys.argv[1]); request,response,result=map(Path,sys.argv[2:])
sent=bytes(range(256)); received=bytearray()
with socket.create_connection(('127.0.0.1',port),timeout=10) as connection:
    for value in sent:
        connection.sendall(bytes([value]))
        actual=connection.recv(1)
        if not actual: raise RuntimeError('UART ended before firmware response')
        received.extend(actual)
request.write_bytes(sent); response.write_bytes(received)
assert received==bytes(value^0xa5 for value in sent),'firmware XOR predicate failed'
result.write_text(json.dumps({'pid':os.getpid(),'request_bytes':len(sent),'response_bytes':len(received),
    'request_sha256':hashlib.sha256(sent).hexdigest(),'response_sha256':hashlib.sha256(received).hexdigest(),
    'predicate':'sequential byte0..255 returns byte XOR0xa5','passed':True}))
'''


def prepare_fixture(paths):
    paths.root.mkdir(parents=True, exist_ok=False, mode=0o700)
    paths.root.chmod(0o700)
    for directory in (paths.home, paths.temporary):
        directory.mkdir(mode=0o700)
    binary, listing = encode_firmware()
    paths.firmware.write_bytes(binary)
    paths.assembly.write_text('# Owned RV32I subset; no libc/interrupts/C build. UART byte XOR0xa5.\n' +
        '\n'.join(('poll:\n' if index == 1 else '') + '    ' + row['mnemonic'] for index, row in enumerate(listing)) + '\n')
    _json(paths.listing, listing)
    paths.helper('bridge').write_text(_BRIDGE)
    paths.helper('client').write_text(_CLIENT)
    paths.platform.write_text('''cpu: CPU.RiscV32 @ sysbus
    cpuType: "rv32imac"
    privilegedArchitecture: PrivilegedArchitecture.Priv1_10
    timeProvider: empty
    PerformanceInMips: 1

ram: Memory.MappedMemory @ sysbus 0x80000000
    size: 0x10000

uart: UART.NS16550 @ sysbus 0x10000000
''')
    # Explicit Renode file tokens also support an owned root with spaces.
    paths.script.write_text(f'''mach create "owned-rv32i-uart"
machine LoadPlatformDescription {_monitor_path(paths.platform)}
sysbus LoadBinary {_monitor_path(paths.firmware)} 0x80000000
cpu PC 0x80000000
emulation CreateUartPtyTerminal "owned-pty" {json.dumps(str(paths.pty))}
connector Connect sysbus.uart owned-pty
start
''')
    provenance = {'format': 'owned-renode-fixture-source.v1',
        'isa_used': 'RV32I subset: LUI,LBU,ANDI,BEQ,XORI,SB,JAL', 'configured_cpu_type': 'rv32imac',
        'peripheral_model': 'UART.NS16550 byte registers at0x10000000', 'ram_base': '0x80000000',
        'fixture_performance_in_mips': 1, 'encoder': 'explicit Python instruction encoder',
        'python_version': sys.version, 'python_executable': sys.executable,
        'support_module_sha256': _sha(Path(__file__)), 'cross_compiler_used': False, 'same_c_binary_claim': False,
        'files': [{'name': str(path.relative_to(paths.root)), 'sha256': _sha(path), 'bytes': path.stat().st_size}
                  for path in paths.frozen_sources]}
    _json(paths.provenance, provenance)
    for source in (*paths.frozen_sources, paths.provenance):
        source.chmod(0o444)
    return provenance


class OwnedRenodeFixture:
    """No launch/download/availability I/O until entering the owned context."""

    def __init__(self, binary, paths):
        self.binary, self.paths = Path(binary).absolute(), paths
        self.bridge = self.client = self.managed = self.control = None
        self.provenance = self.bridge_identity = self.ready = None
        self._streams = []
        self._groups = {}
        self._launch_attempts = set()
        self._settled = {}
        self._close_attempted = False
        self._application_passed = False
        self.receipt = None
        self.tool = None

    def _environment(self):
        return {'HOME': str(self.paths.home), 'TMPDIR': str(self.paths.temporary), 'PATH': os.defpath,
            'XDG_CONFIG_HOME': str(self.paths.home / 'config'), 'XDG_CACHE_HOME': str(self.paths.home / 'cache'),
            'DOTNET_CLI_TELEMETRY_OPTOUT': '1', 'DOTNET_SKIP_FIRST_TIME_EXPERIENCE': '1',
            'DOTNET_PROCESSOR_COUNT': '1', 'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'}

    def _log(self, role, stream):
        output = self.paths.log(role, stream).open('xb')
        self.paths.log(role, stream).chmod(0o600)
        self._streams.append(output)
        return output

    def _launch(self, argv, role, cwd=None):
        from tinystudio.lab.runner import ProcessGroup
        if role in self._launch_attempts: raise RuntimeError('owned fixture role cannot be launched again')
        stdout, stderr = self._log(role, 'stdout'), self._log(role, 'stderr')
        self._launch_attempts.add(role)
        group = ProcessGroup(list(argv), grace=.3, cwd=cwd or self.paths.root, env=self._environment(),
            stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
        self._groups[role] = group
        return group

    def _settle(self, role):
        if role not in self._settled:
            group = self._groups.get(role)
            result = {'cleanup_confirmed': False, 'exit_code': None}
            self._settled[role] = result  # A failed cleanup is never retried.
            if group is None:
                result['error_type'] = 'OwnershipHandleUnavailable'
            else:
                try:
                    group.close()
                    result.update(cleanup_confirmed=bool(group.closed), exit_code=group.process.poll())
                except BaseException as error:
                    result['error_type'] = type(error).__name__
            return result
        return self._settled[role]

    def _wait_file(self, path, *, group, seconds=5):
        deadline = time.monotonic() + seconds
        while not path.exists():
            if group.process.poll() is not None: raise RuntimeError('owned fixture process exited before its identity file')
            if time.monotonic() >= deadline: raise TimeoutError('owned fixture file was not produced')
            time.sleep(.01)

    def _observe_tool(self):
        group = self._launch((str(self.binary), '--version'), 'version', self.binary.parent)
        try:
            code = group.process.wait(timeout=15)
        finally:
            settled = self._settle('version')
        if not settled['cleanup_confirmed']:
            raise RuntimeError('selected Renode version process cleanup is unconfirmed')
        for stream in self._streams: stream.flush()
        output = self.paths.log('version', 'stdout').read_bytes()
        if code != 0 or b'Renode' not in output:
            raise RuntimeError('selected Renode --version failed; inspect private owned version logs')
        self.tool = {'path': str(self.binary), 'sha256': _sha(self.binary), 'bytes': self.binary.stat().st_size,
            'version_stdout': output.decode('utf-8', errors='replace'), 'version_exit_code': code,
            'identity_scope': 'observed_file_and_version_output_not_execution_attestation'}

    def __enter__(self):
        from tinystudio.lab.attempt_control import AttemptControl, AttemptIdentity
        from tinystudio.lab.managed_emulator import ManagedEmulatorProcess
        from tinystudio.lab.managed_process_configuration import ManagedProcessPaths, ManagedProcessSelection, ReadinessSelection
        try:
            self.provenance = prepare_fixture(self.paths)
            self._observe_tool()
            self.bridge = self._launch((sys.executable, '-I', str(self.paths.helper('bridge')),
                str(self.paths.pty), str(self.paths.bridge_identity)), 'bridge')
            self._wait_file(self.paths.bridge_identity, group=self.bridge)
            self.bridge_identity = json.loads(self.paths.bridge_identity.read_text())
            if self.bridge_identity['pid'] != self.bridge.process.pid or self.bridge_identity['address'][0] != '127.0.0.1':
                raise RuntimeError('owned bridge identity did not match its process and loopback bind')
            self.control = AttemptControl(AttemptIdentity('http://127.0.0.1', 'owned-renode-fixture',
                self.paths.root.name, 'owned-target', _sha(self.paths.script)))
            self.control.renew(120, sent_at=time.monotonic())
            selection = ManagedProcessSelection((str(self.binary), '--disable-gui', '--plain', '--port', '-1',
                '--pid-file', str(self.paths.pid), '--config', str(self.paths.config),
                '--execute', 'include ' + _monitor_path(self.paths.script)),
                self.binary.parent, ReadinessSelection('tcp', host='127.0.0.1', port=self.bridge_identity['address'][1]),
                startup_timeout_seconds=20, shutdown_grace_seconds=.5, max_log_bytes=None, raw_log_visibility=None)
            self.managed = ManagedEmulatorProcess(selection, self.control, ManagedProcessPaths(self.paths.managed),
                base_env=self._environment())
            self.managed.start()
            self.ready = self.managed.wait_ready()
            self._wait_file(self.paths.pid, group=self.bridge)
            return self
        except BaseException as error:
            try: self.close(reason='startup_failed')
            except BaseException as cleanup:
                error.add_note('Owned fixture cleanup also failed: ' + type(cleanup).__name__)
            raise

    def exchange_all_bytes(self):
        if self.client is not None: raise RuntimeError('owned fixture client is single use')
        self.client = self._launch((sys.executable, '-I', str(self.paths.helper('client')),
            str(self.bridge_identity['address'][1]), str(self.paths.request), str(self.paths.response),
            str(self.paths.client_result)), 'client')
        if self.client.process.wait(timeout=20) != 0:
            raise RuntimeError('owned UART firmware client failed; inspect its private logs')
        self._application_passed = True
        return self.paths.response.read_bytes()

    def close(self, *, reason='completed'):
        if self._close_attempted:
            if self.receipt is None or not self.receipt['cleanup_confirmed']:
                raise RuntimeError('owned fixture cleanup is unconfirmed; it was not retried')
            return self.receipt
        self._close_attempted = True
        receipt = {'cleanup_confirmed': True, 'known_pids': [], 'artifacts': [], 'errors': []}
        pids = []
        for role in ('client', 'bridge', 'version'):
            if role in self._launch_attempts:
                group = self._groups.get(role)
                if group is not None:
                    pids.append((role, group.process.pid))
                    supervisor = getattr(group.process, 'owner', None)
                    if supervisor is not None:
                        pids.append((role + '_supervisor', supervisor.pid))
                result = self._settle(role)
                receipt[role] = dict(result)
                if not result['cleanup_confirmed']:
                    receipt['errors'].append({'phase': role + '_cleanup', 'type': result.get('error_type', 'CleanupUnconfirmed')})
        if self.managed is not None:
            try:
                selected_reason = 'cancelled' if self.control.snapshot()['revoked'] else reason
                receipt['managed'] = self.managed.close(selected_reason)
                receipt['artifacts'] = self.managed.retain_artifacts(self.paths.retained)
            except BaseException as error:
                receipt['errors'].append({'phase': 'managed_cleanup', 'type': type(error).__name__})
                receipt['managed'] = self.managed.snapshot()
            snapshot = receipt['managed']
            if snapshot['pid'] is not None: pids.append(('renode', snapshot['pid']))
            pids.extend(('readiness_helper', item['pid']) for item in snapshot['helpers'] if item['pid'] is not None)
        for stream in self._streams:
            try: stream.close()
            except BaseException as error:
                receipt['errors'].append({'phase': 'private_log_close', 'type': type(error).__name__})
        for role, pid in pids:
            absent = False
            try: os.kill(pid, 0)
            except ProcessLookupError: absent = True
            except PermissionError: pass
            receipt['known_pids'].append({'role': role, 'pid': pid, 'absent_at_observation': absent})
        receipt['loopback_port_closed'] = True
        if self.bridge_identity is not None:
            with socket.socket() as probe:
                probe.settimeout(.3)
                receipt['loopback_port_closed'] = probe.connect_ex(tuple(self.bridge_identity['address'])) != 0
        receipt['cleanup_confirmed'] = (not receipt['errors'] and receipt['loopback_port_closed']
            and all(item['absent_at_observation'] for item in receipt['known_pids'])
            and all(receipt.get(role, {}).get('cleanup_confirmed', True) for role in ('client', 'bridge', 'version', 'managed')))
        self.receipt = receipt
        if self.provenance is not None:
            # Store a compact qualification record, not a dump of every probe.
            summary = {key: value for key, value in receipt.items() if key != 'managed'}
            if 'managed' in receipt:
                summary['managed'] = {key: receipt['managed'][key] for key in
                    ('pid', 'exit_code', 'cleanup_confirmed', 'readiness', 'logs', 'evidence', 'qualification')}
                summary['readiness_helper_count'] = len(receipt['managed']['helpers'])
            _json(self.paths.result, {'scope': {'physical_device': False, 'model_fidelity': False,
                'hard_real_time': False, 'integrated_runner_server_lease': False},
                'application_predicate_passed': self._application_passed, 'tool': self.tool,
                'source_provenance': self.provenance, 'cleanup': summary})
        if not receipt['cleanup_confirmed']:
            raise RuntimeError('owned fixture cleanup is unconfirmed; inspect its private result')
        return receipt

    def __exit__(self, kind, error, traceback):
        try: self.close(reason='closed' if error is not None else 'completed')
        except BaseException as cleanup:
            if error is None: raise
            error.add_note('Owned fixture cleanup also failed: ' + type(cleanup).__name__)
        return False
