"""Live acceptance with an isolated disposable project and real MCP, R2, and worker."""
from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).absolute().parents[1]
sys.path.insert(0, str(ROOT))
import tinystudio  # loads configured credentials without printing them
import httpx
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from tinystudio.config import config
from tinystudio.integrations.tools import Studio


def wait_task(studio, task):
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        result = studio.call('GET', '/api/tasks/' + task['id'])
        if result['state'] in ('succeeded', 'failed', 'cancelled'):
            if result['state'] != 'succeeded':
                raise RuntimeError('worker failed: ' + str(result.get('error')))
            return result
        time.sleep(2)
    raise RuntimeError('worker did not finish in five minutes')


async def mcp_import(studio, path):
    params = StdioServerParameters(command=str(ROOT / '.venv/bin/tinystudio'), args=['mcp','--url',studio.base_url,'--project',studio.project], env=dict(os.environ), cwd=str(ROOT))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            for name, args in [('create_dataset_from_jsonl', {'name':'cloud-proof','path':str(path)}), ('list_datasets', {})]:
                result = await client.call_tool(name, args)
                if result.is_error:
                    raise RuntimeError(f'MCP {name} failed: {result.content}')
            print('PASS real MCP initialization, direct R2 upload/import, tools/call', flush=True)


def main():
    public = os.environ['TINYSTUDIO_PUBLIC_URL']
    studio = Studio(public, token=os.environ['TINYSTUDIO_API_KEY'])
    edge = {k: v for k,v in studio.http.headers.items() if k.lower().startswith('cf-access-')}
    assert httpx.get(public+'/api/auth/me',timeout=30).status_code in (302,401,403)
    assert httpx.get(public+'/api/auth/me',headers={**edge,'CF-Access-Client-Secret':'invalid'},timeout=30).status_code in (302,401,403)
    assert httpx.get(public+'/api/auth/me',headers=edge,timeout=30).status_code == 401
    assert httpx.get('http://127.0.0.1:8000/api/auth/me',timeout=10).status_code == 401
    assert studio.call('GET','/api/auth/me')['via']=='api_key'
    print('PASS edge rejection, origin assertion enforcement, app API key enforcement',flush=True)
    project = studio.call('POST','/api/projects',json={'name':'Cloudflare Proof '+uuid.uuid4().hex[:8]})
    studio.http.close()
    studio = Studio(public, project=project['slug'], token=os.environ['TINYSTUDIO_API_KEY'])
    try:
        with tempfile.TemporaryDirectory(prefix='tinystudio-cloud-proof-') as tmp:
            root = Path(tmp)
            rows=[{'messages':[{'role':'user','content':f'Count {i}'},{'role':'assistant','content':str(i)}]} for i in range(16)]
            source=root/'data.jsonl';source.write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
            asyncio.run(mcp_import(studio,source))
            studio.call('POST','/api/datasets/cloud-proof/split',json={'train_pct':85})
            snap=studio.call('POST','/api/snapshots',json={'dataset':'cloud-proof','name':'frozen'})
            # Inspect the storage contract independently of HTTP status.
            from tinystudio.db import session
            from tinystudio.db.models import Snapshot
            from tinystudio.services.snapshots import snapshot_rows
            from tinystudio.storage import storage
            with session() as db:
                row=db.get(Snapshot,snap['id'])
                assert row.storage_prefix
                manifest=storage().read_manifest(row.storage_prefix)
                assert any(f['path'].endswith('.parquet') for f in manifest['files'])
                shutil.rmtree(config().project_dir(project['id'])/'snapshots',ignore_errors=True)
                train,val=snapshot_rows(db,row)
                assert len(train)+len(val)==16
                assert sorted(r['messages'][0]['content'] for r in train+val)==sorted(r['messages'][0]['content'] for r in rows)
            print('PASS immutable Parquet snapshot, independently checked R2 data and empty-cache read',flush=True)
            from transformers import AutoTokenizer,LlamaConfig,LlamaForCausalLM
            tok=AutoTokenizer.from_pretrained('HuggingFaceTB/SmolLM2-135M-Instruct',local_files_only=True)
            model=root/'tiny-model'
            cfg=LlamaConfig(vocab_size=len(tok),hidden_size=32,intermediate_size=48,num_hidden_layers=2,num_attention_heads=4,num_key_value_heads=2,max_position_embeddings=256,hidden_act='silu',tie_word_embeddings=True)
            LlamaForCausalLM(cfg).save_pretrained(model);tok.save_pretrained(model)
            studio.call('POST','/api/jobs',json={'name':'proof','snapshot':'frozen'})
            studio.call('POST','/api/jobs/proof/experiments',json={'experiments':[{'base_model':str(model),'training_type':'full','max_steps':2,'batch_size':2,'gradient_accumulation_steps':1,'max_length':64,'checkpoint_every_steps':1}]})
            wait_task(studio,studio.call('POST','/api/jobs/proof/start'))
            item=studio.call('GET','/api/jobs/proof')['experiments'][0]
            assert item['state']=='succeeded' and item['checkpoints']==[1,2]
            from tinystudio.db.models import Experiment
            from tinystudio.services.jobs import exp_path
            with session() as db:
                exp=db.get(Experiment,item['id']);directory=exp_path(exp)
                assert exp.status['artifact_prefix'] and len(exp.status['artifact_checkpoints'])==2
            shutil.rmtree(directory)
            studio.call('POST','/api/models/proof~1/up')
            result=studio.call('POST','/v1/chat/completions',json={'model':'proof/1','messages':[{'role':'user','content':'Count 1'}],'max_tokens':2})
            assert result['choices']
            studio.call('POST','/api/models/proof~1/down')
            print('PASS real worker training, step checkpoints, fresh-cache model restoration and inference',flush=True)
            wait_task(studio,studio.call('POST','/api/interp/captures',json={'model':'proof/1@1','snapshot':'frozen','layers':[0],'topk':5,'name':'proof-capture'}))
            shutil.rmtree(config().project_dir(project['id'])/'interp',ignore_errors=True)
            capture=studio.call('GET','/api/interp/captures/proof-capture')
            assert capture
            print('PASS checkpoint restoration, activation capture publication and empty-cache read',flush=True)
            response=studio.http.get(f'/api/p/{studio.project}/jobs/proof/experiments/1/download')
            assert response.status_code==303 and '.r2.cloudflarestorage.com/' in response.headers['location']
            with httpx.Client(timeout=60) as direct:
                downloaded=direct.get(response.headers['location'])
                assert downloaded.status_code==200 and downloaded.content[:2]==b'PK'
            print('PASS weights download redirects to private signed R2 object',flush=True)
    finally:
        studio.call('DELETE',f'/api/p/{studio.project}')
        print('Removed disposable project and its scoped R2 artifacts',flush=True)


if __name__=='__main__': main()
