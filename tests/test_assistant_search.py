"""Native search requests/events are forwarded without alternate models or invented citations."""
from types import SimpleNamespace as NS
from tinystudio.integrations import assistant


def test_openai_native_search_events_and_real_citations(monkeypatch):
    import openai
    captured=[]
    class Stream:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def __iter__(self):
            yield NS(type='response.web_search_call.searching',item_id='search1')
            yield NS(type='response.output_text.delta',delta='Documented answer')
        def get_final_response(self):
            return NS(usage=None,output=[NS(type='web_search_call',id='search1',status='completed',action=NS(query='actual query',sources=[NS(type='url',url='https://example.org/returned-search-source')])),NS(type='message',content=[NS(annotations=[NS(type='url_citation',url='https://example.org/source',title='Actual source'),NS(type='url_citation',url='javascript:alert(1)',title='Unsafe')])])])
    def stream(**kwargs):captured.append(kwargs);return Stream()
    monkeypatch.setattr(openai,'OpenAI',lambda **kwargs:NS(responses=NS(stream=stream)))
    monkeypatch.setattr(assistant,'openai_available',lambda:True)
    monkeypatch.setattr(assistant,'environment_value',lambda key:'unit-test-placeholder')
    monkeypatch.setattr(assistant,'_turn_tools',lambda *args:[])
    events=list(assistant.run_openai(NS(),[{'role':'user','content':'search'}],model='unchanged-model-id',web_search=True))
    assert len(captured)==1 and captured[0]['model']=='unchanged-model-id'
    assert {'type':'web_search'} in captured[0]['tools']
    assert captured[0]['include']==['web_search_call.action.sources']
    assert [e['state'] for e in events if e['type']=='search']==['searching','completed']
    assert [e['url'] for e in events if e['type']=='source']==['https://example.org/returned-search-source','https://example.org/source']


def test_anthropic_native_tool_preserves_selected_model_and_reports_search_failure(monkeypatch):
    captured=[]
    class Stream:
        def __iter__(self):
            yield NS(type='content_block_start',content_block=NS(type='server_tool_use',name='web_search',id='s1'))
        def get_final_message(self):
            return NS(usage=NS(input_tokens=1,output_tokens=1),stop_reason='end_turn',content=[NS(type='server_tool_use',name='web_search',id='s1',input={'query':'actual query'}),NS(type='web_search_tool_result',tool_use_id='s1',content=NS(error_code='unavailable'))])
    def runner(**kwargs):captured.append(kwargs);return iter([Stream()])
    monkeypatch.setattr(assistant,'anthropic_client',lambda:NS(beta=NS(messages=NS(tool_runner=runner))))
    monkeypatch.setattr(assistant,'anthropic_available',lambda:True)
    monkeypatch.setattr(assistant,'_turn_tools',lambda *args:[])
    events=list(assistant.run_anthropic(NS(),[{'role':'user','content':'search'}],model='claude-opus-5-5',web_search=True))
    assert len(captured)==1 and captured[0]['model']=='claude-opus-5-5'
    assert {'type':'web_search_20250305','name':'web_search','max_uses':5} in captured[0]['tools']
    assert 'fallbacks' not in captured[0]
    assert any(e['type']=='search' and e['state']=='failed' and e['error']=='unavailable' for e in events)
    assert not any(e['type']=='source' for e in events)
