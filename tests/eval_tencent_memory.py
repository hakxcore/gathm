"""Opt-in synthetic Tencent MemoryCore/Gathm retrieval benchmark.

Requires a prepared local Gathm embedding model, installed chat model, Node
22.16+, and dependencies in a pinned Tencent checkout. See the evaluation doc.
Runs localhost servers and stops only processes it starts. No cloud calls or
real conversation capture; no model downloads or npm install hooks.
"""
import argparse
import dataclasses
import http.server
import json
import os
from pathlib import Path
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from lib import harness, semantic_memory as semantic, llamacpp
from lib.llm import LLMProvider, LLMConfig
from eval_harness_recall import CASES, DISTRACTORS, HELD_OUT, UNRELATED

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core-dir', type=Path, required=True, help='Prepared Tencent checkout MemoryCore directory')
    parser.add_argument('--output-dir', type=Path, required=True, help='Benchmark JSON and logs directory')
    args = parser.parse_args()
    OUT = args.output_dir.expanduser().resolve(); OUT.mkdir(parents=True, exist_ok=True)
    CORE = args.core_dir.expanduser().resolve()
    if not (CORE/'src/gateway/server.ts').is_file() or not (CORE/'node_modules/tsx').is_dir():
        raise SystemExit('Prepare the pinned MemoryCore checkout and dependencies first; see docs/tencent-agent-memory-evaluation.md')
    FIXTURE = OUT / 'tencent-fixtures.json'
    report = {'source_commit': 'badbd2179d06f4e0e1f2133bd3482880beab8a02', 'synthetic_only': True, 'bm25_sparse_encoder_enabled': False, 'variants': {}}
    embedding_lock = threading.Lock()
    if not semantic.status()['prepared']:
        raise SystemExit('Prepare Gathm local embeddings first: gathm harness prepare')
    actual_commit = subprocess.check_output(['git', '-C', str(CORE), 'rev-parse', 'HEAD'], text=True).strip()
    if actual_commit != report['source_commit']:
        raise SystemExit('This comparison requires the pinned Tencent commit: '+report['source_commit'])
    report['node_version'] = subprocess.check_output(['node', '--version'], text=True).strip()
    report['python_version'] = sys.version.split()[0]
    report['platform'] = sys.platform
    report['fixture'] = {'approved_lessons': 50, 'positive_queries': 12, 'negative_queries': 5,
                         'unique_timing_queries': 60, 'api_candidate_limit': 3}
    encoder = semantic._load_encoder(semantic._manifest())
    model_cfg = llamacpp.LlamaCppConfig.from_env()
    report['chat_model_size_bytes'] = model_cfg.model.stat().st_size
    embedding_cache = semantic.cache_directory()
    children = []
    model_started = False
    records = []
    for i, (trigger, guidance, _) in enumerate(CASES):
        records.append({'id': f'case-{i}', 'content': trigger+'\n'+guidance})
    for i, topic in enumerate(DISTRACTORS):
        records.append({'id': f'distractor-{i}', 'content': 'When discussing '+topic+'\nUse a concrete practical example when explaining '+topic+'.'})
    for row, vector in zip(records, encoder.passage_embed([row['content'] for row in records])):
        row['vector'] = [float(value) for value in vector]
    FIXTURE.write_text(json.dumps({'records': records}))

    def free_port():
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            return sock.getsockname()[1]

    def stats(values):
        ordered = sorted(values)
        return {'median_ms': round(statistics.median(ordered), 3),
                'p95_ms': round(ordered[int((len(ordered)-1)*.95)], 3), 'max_ms': round(max(ordered), 3)}

    def timed(fn):
        start = time.perf_counter()
        result = fn()
        return result, (time.perf_counter()-start)*1000

    class EmbeddingAPI(http.server.BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            text = data['input']
            with embedding_lock:
                vector = next(iter(encoder.query_embed(text)))
            payload = json.dumps({'data': [{'index':0, 'embedding': [float(v) for v in vector]}], 'usage':{'total_tokens':0}}).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        def log_message(self, *args):
            pass

    def request(port, query, route='/v3/atomic/search'):
        body={'query':query,'limit':3,'session_key':'benchmark'}
        headers={'Content-Type':'application/json','Authorization':'Bearer gathm-benchmark',
                 'x-tdai-service-id':'default','x-tdai-team-id':'gathm','x-tdai-user-id':'benchmark','x-tdai-agent-id':'assistant'}
        req=urllib.request.Request(f'http://127.0.0.1:{port}{route}', data=json.dumps(body).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=20) as response:
            data=json.load(response)
        if data.get('code', 0) != 0:
            raise RuntimeError(str(data)[:600])
        return data

    def benchmark(fn):
        def result_ids(result):
            if isinstance(result, str):
                return {row['id'] for row in json.loads(result.rsplit('\n', 1)[1])} if result else set()
            return {row['id'] for row in result.get('data', {}).get('items', [])}
        cases=[]
        for i,q in enumerate(HELD_OUT):
            result,duration=timed(lambda:fn(q))
            cases.append({'query':q,'expected':f'case-{i}','hit':f'case-{i}' in result_ids(result),'latency_ms':round(duration,3),'result':result})
        unrelated=[]
        for q in UNRELATED:
            result,duration=timed(lambda:fn(q))
            if isinstance(result,str):
                hits=bool(result)
            else:
                hits=bool(result.get('data',{}).get('items',[]))
            unrelated.append({'query':q,'returned_memory':hits,'result':result})
        values=[]
        for i in range(60):
            _,duration=timed(lambda:fn(HELD_OUT[i%len(HELD_OUT)]+f' Example {i}.'))
            values.append(duration)
        return {'timing':stats(values),'cases':cases,'hits':sum(row['hit'] for row in cases),
                'unrelated':unrelated,'unrelated_return_count':sum(row['returned_memory'] for row in unrelated)}

    with tempfile.TemporaryDirectory(prefix='gathm-tencent-benchmark-') as scratch, patch.dict(os.environ, {
        'GATHM_HARNESS_DIR':str(Path(scratch)/'gathm'), 'GATHM_HARNESS_DISABLED':'0',
        'GATHM_HARNESS_SEMANTIC':'1','GATHM_HARNESS_EMBEDDING_DIR':str(embedding_cache),
        'GATHM_CHAT_OWNER':'latency-benchmark','GATHM_CONFIG_DIR':scratch,
    }):
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),EmbeddingAPI)
        server.daemon_threads=True
        threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            store=harness.Harness()
            ids=[]
            for trigger,guidance,q in CASES:
                lesson=store.teach(trigger,guidance);store.review(lesson,'approve');ids.append(lesson)
            for topic in DISTRACTORS:
                lesson=store.teach('When discussing '+topic,'Use a concrete practical example when explaining '+topic+'.')
                store.review(lesson,'approve')
            print('Benchmarking current Gathm',flush=True)
            report['gathm_cold_ms']=timed(lambda:store.recall('Python recursion'))[1]
            deadline = time.monotonic() + 75
            while semantic._worker and semantic._worker.busy and time.monotonic() < deadline:
                time.sleep(.01)
            indexed = semantic.index(store)
            if indexed.get('status') != 'indexed':
                raise RuntimeError('Gathm semantic baseline was not indexed: '+str(indexed))
            def current(query):
                context=store.recall(query)
                for i,lesson in enumerate(ids):context=context.replace(lesson,f'case-{i}')
                return context
            report['variants']['gathm']=benchmark(current)
            runners={}
            for variant in ('same-bge','default-fts'):
                print('Starting actual Tencent gateway: '+variant,flush=True)
                port=free_port()
                data_dir=Path(scratch)/variant;data_dir.mkdir()
                config={
                    'deployMode':'standalone','stateBackend':'local','instanceId':'default',
                    'server':{'host':'127.0.0.1','port':port,'apiKey':'gathm-benchmark'},
                    'data':{'baseDir':str(data_dir)},
                    'llm':{'baseUrl':'http://127.0.0.1:1/v1','apiKey':'synthetic','model':'unused'},
                    'memory':{'capture':{'enabled':False},'extraction':{'enabled':False},
                              'pipeline':{'enableWarmup':False},'storeBackend':'sqlite',
                              'embedding':({'provider':'openai','baseUrl':f'http://127.0.0.1:{server.server_port}/v1',
                                            'apiKey':'synthetic','model':semantic.MODEL,'dimensions':384,'sendDimensions':False,
                                            'timeoutMs':5000} if variant=='same-bge' else
                                           {'provider':'none'}),
                              'bm25':{'enabled':False,'language':'en'},
                              'recall':{'timeoutMs':25,'maxResults':3,'maxTotalRecallChars':2400}},
                    'observability':{'otel':{'enabled':False},'kafka':{'enabled':False},'clickhouse':{'enabled':False},'langfuse':{'enabled':False}},
                }
                cfg_path=data_dir/'gateway.json';cfg_path.write_text(json.dumps(config))
                env={key:os.environ[key] for key in ('PATH','HOME','TMPDIR','LANG') if key in os.environ}
                env.update({'TDAI_GATEWAY_CONFIG':str(cfg_path),'TDAI_METADATA_SQLITE_BASE_DIR':str(data_dir/'metadata'),
                            'NODE_ENV':'production', 'OTEL_SDK_DISABLED':'true'})
                log=(OUT/('tencent-'+variant+'.log')).open('w')
                launch_start=time.monotonic()
                proc=subprocess.Popen(['node','--expose-gc','--max-old-space-size=1536','--import','tsx',str(ROOT/'tests/tencent_memory_runner.mts'),str(CORE),str(FIXTURE)],cwd=CORE,env=env,
                                      stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                children.append((proc,log))
                deadline=time.monotonic()+300
                while not (data_dir/'benchmark-ready.json').exists():
                    if proc.poll() is not None:raise RuntimeError('Tencent '+variant+' exited; inspect log')
                    if time.monotonic()>deadline:raise RuntimeError('Tencent startup deadline exceeded')
                    time.sleep(.2)
                metadata=json.loads((data_dir/'benchmark-ready.json').read_text())
                metadata['launch_to_ready_ms']=round((time.monotonic()-launch_start)*1000,3)
                print('Gateway ready: '+variant+' '+str(metadata),flush=True)
                runners[variant]=(port,lambda q,p=port:request(p,q))
                report['variants'][variant]=benchmark(runners[variant][1])
                report['variants'][variant]['runtime']=metadata
                report['variants'][variant]['auto_recall_examples']=[request(port,q,'/recall') for q in (HELD_OUT[0],UNRELATED[0])]
                print('Measured '+variant+' '+str(report['variants'][variant]['timing']),flush=True)
            # Measure all three retrieval paths while the installed chat model generates.
            chat_port=free_port();cfg=dataclasses.replace(model_cfg,host='127.0.0.1',port=chat_port)
            os.environ['GATHM_LLAMACPP_BASE_URL']=cfg.server_url+'/v1'
            os.environ['GATHM_LLAMACPP_AUTOSTART']='0'
            print('Starting isolated configured chat model for load test',flush=True)
            ready,detail=llamacpp.start(cfg,timeout=60);model_started=True
            if not ready:raise RuntimeError(detail)
            provider=LLMProvider(LLMConfig(backend='llamacpp',model=llamacpp.model_label(cfg.model),base_url=cfg.base_url))
            provider.complete([{'role':'user','content':'Say ready.'}],max_tokens=8,temperature=0,timeout=45)
            stop=threading.Event();model_errors=[];chat_durations=[]
            def chat_load():
                while not stop.is_set():
                    try:
                        begin=time.monotonic()
                        provider.complete([{'role':'user','content':'Explain how to build a simple database with several practical examples.'}],max_tokens=400,temperature=0,timeout=45)
                        chat_durations.append(round((time.monotonic()-begin)*1000,3))
                    except Exception as exc: model_errors.append(type(exc).__name__);break
            thread=threading.Thread(target=chat_load,daemon=True);thread.start()
            time.sleep(.5)
            try:
                for name,fn in [('gathm',current)]+[(key,value[1]) for key,value in runners.items()]:
                    print('Benchmarking under chat load: '+name,flush=True)
                    report['variants'][name]['under_chat_load']=benchmark(fn)
            finally:
                stop.set();thread.join(50)
            report['chat_load_errors']=model_errors
            report['chat_load_completions_ms']=chat_durations
            if model_errors or not chat_durations or thread.is_alive():
                raise RuntimeError('Configured chat load did not complete successfully')
            for variant in ('same-bge', 'default-fts'):
                source = report['variants'][variant]
                hits = unrelated_count = 0
                for case in source['cases'] + source['unrelated']:
                    query_vector = semantic.normalize(next(iter(encoder.query_embed(case['query']))))
                    scores = {row['id']: sum(a*b for a,b in zip(query_vector, semantic.normalize(row['vector']))) for row in records}
                    closest = sorted(scores, key=scores.get, reverse=True)
                    choice = closest[0] if scores[closest[0]] >= semantic.MIN_SIMILARITY and scores[closest[0]]-scores[closest[1]] >= semantic.MIN_MARGIN else None
                    raw_ids = {item['id'] for item in case['result']['data']['items']}
                    words = harness._terms(case['query'])
                    chosen = {row['id'] for row in records if row['id'] in raw_ids and (row['id']==choice or len(words & harness._terms(row['content'].split('\n')[0]))>=2)}
                    if 'expected' in case:
                        hits += case['expected'] in chosen
                    else:
                        unrelated_count += bool(chosen)
                source['offline_shared_relevance_gate'] = {'hits': hits, 'unrelated_return_count': unrelated_count, 'note': 'Post-hoc diagnostic only; separate complete-fixture local scoring. Adapter latency and lifecycle are not measured.'}
        except Exception as exc:
            report['error']=str(exc)
            print('Benchmark error: '+str(exc),flush=True)
        finally:
            if model_started:llamacpp.stop(cfg)
            for proc,log in children:
                if proc.poll() is None:
                    os.killpg(proc.pid,signal.SIGTERM)
                    try:proc.wait(10)
                    except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
                log.close()
            server.shutdown();server.server_close()
            if semantic._worker:
                semantic._worker.jobs.put(None,timeout=10);semantic._worker.thread.join(10)
            (OUT/'tencent-latency-report.json').write_text(json.dumps(report,indent=2))
            summary={name:{key:variant.get(key) for key in ('timing','hits','unrelated_return_count','runtime')} for name,variant in report['variants'].items()}
            print(json.dumps({'summary':summary,'error':report.get('error')},indent=2),flush=True)
    return 1 if report.get('error') else 0


if __name__ == '__main__':
    raise SystemExit(main())
