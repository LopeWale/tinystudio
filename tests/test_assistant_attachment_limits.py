"""Hostile documents are bounded before expansion; extraction never owns the ASGI loop."""
import asyncio
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import zipfile

from fastapi import FastAPI
import httpx
import pytest

from tinystudio.api.deps import current_user, get_db, project_dep
from tinystudio.api.routes import assistant_attachments as routes
from tinystudio.services import assistant_attachments as attachments

spec=importlib.util.spec_from_file_location('bounded_attachment_worker',Path(attachments.__file__).with_name('attachment_extract_worker.py'))
worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)


def test_office_archive_expansion_is_rejected_before_reading_members(tmp_path,monkeypatch):
    path=tmp_path/'bomb.docx'
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('word/document.xml',b'a'*(worker.MAX_EXPANDED_BYTES+1))
    assert path.stat().st_size<attachments.MAX_BYTES
    def unexpected(*args,**kwargs):raise AssertionError('Oversized archive must not expand a member')
    monkeypatch.setattr(zipfile.ZipFile,'open',unexpected)
    with pytest.raises(ValueError,match='expanded document'):
        worker._office(path)


def pdf_with_content(path,expanded):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject,NameObject
    writer=PdfWriter();page=writer.add_blank_page(width=72,height=72)
    stream=DecodedStreamObject();stream.set_data(b' '*expanded)
    page[NameObject('/Contents')]=writer._add_object(stream.flate_encode())
    writer.write(path)


def test_compressed_pdf_stream_is_capped_before_page_text_parsing(tmp_path,monkeypatch):
    import pypdf
    previous=pypdf.get_configuration()
    path=tmp_path/'expanded.pdf';pdf_with_content(path,worker.MAX_PDF_STREAM_BYTES+1)
    assert path.stat().st_size<10000
    def unexpected(*args,**kwargs):raise AssertionError('Page text parser must not run on oversized content')
    monkeypatch.setattr(pypdf._page.PageObject,'extract_text',unexpected)
    try:
        with pytest.raises(Exception,match='Limit reached|limit'):
            worker._pdf(path)
    finally:pypdf.overwrite_configuration(previous)


def test_real_isolated_parser_rejects_compressed_pdf_resource_excess(tmp_path):
    path=tmp_path/'expanded.pdf';pdf_with_content(path,worker.MAX_PDF_STREAM_BYTES+1)
    with pytest.raises(ValueError,match='extraction failed.*resource limits'):
        attachments.bounded_extract(path)


def test_page_count_and_total_text_have_independent_bounds(tmp_path):
    from pypdf import PdfWriter
    path=tmp_path/'too-many.pdf';writer=PdfWriter()
    for _ in range(worker.MAX_PAGES+1):writer.add_blank_page(width=72,height=72)
    writer.write(path)
    with pytest.raises(ValueError,match='100-page'):
        attachments.bounded_extract(path)
    text=tmp_path/'too-long.txt';text.write_text('a'*(worker.MAX_TEXT_CHARS+1))
    with pytest.raises(ValueError,match='text exceeds'):
        attachments.bounded_extract(text)


def test_wall_timeout_kills_and_reaps_real_parser_child(tmp_path,monkeypatch):
    path=tmp_path/'source.txt';path.write_text('safe fixture')
    original=subprocess.run;pids=[]
    def slow(*args,**kwargs):
        try:
            return original([sys.executable,'-I','-c','import os,time;print(os.getpid(),flush=True);time.sleep(30)'],**kwargs)
        except subprocess.TimeoutExpired as error:
            pids.append(int(error.output.strip()));raise
    monkeypatch.setattr(attachments.subprocess,'run',slow)
    monkeypatch.setattr(attachments,'EXTRACTION_TIMEOUT_SECONDS',.1)
    with pytest.raises(ValueError,match='time limit'):
        attachments.bounded_extract(path)
    assert len(pids)==1
    with pytest.raises(ProcessLookupError):os.kill(pids[0],0)


def test_real_child_memory_guard_terminates_oversized_allocation():
    script=f'''import importlib.util,time
spec=importlib.util.spec_from_file_location("worker",{str(Path(worker.__file__))!r})
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
m.MAX_MEMORY=32*1024*1024
m.restrict_resources()
block=bytearray(96*1024*1024)
time.sleep(.15)
print("memory-guard-did-not-enforce")
'''
    result=subprocess.run([sys.executable,'-I','-c',script],capture_output=True,timeout=3)
    assert result.returncode!=0
    assert b'memory-guard-did-not-enforce' not in result.stdout
    assert result.returncode==73 or b'MemoryError' in result.stderr


def test_extraction_parallelism_is_bounded_and_slot_recovers(tmp_path,monkeypatch):
    path=tmp_path/'source.txt';path.write_text('fixture')
    semaphore=threading.BoundedSemaphore(1)
    assert semaphore.acquire(blocking=False)
    monkeypatch.setattr(attachments,'_EXTRACTORS',semaphore)
    with pytest.raises(attachments.ExtractionBusy,match='busy'):
        attachments.bounded_extract(path)
    semaphore.release()
    assert attachments.bounded_extract(path)==('fixture',7)


def test_slow_upload_extraction_leaves_same_asgi_event_loop_responsive(monkeypatch):
    entered,release=threading.Event(),threading.Event();threads=[]
    def slow_create(*args):
        threads.append(threading.get_ident());entered.set()
        if not release.wait(3):raise RuntimeError('fixture extraction was not released')
        return {'id':'fixture'}
    monkeypatch.setattr(attachments,'create',slow_create)
    app=FastAPI();app.include_router(routes.router)
    app.dependency_overrides[current_user]=lambda:SimpleNamespace(user=SimpleNamespace(id='u'))
    app.dependency_overrides[project_dep]=lambda:SimpleNamespace(id='p')
    app.dependency_overrides[get_db]=lambda:object()
    @app.get('/responsive')
    async def responsive():return {'ok':True}
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://fixture') as client:
            task=asyncio.create_task(client.post('/api/p/p/assistant/attachments',files={'file':('safe.txt',b'fixture','text/plain')}))
            try:
                assert await asyncio.to_thread(entered.wait,2)
                assert not task.done(), 'Extraction must still be blocked while the event loop handles another request'
                answer=await asyncio.wait_for(client.get('/responsive'),timeout=.5)
                assert answer.json()=={'ok':True}
                assert threads[0]!=threading.get_ident()
            finally:release.set()
            assert (await task).status_code==200
    asyncio.run(exercise())


def test_normal_pdf_office_and_html_remain_readable_with_resource_limits(tmp_path):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject,DictionaryObject,NameObject
    path=tmp_path/'normal.pdf';writer=PdfWriter();page=writer.add_blank_page(width=100,height=100)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    stream=DecodedStreamObject();stream.set_data(b'BT /F1 12 Tf 10 10 Td (Bounded PDF evidence) Tj ET')
    page[NameObject('/Contents')]=writer._add_object(stream.flate_encode());writer.write(path)
    assert attachments.bounded_extract(path)==('Bounded PDF evidence',20)
    for suffix,member,xml in [('.docx','word/document.xml','<w:document xmlns:w="urn:w"><w:p><w:r><w:t>Bounded </w:t></w:r><w:r><w:t>Word evidence</w:t></w:r></w:p></w:document>'),('.pptx','ppt/slides/slide1.xml','<a:root xmlns:a="urn:a"><a:p><a:r><a:t>Bounded slide evidence</a:t></a:r></a:p></a:root>')]:
        document=tmp_path/('normal'+suffix)
        with zipfile.ZipFile(document,'w',compression=zipfile.ZIP_DEFLATED) as archive:archive.writestr(member,xml)
        expected='Bounded Word evidence' if suffix=='.docx' else 'Bounded slide evidence'
        assert attachments.bounded_extract(document)==(expected,len(expected))
    markup=tmp_path/'normal.html';markup.write_text('<p>First paragraph</p><p>Second paragraph</p><script>ignored()</script>')
    text,count=attachments.bounded_extract(markup)
    assert 'First paragraph' in text and 'Second paragraph' in text and 'ignored' not in text
    assert count==len(text)
