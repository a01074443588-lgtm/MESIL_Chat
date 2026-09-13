"""Cancellable natural answers with deterministic and local semantic verification."""
from __future__ import annotations
import asyncio,hashlib,ipaddress,json,re,socket
from threading import BoundedSemaphore,RLock
from time import perf_counter
from urllib.parse import urlsplit
import httpx
from pydantic import BaseModel,ConfigDict,Field
from .ai_settings_store import load_ai_settings,effective_central_models,central_feature_selection
from .record_text_ai import prepare_evidence,deidentify,_restore,RecordModelError
from .config import settings
from .record_gpu_capacity import read_profile,require_capacity
from .record_answer_quality import GroundedDraft,GroundingReview,guarded_sentences,review_payload,DRAFT_INSTRUCTION,REVIEW_INSTRUCTION

_SLOTS=BoundedSemaphore(2)
_PREPARE_SLOT=BoundedSemaphore(1)
_LOCK=RLock()
_ACTIVE=set()
_WARM_TASKS={}
_WARM_FINISHED={}
_WARM_PROGRESS={}

def model_retention(model=None,context_tokens=8192):
    configured=settings.record_ai_keep_alive_seconds
    qualified=(settings.record_ai_coexistence_verified and model is not None
        and model==settings.record_ai_qualified_text_model
        and context_tokens<=settings.record_ai_qualified_context_tokens)
    return configured if qualified else min(configured,180)

async def prepare_record_model(*,wait=True,feature='care_record_question'):
    """Content-free preparation shared by callers on this single-worker service.

    Only the selected model/context is sent. No question, record or identity.
    The shared task is bounded independently; disconnecting one screen must not
    cancel another screen's preparation. It never stops an existing model.
    """
    if feature not in {'care_record_question','search_summary','document_text'}:
        return {'status':'unavailable','error_type':'local_model_unconfigured'}
    document,_=load_ai_settings();policy=effective_central_models(document)
    selected=central_feature_selection(policy,feature)
    if not selected or selected.provider!='ollama' or not selected.model:
        return {'status':'unavailable','error_type':'local_model_unconfigured'}
    base=policy.base_url or document.providers['ollama'].base_url or settings.ai_review_base_url
    loop=asyncio.get_running_loop()
    # A two-second ready handoff avoids turning every poll->question transition
    # into another slow status check. Generation still rechecks live residency.
    for old_key,old_task in list(_WARM_TASKS.items()):
        if old_task.done() and (old_key[0].is_closed() or
                perf_counter()-_WARM_FINISHED.get(old_key,perf_counter())>2):
            _WARM_TASKS.pop(old_key,None);_WARM_FINISHED.pop(old_key,None);_WARM_PROGRESS.pop(old_key,None)
    key=(loop,base,selected.model,policy.context_tokens)
    task=_WARM_TASKS.get(key)
    if task is None:
        progress={}
        _WARM_PROGRESS[key]=progress
        task=asyncio.create_task(_prepare_record_model(base,selected.model,policy,progress=progress))
        _WARM_TASKS[key]=task
        def finished(completed):
            if _WARM_TASKS.get(key) is not completed:return
            if completed.cancelled():
                _WARM_TASKS.pop(key,None);_WARM_FINISHED.pop(key,None);_WARM_PROGRESS.pop(key,None)
            else:_WARM_FINISHED[key]=perf_counter()
        task.add_done_callback(finished)
    try:
        if not wait:
            done,_=await asyncio.wait({task},timeout=.05)
            if not done:return {'status':'preparing','error_type':None,'model':selected.model,
                'preparation_budget_ms':round((settings.record_ai_prepare_timeout_seconds+getattr(settings,'record_ai_resident_wait_seconds',0))*1000),
                **_WARM_PROGRESS.get(key,{})}
        return await asyncio.shield(task)
    finally:
        if task.done() and not task.cancelled() and task.result().get('status')!='ready' and _WARM_TASKS.get(key) is task:
            _WARM_TASKS.pop(key,None);_WARM_FINISHED.pop(key,None);_WARM_PROGRESS.pop(key,None)

async def _prepare_record_model(base,model,policy,*,progress=None):
    start=perf_counter();result={'status':'unavailable','error_type':None,'model':model,
        'status_ms':0,'cold_load_ms':0,'keep_alive_seconds':model_retention(model,policy.context_tokens)}
    acquired=False;capacity_wait_started=None;waiting_reason=None;resident_wait_started=None
    resident_wait_seconds=getattr(settings,'record_ai_resident_wait_seconds',0)
    try:
        async with asyncio.timeout(settings.record_ai_prepare_timeout_seconds+resident_wait_seconds),httpx.AsyncClient(trust_env=False,follow_redirects=False) as client:
            ready=await local_request(client,base,'/api/ps',None,2)
            info=await local_request(client,base,'/api/show',{'model':model},2)
            result['status_ms']=round((perf_counter()-start)*1000)
            if info.get('remote_model') or info.get('remote_host') or 'cloud' in model.lower():
                raise RecordModelError('remote_model_blocked')
            loaded=ready.get('models',[])
            active=next((row for row in loaded if model in (row.get('name'),row.get('model'))),None)
            result['loaded_vram_bytes']=sum(int(row.get('size_vram') or 0) for row in loaded)
            if active and active.get('context_length',policy.context_tokens)==policy.context_tokens:
                result['status']='ready';return result
            # Insufficient evidence of spare GPU memory is not permission to
            # evict an image model. Existing jobs and model lifetimes remain intact.
            capacity_profile=getattr(settings,'record_ai_gpu_capacity_profile_json',None)
            if loaded and not capacity_profile and not resident_wait_seconds:
                raise RecordModelError('gpu_capacity_unverified')
            while not acquired:
                acquired=_PREPARE_SLOT.acquire(blocking=False)
                if not acquired:
                    await asyncio.sleep(.05)
            # Recheck even an initially empty runner while holding our slot.
            # Another caller may have loaded a model after the initial read.
            while True:
                waiting_reason=None
                try:
                    loaded=await _admit_preparation(client,base,model,policy.context_tokens)
                    break
                except RecordModelError as exc:
                    # Missing qualification never authorizes coexistence: only
                    # natural release (or an exact already-loaded model) admits.
                    if not capacity_profile:
                        if not resident_wait_seconds or str(exc) not in {'gpu_capacity_unverified','model_prepare_busy'}:raise
                        waiting_reason='model_prepare_busy'
                        if resident_wait_started is None:resident_wait_started=perf_counter()
                        remaining=resident_wait_seconds-(perf_counter()-resident_wait_started)
                        if remaining<=0:raise RecordModelError('model_prepare_busy')
                        if progress is not None:progress.update(phase='waiting_capacity',wait_reason=waiting_reason)
                        await asyncio.sleep(min(1,remaining))
                        continue
                    # Invalid/stale qualification or telemetry stays fail-closed.
                    if str(exc) not in {'gpu_memory_insufficient','model_prepare_busy'}:raise
                    waiting_reason=str(exc)
                    if capacity_wait_started is None:capacity_wait_started=perf_counter()
                    if progress is not None:progress.update(phase='waiting_capacity',wait_reason=waiting_reason)
                    await asyncio.sleep(1)
            if capacity_wait_started is not None:
                result['capacity_wait_ms']=round((perf_counter()-capacity_wait_started)*1000)
                capacity_wait_started=None
            if resident_wait_started is not None:
                result['resident_wait_ms']=round((perf_counter()-resident_wait_started)*1000)
                resident_wait_started=None
            waiting_reason=None
            if progress is not None:progress.clear()
            if any(model in (row.get('name'),row.get('model')) and row.get('context_length')==policy.context_tokens for row in loaded):
                result['status']='ready';return result
            preparation_started=perf_counter()
            async with asyncio.timeout(settings.record_ai_prepare_timeout_seconds):
                loaded_result=await local_request(client,base,'/api/generate',{
                    'model':model,'stream':False,'keep_alive':model_retention(model,policy.context_tokens),
                    'options':{'num_ctx':policy.context_tokens}},settings.record_ai_prepare_timeout_seconds)
            if loaded_result.get('done') is not True or loaded_result.get('model')!=model:
                raise RecordModelError('model_response_invalid')
            result['preparation_call_ms']=round((perf_counter()-preparation_started)*1000)
            # Empty-prompt preloads may omit Ollama load_duration. Unknown is
            # not zero; keep measured request time separate from model time.
            result['cold_load_ms']=(round(loaded_result['load_duration']/1e6)
                if loaded_result.get('load_duration') is not None else None)
            result['status']='ready'
    except (TimeoutError,httpx.TimeoutException):result['error_type']=waiting_reason or 'model_prepare_timeout'
    except httpx.HTTPStatusError as exc:result['error_type']='model_missing' if exc.response.status_code==404 else 'model_server_error'
    except httpx.RequestError:result['error_type']='model_connection_error'
    except RecordModelError as exc:result['error_type']=str(exc)
    except Exception:result['error_type']='model_internal_error'
    finally:
        if capacity_wait_started is not None:result['capacity_wait_ms']=round((perf_counter()-capacity_wait_started)*1000)
        if resident_wait_started is not None:result['resident_wait_ms']=round((perf_counter()-resident_wait_started)*1000)
        if acquired:_PREPARE_SLOT.release()
        result['total_ms']=round((perf_counter()-start)*1000)
    return result

async def _admit_preparation(client,base,model,context_tokens):
    """Called while holding the preparation slot; telemetry is never cached."""
    raw=getattr(settings,'record_ai_gpu_capacity_profile_json',None)
    profile=read_profile(raw,base=base,model=model,context_tokens=context_tokens) if raw else None
    digest=version=None
    if profile:
        version=(await local_request(client,base,'/api/version',None,2)).get('version')
        tags=await local_request(client,base,'/api/tags',None,2)
        digest=next((row.get('digest') for row in tags.get('models',[]) if model in (row.get('name'),row.get('model'))),None)
    loaded=(await local_request(client,base,'/api/ps',None,2)).get('models',[])
    active=next((row for row in loaded if model in (row.get('name'),row.get('model'))),None)
    if active:
        if active.get('context_length')==context_tokens:return loaded
        # Resizing a loaded model may evict it; not covered by this admission.
        raise RecordModelError('model_prepare_busy')
    if profile:
        await require_capacity(profile,loaded=loaded,digest=digest,version=version)
    elif loaded:raise RecordModelError('gpu_capacity_unverified')
    return loaded

PREFIXES=('기록에는 ', '최근 기록에는 ', '이전 기록에는 ', '이후 기록에는 ', '가장 최근 기록에는 ')

class NarrativeSentence(BaseModel):
    model_config=ConfigDict(extra='forbid')
    text:str=Field(min_length=1,max_length=800)
    citations:list[str]=Field(min_length=1,max_length=1)

class Narrative(BaseModel):
    model_config=ConfigDict(extra='forbid')
    sentences:list[NarrativeSentence]=Field(max_length=4)

def compact_repeated_facts(facts):
    """Keep first/latest evidence for exact repeated observations sent to AI."""
    positions={}
    for index,fact in enumerate(facts):
        key=(str(fact.get('resident_id') or fact.get('resident_name') or ''),re.sub(r'\s+',' ',fact.get('summary','')).strip())
        positions.setdefault(key,[]).append(index)
    keep=set()
    for indexes in positions.values():
        keep.add(indexes[0]);keep.add(indexes[-1])
    return [fact for index,fact in enumerate(facts) if index in keep]

def source_clauses(text):
    return list(dict.fromkeys(part.strip().rstrip('.!?') for part in re.split(r'\n+|(?<=[.!?])\s+',text) if part.strip()))

def natural_clause(clause):
    for ending,replacement in [('보임','보였습니다'),('드심','드셨습니다'),('없음','없었습니다'),('있음','있었습니다'),('들음','들었습니다'),('됨','되었습니다'),('함','했습니다'),('임','입니다')]:
        if clause.endswith(ending):return clause[:-len(ending)]+replacement
    return clause

def validate_narrative(raw,records):
    value=Narrative.model_validate(raw);by_id={r['id']:r for r in records}
    valid=[];seen=set();rejected=0
    for sentence in value.sentences:
        token=sentence.citations[0];record=by_id.get(token)
        if record is None:rejected+=1;continue
        multi = len({r['person'] for r in records}) > 1
        label = record['person']+'의 ' if multi else ''
        prefix=next((p for p in sorted(PREFIXES,key=len,reverse=True) if sentence.text.startswith(label+p)),None)
        if not prefix:rejected+=1;continue
        peers=[r for r in records if r['person']==record['person']]
        if prefix=='가장 최근 기록에는 ' and record['date']!=max(r['date'] for r in peers):rejected+=1;continue
        if prefix=='이전 기록에는 ' and not any(r['date']>record['date'] for r in peers):rejected+=1;continue
        if prefix=='이후 기록에는 ' and not any(r['date']<record['date'] for r in peers):rejected+=1;continue
        framing=label+prefix
        allowed=set()
        for clause in source_clauses(record['text']):
            allowed.add(framing+natural_clause(clause)+'.')
            allowed.add(framing+'“'+clause+'”라고 적혀 있습니다.')
        if sentence.text not in allowed:rejected+=1;continue
        factual=sentence.text[len(framing):]
        if (record['person'],factual) in seen:continue
        seen.add((record['person'],factual));valid.append(sentence)
    if len(valid)<2:raise RecordModelError('narrative_validation_failed')
    cited={s.citations[0] for s in valid}
    # Latest status must not be replaced by an older attractive observation.
    for person in {by_id[token]['person'] for token in cited}:
        latest=max(r['date'] for r in records if r['person']==person)
        if not any(by_id[token]['date']==latest for token in cited if by_id[token]['person']==person):raise RecordModelError('latest_evidence_missing')
    return valid,rejected

async def local_request(client,base,path,body,timeout):
    parsed=urlsplit(base)
    if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:raise RecordModelError('endpoint_blocked')
    addresses=await asyncio.to_thread(socket.getaddrinfo,parsed.hostname,parsed.port or (443 if parsed.scheme=='https' else 80),type=socket.SOCK_STREAM)
    if not addresses or any(not (ipaddress.ip_address(row[4][0]).is_private or ipaddress.ip_address(row[4][0]).is_loopback) for row in addresses):raise RecordModelError('external_endpoint_blocked')
    async with client.stream('POST' if body is not None else 'GET',base.rstrip('/')+path,json=body,timeout=timeout) as response:
        if response.is_redirect:raise RecordModelError('redirect_blocked')
        data=bytearray()
        async for part in response.aiter_bytes():
            data.extend(part)
            if len(data)>200000:raise RecordModelError('response_limit')
        if response.status_code>=400 and re.search(rb'out of memory|cuda[^\n]{0,80}memory',bytes(data),re.I):
            raise RecordModelError('gpu_memory_insufficient')
        if response.status_code==400:
            # Recognize the runner's explicit input refusal, not arbitrary
            # occurrences of "context" (nor any echoed question/record fields).
            try:
                error=json.loads(data).get('error')
                if isinstance(error,dict):error=error.get('message')
            except (ValueError,AttributeError):error=None
            if isinstance(error,str) and re.search(r'request \(\d+ tokens\) exceeds the available context size \(\d+ tokens\)',error):
                raise RecordModelError('context_limit')
        response.raise_for_status()
        return json.loads(data)

async def generate_narrative(*,question,facts,names,all_synthetic,request_key,deadline,allow_cold_start=False,progress=None):
    start=perf_counter();out={'processing_method':'rules','generation_verified':False,'error_type':None,'ai_elapsed_ms':0,'load_ms':None,'generation_ms':None,'prefill_ms':None,'rejected_sentence_count':0,'draft_sentence_count':None,'guarded_sentence_count':None,'correction_attempted':False,'validation_stage':'setup'}
    acquired=False;prepare_acquired=False;registered=False
    try:
        with _LOCK:
            if request_key in _ACTIVE:raise RecordModelError('duplicate_in_progress')
            _ACTIVE.add(request_key);registered=True
        acquired=_SLOTS.acquire(blocking=False)
        if not acquired:raise RecordModelError('busy')
        document,_=load_ai_settings();policy=effective_central_models(document);selected=central_feature_selection(policy,'care_record_question')
        out['validation_stage']='model_selection'
        if not selected or selected.provider!='ollama' or not selected.model:raise RecordModelError('local_model_unconfigured')
        if not all_synthetic and not policy.real_record_logging_verified:raise RecordModelError('logging_policy_unverified')
        if not facts or len(facts)>32:raise RecordModelError('no_relevant_records' if not facts else 'evidence_limit')
        model_facts=compact_repeated_facts(facts)
        out['model_candidate_count']=len(model_facts)
        records,mapping,aliases=prepare_evidence(model_facts,names)
        # The timestamp is a message-recording time. Give the model the date
        # for ordering, while event times must come from the cited source text.
        records=[{**record,'date':record['date'][:10]} for record in records]
        base=policy.base_url or document.providers['ollama'].base_url or settings.ai_review_base_url
        # The central limit and the request's remaining absolute deadline both
        # apply to draft generation AND independent evidence verification.
        budget=min(policy.timeout_seconds,deadline-perf_counter())
        if budget<.2:raise RecordModelError('timeout')
        async with asyncio.timeout(budget),httpx.AsyncClient(trust_env=False,follow_redirects=False) as client:
            out['validation_stage']='readiness'
            if progress:progress('preparing')
            status_started=perf_counter()
            ready=await local_request(client,base,'/api/ps',None,min(1,budget))
            loaded=ready.get('models',[])
            active=next((r for r in loaded if selected.model in (r.get('name'),r.get('model'))),None)
            out['loaded_model_count']=len(loaded)
            out['loaded_vram_bytes']=sum(int(row.get('size_vram') or 0) for row in loaded)
            try:
                info=await local_request(client,base,'/api/show',{'model':selected.model},min(1,budget))
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    raise RecordModelError('model_missing') from exc
                raise
            if info.get('remote_model') or info.get('remote_host') or 'cloud' in selected.model.lower():raise RecordModelError('remote_model_blocked')
            out['model_status_ms']=round((perf_counter()-status_started)*1000)
            # Do not evict a different active model (image/STT coexistence).
            # With an empty Ollama slot, first-load work shares the same short,
            # cancellable budget as generation and cannot run after disconnect.
            if active is None and loaded and not allow_cold_start:raise RecordModelError('model_not_ready')
            if (active is None or active.get('context_length',policy.context_tokens)!=policy.context_tokens) and not allow_cold_start:
                raise RecordModelError('model_cold')
            # Cold-start permission is not permission to resize a resident
            # model. Match _admit_preparation: wait for its natural release,
            # rather than letting /api/chat replace its active context.
            if active is not None and active.get('context_length',policy.context_tokens)!=policy.context_tokens:
                raise RecordModelError('model_prepare_busy')
            if active is None:
                if loaded and not getattr(settings,'record_ai_gpu_capacity_profile_json',None):raise RecordModelError('gpu_capacity_unverified')
                # Only one cold preparation may run in this process.  Ollama's
                # current model/VRAM state was read above, but no model is
                # stopped or evicted by this adapter.
                prepare_acquired=_PREPARE_SLOT.acquire(blocking=False)
                if not prepare_acquired:raise RecordModelError('model_prepare_busy')
                if getattr(settings,'record_ai_gpu_capacity_profile_json',None):
                    await _admit_preparation(client,base,selected.model,policy.context_tokens)
            # Extend an already loaded model briefly. Never load another model,
            # force an eviction, or pin multiple models indefinitely.
            keep_alive=model_retention(selected.model,policy.context_tokens)
            safe_question=deidentify(question,aliases)
            instruction={'question':safe_question,'records':records}
            if len(json.dumps(instruction,ensure_ascii=False))>policy.max_input_chars:raise RecordModelError('context_limit')
            async def chat(prompt,payload,schema,max_tokens):
                body={'model':selected.model,'stream':False,'think':False,'keep_alive':keep_alive,'format':schema.model_json_schema(),'options':{'temperature':0,'num_ctx':policy.context_tokens,'num_predict':max_tokens},'messages':[{'role':'system','content':prompt},{'role':'user','content':json.dumps(payload,ensure_ascii=False)}]}
                # Bound ALL requests, including schema, correction feedback and
                # evidence review. This is a character/resource cap, NOT a
                # tokenizer estimate; the runner's context refusal is separate.
                # Never silently shorten evidence or a previous draft to fit.
                if len(json.dumps(body,ensure_ascii=False))>policy.max_input_chars:
                    raise RecordModelError('context_limit')
                response=await local_request(client,base,'/api/chat',body,budget)
                if response.get('model')!=selected.model or response.get('done') is not True:raise RecordModelError('model_response_invalid')
                for field,remote in [('load_ms','load_duration'),('generation_ms','eval_duration'),('prefill_ms','prompt_eval_duration')]:out[field]=(out[field] or 0)+round(response.get(remote,0)/1e6)
                return response
            if progress:progress('verifying')
            total_rejected=0;guard_reasons=set();sentences=[];previous=None
            out['draft_ms']=0;out['evidence_review_ms']=0
            out['draft_generation_ms']=0;out['draft_prefill_ms']=0;out['review_generation_ms']=0
            for attempt in range(2):
                correction=attempt==1
                if correction:out['correction_attempted']=True
                out['validation_stage']='correction_generation' if correction else 'draft_generation'
                draft_started=perf_counter();generation_before=out['generation_ms'] or 0;prefill_before=out['prefill_ms'] or 0
                prompt=(DRAFT_INSTRUCTION if not correction else DRAFT_INSTRUCTION+'\n이전 초안은 일부 사실 또는 근거 검사를 통과하지 못했습니다. 검증 가능한 핵심만 남겨 다시 작성하세요. 같은 오류를 반복하지 마세요.')
                if correction and 'question_not_answered' in guard_reasons:
                    prompt+='\n이전 초안은 질문의 결론에 직접 답하지 못했습니다. 같은 날짜순 사실 목록을 반복하지 마세요. 비교 질문이면 변화 여부를 판단할 수 있는지를 첫 문장에 답하세요. 이전 또는 이후의 실제 값이 기록되지 않았다면 증가·감소를 단정하지 말고 비교할 수 없는 이유를 limitation으로 설명하세요. 제공량과 실제 섭취량을 서로 비교하지 마세요. 확인된 최근 사실은 그 다음에 간결하게 덧붙이세요.'
                if correction and 'resident' in guard_reasons:
                    prompt+='\n대상 혼합 오류를 수정하세요. 이전 문장의 여러 person을 각각 별도 문장 객체로 나누고 각 객체에는 동일 person의 근거만 인용하세요. 사람별 요약 자체가 conclusion이 될 수 있습니다. 근거가 있는 사람별 사실을 버리고 빈 sentences로 바꾸지 마세요.'
                if correction and 'event_relation' in guard_reasons:
                    prompt+='\n별개 사건을 경과로 연결한 오류를 수정하세요. event_group이 다른 원문의 상태를 한 사건 이후의 결과처럼 쓰지 말고 별도의 사실로 나누세요. 특히 식사 뒤 상태를 낙상 뒤 상태로 바꾸지 마세요.'
                draft_payload=(instruction if not correction else {**instruction,'previous_draft':previous,'validation_feedback':sorted(guard_reasons) or ['semantic_review']})
                # Korean text plus citation/role JSON can exceed 256 tokens even
                # for a short answer. The outer timeout still bounds correction.
                response=await chat(prompt,draft_payload,GroundedDraft,512)
                out['draft_ms']+=round((perf_counter()-draft_started)*1000)
                out['draft_generation_ms']+=(out['generation_ms'] or 0)-generation_before
                out['draft_prefill_ms']+=(out['prefill_ms'] or 0)-prefill_before
                if response.get('done_reason') in {'length','max_tokens'}:
                    if not correction:previous={'sentences':[]};guard_reasons.add('response_truncated');continue
                    raise RecordModelError('response_truncated')
                out['validation_stage']='draft_validation'
                try:raw=json.loads(response['message']['content'])
                except (json.JSONDecodeError,TypeError,KeyError):
                    if not correction:previous={'sentences':[]};guard_reasons.add('response_format_invalid');continue
                    raise RecordModelError('response_format_invalid')
                previous=raw
                out['draft_sentence_count']=len(raw.get('sentences',[])) if isinstance(raw,dict) else None
                guard_diagnostics={}
                try:sentences,rejected=guarded_sentences(raw,records,safe_question,guard_diagnostics)
                except ValueError:
                    sentences=[];rejected=0;guard_diagnostics={'response_format_invalid':1}
                total_rejected+=rejected;guard_reasons.update(guard_diagnostics)
                out['rejected_sentence_count']=total_rejected
                if not sentences:
                    if not correction:continue
                    raise RecordModelError('no_relevant_records' if raw=={'sentences':[]} else 'narrative_validation_failed')
                # Review can accept/reject facts but cannot add a conclusion.
                # This draft cannot meet the final answer contract even if all
                # review flags are true; spend the remaining budget on repair.
                if not any(s.role in ('conclusion','limitation') for s in sentences):
                    guard_reasons.add('missing_conclusion')
                    if not correction:continue
                    raise RecordModelError('question_answer_not_supported')
                out['validation_stage']='semantic_review'
                review_started=perf_counter();generation_before=out['generation_ms'] or 0
                review_response=await chat(REVIEW_INSTRUCTION,review_payload(sentences,records,safe_question),GroundingReview,80)
                out['evidence_review_ms']+=round((perf_counter()-review_started)*1000)
                out['review_generation_ms']+=(out['generation_ms'] or 0)-generation_before
                try:review=GroundingReview.model_validate_json(review_response['message']['content'])
                except ValueError:
                    if not correction:guard_reasons.add('review_format_invalid');continue
                    raise RecordModelError('review_format_invalid')
                if len(review.supported)>len(sentences):
                    review=review.model_copy(update={'supported':review.supported[:len(sentences)]})
                    guard_reasons.add('review_count_normalized')
                elif len(review.supported)<len(sentences):
                    if not correction:guard_reasons.add('review_count_mismatch');continue
                    raise RecordModelError('question_answer_not_supported')
                total_rejected+=sum(not item for item in review.supported)
                out['rejected_sentence_count']=total_rejected
                if not review.answers_question:guard_reasons.add('question_not_answered')
                if not all(review.supported):guard_reasons.add('unsupported_sentence')
                # Keep actionable, content-free feedback on terminal failures
                # too; the final success-only update used to lose this reason.
                out['guard_rejection_types']=','.join(sorted(guard_reasons)) or None
                sentences=[sentence for sentence,supported in zip(sentences,review.supported) if supported]
                if review.answers_question and sentences and any(s.role in ('conclusion','limitation') for s in sentences):break
                if not correction:guard_reasons.add('semantic_review');continue
                raise RecordModelError('question_answer_not_supported')
            out.update(guarded_sentence_count=len(sentences),rejected_sentence_count=total_rejected,
                guard_rejection_types=','.join(sorted(guard_reasons)) or None)
            rendered=[{'text':_restore(s.text,aliases),'evidence_ids':[mapping[token]['message_id'] for token in s.citations]} for s in sentences]
            out.update(processing_method='local_ai',generation_verified=True,sentences=rendered,answer=' '.join(s['text'] for s in rendered),evidence_ids=list(dict.fromkeys(identifier for s in rendered for identifier in s['evidence_ids'])),keep_alive_seconds=keep_alive)
            out['validation_stage']='complete'
            out['selected_facts']=[mapping[token] for token in dict.fromkeys(token for sentence in sentences for token in sentence.citations)]
    except (TimeoutError,httpx.TimeoutException):out['error_type']='timeout'
    except httpx.HTTPStatusError:out['error_type']='model_server_error'
    except httpx.RequestError:out['error_type']='model_connection_error'
    except (RecordModelError,ValueError,KeyError) as exc:out['error_type']=str(exc) if isinstance(exc,RecordModelError) else 'narrative_validation_failed'
    except Exception:out['error_type']='model_internal_error'
    finally:
        if prepare_acquired:_PREPARE_SLOT.release()
        if acquired:_SLOTS.release()
        if registered:
            with _LOCK:_ACTIVE.discard(request_key)
        out['ai_elapsed_ms']=round((perf_counter()-start)*1000)
    return out

async def await_connected(task,request):
    try:
        while not task.done():
            if request is not None and await request.is_disconnected():
                raise asyncio.CancelledError
            await asyncio.wait({task},timeout=.1)
        return await task
    finally:
        if not task.done():
            task.cancel()
            try:await task
            except asyncio.CancelledError:pass
