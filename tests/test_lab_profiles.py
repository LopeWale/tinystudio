"""Isolation plans are structural evidence; denial fixtures are runtime evidence."""
from dataclasses import replace
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys

import pytest

from tinystudio.lab.profiles import Limits, ProfileRequest, RunnerGrants, ProfileExecution, build_profile

PINNED='python@sha256:'+'a'*64


@pytest.fixture
def request_(tmp_path):
    root=tmp_path/'attempt';root.mkdir()
    source=root/'runtime/tinystudio';source.mkdir(parents=True)
    inputs=root/'inputs';inputs.mkdir()
    output=root/'output';output.mkdir()
    return ProfileRequest(backend='native',attempt_id='attempt-1',attempt_root=root,source_root=source,inputs_root=inputs,output_root=output,
                          command=(sys.executable,'-c','print("allowed")'),expected_os='Darwin',runtime_read_paths=(Path(sys.prefix),Path(sys.base_prefix)))


def container(request_):
    return replace(request_,backend='container',image=PINNED,expected_os='Linux',runtime_read_paths=(),command=('/usr/local/bin/python','-c','print("hello")'))


def kube(request_,tmp_path):
    config=tmp_path/'owned-kubeconfig';config.write_text('# fixture only, no credentials')
    return replace(container(request_),backend='kubernetes',namespace='owned-lab',kube_context='owned-fixture-context',kubeconfig=config,storage_claim='owned-attempt-pvc')


def test_native_plan_denies_by_default_and_reports_unenforced_resources(request_):
    plan=build_profile(request_,system='Darwin')
    assert '(deny default)' in plan.profile_text
    assert '(allow network' not in plan.profile_text
    assert f'(allow file-write* (subpath "{request_.output_root}"))' in plan.profile_text
    assert plan.manifest()['qualification']=='unqualified_until_denial_fixture'
    assert any('CPU/memory/PID' in value for value in plan.limitations)
    with pytest.raises(ValueError,match='cannot enforce'):
        build_profile(replace(request_,require_hard_resources=True),system='Darwin')
    with pytest.raises(ValueError,match='matching macOS'):
        build_profile(request_,system='Linux')
    manifest=plan.manifest();manifest['limits']['cpu']=200
    assert plan.request.limits.cpu==1


def test_native_unavailable_has_no_unrestricted_fallback(request_,monkeypatch):
    monkeypatch.setattr(shutil,'which',lambda _:None)
    with pytest.raises(ValueError,match='fallback is prohibited'):
        build_profile(request_,system='Darwin')


def test_authored_grants_cannot_be_passed_as_profile_authority(request_):
    with pytest.raises(ValueError,match='runner-owned'):
        build_profile(request_,{'network':True})
    with pytest.raises(ValueError,match='exact IP'):
        build_profile(request_,RunnerGrants(network_endpoints=(('127.0.0.1',0),)))


def test_docker_is_digest_pinned_readonly_bounded_and_credential_free(request_):
    plan=build_profile(container(request_))
    args=list(plan.argv)
    assert '--read-only' in args and '--cap-drop' in args and args[args.index('--cap-drop')+1]=='ALL'
    assert args[args.index('--network')+1]=='none'
    assert args[args.index('--pull=never')]=='--pull=never'
    assert args[args.index('--pids-limit')+1]=='64'
    assert args[args.index('--memory')+1]==str(512*1024**2)
    assert args[args.index('--memory-swap')+1]==str(512*1024**2)
    assert f'type=bind,src={request_.attempt_root},dst={request_.attempt_root},readonly' in args
    assert PINNED in args and '--privileged' not in args
    assert 'no-new-privileges=true' in args
    assert '--env-file' not in args
    logging = [args[index+1] for index, value in enumerate(args) if value == '--log-opt']
    # Docker 29 rejects its default rotated-file compression with one file.
    # Keep the 16 MiB single-file bound explicit and independently runnable.
    assert args[args.index('--log-driver')+1] == 'local'
    assert set(logging) == {'max-size=16m', 'max-file=1', 'compress=false'}
    with pytest.raises(ValueError,match='image@sha256'):
        build_profile(replace(container(request_),image='python:latest'))
    with pytest.raises(ValueError,match='Linux runtime'):
        build_profile(replace(container(request_),expected_os='Darwin'))
    with pytest.raises(ValueError,match='endpoint ACLs'):
        build_profile(container(request_),RunnerGrants(network_endpoints=(('127.0.0.1',80),)))
    with pytest.raises(ValueError,match='operator-managed'):
        build_profile(container(request_),RunnerGrants(docker_network='host'))


def test_kubernetes_security_context_resources_and_default_deny_policy(request_,tmp_path):
    request=kube(request_,tmp_path)
    plan=build_profile(request)
    policy,job=json.loads(plan.manifests_json)['items']
    pod=job['spec']['template']['spec'];runtime=pod['containers'][0]
    assert pod['automountServiceAccountToken'] is False and pod['hostNetwork'] is False
    assert pod['hostPID'] is False and pod['hostIPC'] is False
    assert pod['securityContext']['runAsNonRoot'] is True
    assert runtime['securityContext']=={'allowPrivilegeEscalation':False,'readOnlyRootFilesystem':True,'capabilities':{'drop':['ALL']}}
    assert runtime['resources']['limits']['memory']==str(request.limits.memory_bytes)
    assert job['spec']['backoffLimit']==0 and job['spec']['activeDeadlineSeconds']==300
    assert policy['spec']['egress']==[] and policy['spec']['ingress']==[]
    assert all('hostPath' not in volume for volume in pod['volumes'])
    assert plan.required_attestations==('CNI NetworkPolicy enforcement',)
    with pytest.raises(RuntimeError,match='unqualified backend prerequisites'):
        ProfileExecution(plan,command_runner=lambda *_:pytest.fail('must not call cluster')).start()
    with pytest.raises(ValueError,match='PID limits'):
        build_profile(replace(request,require_hard_resources=True),RunnerGrants(network_policy_enforced=True))


def test_kubernetes_explicit_network_and_secrets_do_not_grant_service_tokens(request_,tmp_path):
    plan=build_profile(kube(request_,tmp_path),RunnerGrants(network_policy_enforced=True,network_endpoints=(('192.0.2.1',443),),kubernetes_secrets=('fixture-secret',)))
    policy,job=json.loads(plan.manifests_json)['items']
    assert policy['spec']['egress'][0]=={'to':[{'ipBlock':{'cidr':'192.0.2.1/32'}}],'ports':[{'protocol':'TCP','port':443}]}
    pod=job['spec']['template']['spec']
    assert pod['automountServiceAccountToken'] is False
    assert pod['volumes'][1]['secret']['secretName']=='fixture-secret'
    assert 'secretKeyRef' not in plan.manifests_json


class DockerFixture:
    def __init__(self,plan,*,foreign=False,uncertain=False):
        self.plan=plan;self.foreign=foreign;self.uncertain=uncertain;self.removed=False;self.calls=[]
        self.id='b'*64
    def __call__(self,args,**kwargs):
        self.calls.append(args);code=0;error='';text=''
        if args[1]=='create':text=self.id
        elif args[1]=='inspect':
            if self.removed:code=1;error='daemon unavailable' if self.uncertain else 'Error: No such object: '+self.id
            else:text=json.dumps([{'Id':self.id,'Config':{'Image':self.plan.request.image,'Labels':{'tinystudio.owner':'foreign' if self.foreign else self.plan.owner}},'State':{'Running':False,'ExitCode':0}}])
        elif args[1]=='rm':self.removed=True
        return subprocess.CompletedProcess(args,code,text,error)


def test_container_lifecycle_deletes_only_exact_owned_identity_and_checks_absence(request_):
    plan=build_profile(container(request_));fixture=DockerFixture(plan)
    owner=ProfileExecution(plan,command_runner=fixture).start()
    assert owner.poll()==0
    owner.close();assert owner.closed
    assert ['docker','stop','--time','10',fixture.id] in fixture.calls
    assert ['docker','rm','--force',fixture.id] in fixture.calls
    with pytest.raises(RuntimeError,match='already started or closed'):owner.start()


def test_foreign_identity_and_uncertain_backend_absence_never_qualify_cleanup(request_):
    plan=build_profile(container(request_));fixture=DockerFixture(plan,foreign=True)
    owner=ProfileExecution(plan,command_runner=fixture)
    with pytest.raises(RuntimeError,match='ownership'):owner.start()
    assert not any(args[1] in ('rm','stop') for args in fixture.calls)
    uncertain=DockerFixture(plan,uncertain=True);owner=ProfileExecution(plan,command_runner=uncertain).start()
    with pytest.raises(RuntimeError,match='unconfirmed'):owner.close()
    assert not owner.closed


def test_kubernetes_cleanup_uses_uid_preconditions_and_requires_node_evidence(request_,tmp_path):
    plan=build_profile(kube(request_,tmp_path),RunnerGrants(network_policy_enforced=True))
    calls=[]
    def command(args,**kwargs):
        calls.append((args,kwargs.get('input')))
        if 'create' in args:
            resource=json.loads(kwargs['input']);resource['metadata']['uid']='uid-'+resource['kind']
            text=json.dumps(resource)
        else:text='{}'
        return subprocess.CompletedProcess(args,0,text,'')
    owner=ProfileExecution(plan,command_runner=command).start()
    with pytest.raises(RuntimeError,match='node process termination'):owner.close()
    deletes=[json.loads(data) for args,data in calls if 'delete' in args]
    assert {row['preconditions']['uid'] for row in deletes}=={'uid-Job','uid-NetworkPolicy'}
    assert all(row['propagationPolicy']=='Foreground' for row in deletes)
    assert all('--context' in args and plan.request.kube_context in args for args,_ in calls)


def test_paths_cannot_overlap_or_escape_grants(request_):
    with pytest.raises(ValueError,match='must not overlap'):
        build_profile(replace(request_,output_root=request_.source_root),system='Darwin')
    with pytest.raises(ValueError,match='attempt-owned'):
        build_profile(replace(request_,inputs_root=request_.attempt_root.parent),system='Darwin')
    with pytest.raises(ValueError,match='memory'):
        build_profile(replace(request_,limits=Limits(memory_bytes=0)),system='Darwin')


@pytest.mark.skipif(platform.system()!='Darwin' or shutil.which('sandbox-exec') is None,reason='macOS sandbox-exec unavailable; isolation unqualified')
def test_actual_native_denial_and_explicit_read_write_grants(request_,tmp_path):
    allowed=request_.inputs_root/'allowed.txt';allowed.write_text('allowed fixture')
    denied=tmp_path/'ungranted.txt';denied.write_text('task-local ungranted fixture')
    outside=tmp_path/'outside.txt'
    script=request_.source_root/'denials.py'
    script.write_text('''import json,pathlib,socket
result={}
result['read_granted']=pathlib.Path(%r).read_text()=='allowed fixture'
try:pathlib.Path(%r).read_text();result['read_denied']=False
except PermissionError:result['read_denied']=True
try:pathlib.Path(%r).write_text('must not appear');result['write_denied']=False
except PermissionError:result['write_denied']=True
try:
 s=socket.socket();s.settimeout(.2);s.connect(('127.0.0.1',%d));result['network_denied']=False
except PermissionError:result['network_denied']=True
except OSError as error:result['network_denied']='inconclusive:'+str(error.errno)
pathlib.Path(%r).write_text('permitted output')
print(json.dumps(result))
'''%(str(allowed),str(denied),str(outside),9,str(request_.output_root/'allowed-output.txt')))
    plan=build_profile(replace(request_,command=(sys.executable,str(script))),system='Darwin')
    result=subprocess.run(plan.argv,capture_output=True,text=True,env={**dict(plan.environment),'PATH':os.environ.get('PATH','')},timeout=5,cwd=request_.output_root)
    if 'sandbox_init: Operation not permitted' in result.stderr or 'sandbox_apply: Operation not permitted' in result.stderr:
        pytest.skip('outer execution sandbox denies nested sandbox-exec; no isolation qualification occurred')
    assert result.returncode==0,result.stderr
    evidence=json.loads(result.stdout)
    assert evidence=={'read_granted':True,'read_denied':True,'write_denied':True,'network_denied':True}
    assert not outside.exists()
    assert (request_.output_root/'allowed-output.txt').read_text()=='permitted output'


def test_docker_runtime_qualification_is_explicitly_not_claimed():
    pytest.skip('Docker daemon unavailable to this task; structural plan and lifecycle fixtures are not provider qualification')


def test_kubernetes_runtime_qualification_is_explicitly_not_claimed():
    pytest.skip('No explicitly authorized test cluster; no provider API calls made, enforcement unqualified')


@pytest.mark.skipif(platform.system()!='Darwin' or shutil.which('sandbox-exec') is None,reason='macOS sandbox-exec unavailable; grants unqualified')
def test_actual_native_trusted_grants_are_narrow_and_network_has_a_live_control(request_,tmp_path):
    fixture=tmp_path/'operator-granted.txt';fixture.write_text('operator-owned fixture')
    sibling=tmp_path/'still-ungranted.txt';sibling.write_text('must remain denied')
    script=request_.source_root/'grant.py'
    with socket.socket() as listener:
        try:listener.bind(('127.0.0.1',0))
        except PermissionError:pytest.skip('outer sandbox denies owned loopback listener; grants unqualified')
        listener.listen(4);port=listener.getsockname()[1]
        with socket.create_connection(('127.0.0.1',port),timeout=1):pass
        script.write_text('''import socket,pathlib,json
result={}
result['read_granted']=pathlib.Path(%r).read_text()=='operator-owned fixture'
try:pathlib.Path(%r).read_text();result['sibling_denied']=False
except PermissionError:result['sibling_denied']=True
with socket.create_connection(('127.0.0.1',%d),timeout=1):result['network_granted']=True
print(json.dumps(result))
'''%(str(fixture),str(sibling),port))
        plan=build_profile(replace(request_,command=(sys.executable,str(script))),RunnerGrants(read_secrets=(fixture,),network_endpoints=(('127.0.0.1',port),)))
        result=subprocess.run(plan.argv,capture_output=True,text=True,env={**dict(plan.environment),'PATH':os.environ.get('PATH','')},timeout=5,cwd=request_.output_root)
        if 'sandbox_apply: Operation not permitted' in result.stderr or 'sandbox_init: Operation not permitted' in result.stderr:
            pytest.skip('outer sandbox denies nested sandbox-exec; no grant qualification occurred')
        assert result.returncode==0,result.stderr
        assert json.loads(result.stdout)=={'read_granted':True,'sibling_denied':True,'network_granted':True}


def test_uncertain_container_creation_reconciles_only_owner_label_and_cleans_up(request_):
    plan=build_profile(container(request_));fixture=DockerFixture(plan)
    def command(args,**kwargs):
        if args[1]=='create':raise subprocess.TimeoutExpired(args,30)
        return fixture(args,**kwargs)
    owner=ProfileExecution(plan,command_runner=command)
    with pytest.raises(subprocess.TimeoutExpired):owner.start()
    assert owner.closed
    assert ['docker','inspect',plan.name] in fixture.calls
    assert ['docker','rm','--force',fixture.id] in fixture.calls


def test_native_rejects_network_grants_it_cannot_enforce(request_):
    with pytest.raises(ValueError,match='localhost TCP only'):
        build_profile(request_,RunnerGrants(network_endpoints=(('192.0.2.1',443),)))
