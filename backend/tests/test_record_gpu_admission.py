"""Synthetic qualification data only; none of these values authorize production."""
import asyncio
import json
import subprocess
from types import SimpleNamespace

import pytest

from app import record_narrative as n


def profile():
    return {
        "base_url": "http://127.0.0.1:11434", "gpu_uuid": "GPU-synthetic",
        "total_mib": 32768, "model": "synthetic-model", "digest": "a" * 64,
        "context_tokens": 4096, "ollama_version": "synthetic-version",
        "required_free_mib": 21000, "reserve_mib": 2048,
        "max_loaded_models": 2, "allowed_resident_digests": ["b" * 64],
    }


def setup(monkeypatch, *, free=24000, patch_profile=None, loaded=None, gpu="GPU-synthetic", version="synthetic-version", digest="a" * 64):
    # These tests replace local_request, so no HTTP transport is exercised.
    # Avoid charging Windows certificate-store construction to a 120ms
    # synthetic capacity deadline; keep real asyncio timeouts and semaphore.
    class NoTransport:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
    monkeypatch.setattr(n.httpx, 'AsyncClient', NoTransport)
    qualification=profile()
    qualification.update(patch_profile or {})
    # Settings is a protected operator input, not an AI-settings API field.
    monkeypatch.setattr(n, "settings", SimpleNamespace(
        record_ai_gpu_capacity_profile_json=json.dumps(qualification),
        record_ai_prepare_timeout_seconds=5, record_ai_keep_alive_seconds=180,
        record_ai_coexistence_verified=False))
    prepared=[]
    residents=loaded if loaded is not None else [{"name":"synthetic-vl", "digest":"b"*64, "size_vram":5*1024**3}]
    async def request(client, base, path, body, timeout):
        if path=="/api/ps":return {"models":residents}
        if path=="/api/show":return {}
        if path=="/api/version":return {"version":version}
        if path=="/api/tags":return {"models":[{"name":"synthetic-model", "digest":digest}]}
        assert path=="/api/generate", "No stop/unload or unrelated API is allowed"
        assert "prompt" not in body and "messages" not in body
        assert body["keep_alive"]==180
        prepared.append(body)
        return {"done":True,"model":"synthetic-model"}
    monkeypatch.setattr(n,"local_request",request)
    def telemetry(args, **kwargs):
        assert args==["nvidia-smi","--query-gpu=uuid,memory.total,memory.free", "--format=csv,noheader,nounits"]
        assert kwargs["timeout"]<=2 and not kwargs.get("shell",False)
        return SimpleNamespace(returncode=0,stdout=f"{gpu}, 32768, {free}\n")
    monkeypatch.setattr(subprocess,"run",telemetry)
    return prepared


def test_unprofiled_resident_naturally_expires_before_chat_load(monkeypatch):
    loads=setup(monkeypatch)
    n.settings.record_ai_gpu_capacity_profile_json=None
    n.settings.record_ai_resident_wait_seconds=2
    original=n.local_request
    checks=0
    async def request(client,base,path,body,timeout):
        nonlocal checks
        if path=='/api/ps':
            checks+=1
            return {'models':[] if checks>=3 else [{'name':'talk-synthetic-model'}]}
        return await original(client,base,path,body,timeout)
    monkeypatch.setattr(n,'local_request',request)
    result=prepare()
    assert result['status']=='ready',result
    assert len(loads)==1 and checks>=3
    assert result['resident_wait_ms']>0


def test_unprofiled_busy_deadline_never_loads_or_evicts(monkeypatch):
    loads=setup(monkeypatch)
    n.settings.record_ai_gpu_capacity_profile_json=None
    n.settings.record_ai_resident_wait_seconds=.12
    result=prepare()
    assert result['error_type']=='model_prepare_busy',result
    assert result['resident_wait_ms']>=50
    assert loads==[]
    assert n._PREPARE_SLOT.acquire(blocking=False)
    n._PREPARE_SLOT.release()


def test_unprofiled_wait_is_visible_without_restarting_shared_task(monkeypatch):
    async def run():
        loads=setup(monkeypatch)
        n.settings.record_ai_gpu_capacity_profile_json=None
        n.settings.record_ai_resident_wait_seconds=.15
        policy=SimpleNamespace(base_url='http://127.0.0.1:11434',context_tokens=4096)
        monkeypatch.setattr(n,'load_ai_settings',lambda:(None,None))
        monkeypatch.setattr(n,'effective_central_models',lambda _:policy)
        monkeypatch.setattr(n,'central_feature_selection',lambda *_:SimpleNamespace(provider='ollama',model='synthetic-model'))
        first=await n.prepare_record_model(wait=False)
        assert first['status']=='preparing',first
        assert first['phase']=='waiting_capacity'
        assert first['wait_reason']=='model_prepare_busy'
        assert first['preparation_budget_ms']==5150
        result=await n.prepare_record_model()
        assert result['error_type']=='model_prepare_busy' and loads==[]
    asyncio.run(run())


def prepare():
    return asyncio.run(n._prepare_record_model("http://127.0.0.1:11434", "synthetic-model", SimpleNamespace(context_tokens=4096)))


def test_transient_measured_shortage_recovers_without_eviction(monkeypatch):
    loads=setup(monkeypatch,free=22000)
    samples=iter([22000,24000])
    monkeypatch.setattr(subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=0,stdout=f'GPU-synthetic, 32768, {next(samples)}\n'))
    result=prepare()
    assert result['status']=='ready',result
    assert len(loads)==1
    assert result['capacity_wait_ms']>0


def test_busy_resident_naturally_expires_before_preparation(monkeypatch):
    loads=setup(monkeypatch,patch_profile={'max_loaded_models':1})
    original=n.local_request;checks=0
    async def request(client,base,path,body,timeout):
        nonlocal checks
        if path=='/api/ps':
            checks+=1
            return {'models':[] if checks>=3 else [{'name':'synthetic-vl','digest':'b'*64}]}
        return await original(client,base,path,body,timeout)
    monkeypatch.setattr(n,'local_request',request)
    result=prepare()
    assert result['status']=='ready',result
    assert len(loads)==1 and checks>=3


def test_capacity_wait_is_bounded_and_releases_preparation_slot(monkeypatch):
    loads=setup(monkeypatch,free=22000)
    n.settings.record_ai_prepare_timeout_seconds=.12
    result=prepare()
    assert result['error_type']=='gpu_memory_insufficient'
    assert result.get('capacity_wait_ms',0)>=50
    assert loads==[]
    assert n._PREPARE_SLOT.acquire(blocking=False)
    n._PREPARE_SLOT.release()


def test_capacity_wait_reports_nonblocking_phase_and_does_not_restart_budget(monkeypatch):
    async def run():
        loads=setup(monkeypatch,free=22000)
        n.settings.record_ai_prepare_timeout_seconds=.2
        policy=SimpleNamespace(base_url='http://127.0.0.1:11434',context_tokens=4096)
        monkeypatch.setattr(n,'load_ai_settings',lambda:(None,None))
        monkeypatch.setattr(n,'effective_central_models',lambda _:policy)
        monkeypatch.setattr(n,'central_feature_selection',lambda *_:SimpleNamespace(provider='ollama',model='synthetic-model'))
        first=await n.prepare_record_model(wait=False)
        assert first['status']=='preparing',first
        assert first['phase']=='waiting_capacity'
        assert first['wait_reason']=='gpu_memory_insufficient'
        result=await n.prepare_record_model()
        assert result['error_type']=='gpu_memory_insufficient' and loads==[]
    asyncio.run(run())


def test_metadata_timeout_after_capacity_wait_is_not_reported_as_memory_shortage(monkeypatch):
    loads=setup(monkeypatch,free=22000)
    original=n.local_request;versions=0
    async def request(client,base,path,body,timeout):
        nonlocal versions
        if path=='/api/version':
            versions+=1
            if versions==2:raise TimeoutError()
        return await original(client,base,path,body,timeout)
    monkeypatch.setattr(n,'local_request',request)
    assert prepare()['error_type']=='model_prepare_timeout'
    assert loads==[]


def test_cancelling_capacity_task_releases_slot_without_loading(monkeypatch):
    async def run():
        loads=setup(monkeypatch,free=22000)
        progress={}
        task=asyncio.create_task(n._prepare_record_model('http://127.0.0.1:11434','synthetic-model',SimpleNamespace(context_tokens=4096),progress=progress))
        async with asyncio.timeout(1):
            while progress.get('phase')!='waiting_capacity':await asyncio.sleep(.001)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert loads==[]
        assert n._PREPARE_SLOT.acquire(blocking=False)
        n._PREPARE_SLOT.release()
    asyncio.run(run())


def test_other_resident_is_not_blanket_rejected_when_measured_capacity_is_qualified(monkeypatch):
    loads=setup(monkeypatch)
    result=prepare()
    assert result["status"]=="ready",result
    assert len(loads)==1


def test_insufficient_measured_memory_is_distinct_and_never_loads(monkeypatch):
    loads=setup(monkeypatch,free=22000)
    assert prepare()["error_type"]=="gpu_memory_insufficient"
    assert loads==[]


@pytest.mark.parametrize("changes",[
    {"gpu":"GPU-other"}, {"version":"changed-version"}, {"digest":"c"*64},
    {"patch_profile":{"context_tokens":8192}},
    {"patch_profile":{"base_url":"http://different-host:11434"}},
    {"patch_profile":{"required_free_mib":-1}},
    {"patch_profile":{"required_free_mib":True}},
    {"patch_profile":{"allowed_resident_digests":[]}},
])
def test_unqualified_or_mismatched_evidence_fails_closed(monkeypatch,changes):
    loads=setup(monkeypatch,**changes)
    assert prepare()["error_type"]=="gpu_capacity_unverified"
    assert loads==[]


def test_resident_slot_limit_prevents_automatic_scheduler_eviction(monkeypatch):
    loads=setup(monkeypatch,patch_profile={"max_loaded_models":1})
    assert prepare()["error_type"]=="model_prepare_busy"
    assert loads==[]


def test_empty_ollama_does_not_hide_memory_used_by_stt_when_profile_enabled(monkeypatch):
    loads=setup(monkeypatch,loaded=[],free=1000)
    assert prepare()["error_type"]=="gpu_memory_insufficient"
    assert loads==[]


def test_unavailable_gpu_measurement_never_uses_model_file_size_as_free_memory(monkeypatch):
    loads=setup(monkeypatch,loaded=[])
    def unavailable(*args,**kwargs):raise FileNotFoundError("nvidia-smi")
    monkeypatch.setattr(subprocess,"run",unavailable)
    assert prepare()["error_type"]=="gpu_capacity_unverified"
    assert loads==[]


@pytest.mark.parametrize("output",["", "GPU-synthetic, 32768, N/A", "GPU-synthetic, 32768, -1",
    "GPU-synthetic, 32768, 40000", "GPU-synthetic, 16384, 10000",
    "GPU-synthetic, 32768, 24000\nGPU-second, 32768, 24000"])
def test_malformed_or_multi_gpu_telemetry_is_not_admission(monkeypatch,output):
    loads=setup(monkeypatch)
    monkeypatch.setattr(subprocess,"run",lambda *args,**kwargs:SimpleNamespace(returncode=0,stdout=output))
    assert prepare()["error_type"]=="gpu_capacity_unverified"
    assert loads==[]


def test_exact_measured_requirement_plus_reserve_is_sufficient(monkeypatch):
    loads=setup(monkeypatch,free=23048)
    assert prepare()["status"]=="ready"
    assert len(loads)==1


def test_stale_telemetry_is_rejected(monkeypatch):
    from app import record_gpu_capacity as capacity
    loads=setup(monkeypatch)
    timestamps=iter([100.0,103.0])
    monkeypatch.setattr(capacity,"perf_counter",lambda:next(timestamps))
    assert prepare()["error_type"]=="gpu_capacity_unverified"
    assert loads==[]


def test_capacity_is_checked_again_after_waiting_for_preparation_slot(monkeypatch):
    async def run():
        loads=setup(monkeypatch)
        original=n.local_request
        checked=asyncio.Event()
        other_loaded=False
        async def changed(client,base,path,body,timeout):
            if path=="/api/ps":
                checked.set()
                return {"models":[{"name":"new-unqualified-model","digest":"c"*64}] if other_loaded else []}
            return await original(client,base,path,body,timeout)
        monkeypatch.setattr(n,"local_request",changed)
        assert n._PREPARE_SLOT.acquire(blocking=False)
        task=asyncio.create_task(n._prepare_record_model("http://127.0.0.1:11434","synthetic-model",SimpleNamespace(context_tokens=4096)))
        try:
            await asyncio.wait_for(checked.wait(),1)
            other_loaded=True
        finally:n._PREPARE_SLOT.release()
        result=await task
        assert result["error_type"]=="gpu_capacity_unverified"
        assert loads==[]
    asyncio.run(run())
