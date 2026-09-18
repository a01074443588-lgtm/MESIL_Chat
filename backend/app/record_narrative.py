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
from .record_answer_quality import model_json_object
from .record_nutrition import nutrition_question, nutrition_facts, complete_meal_evidence, intake_requirements, missing_intake_records, LIMIT

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
    facts=nutrition_facts(question,facts)
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
            required_intake = intake_requirements(records)
            if required_intake:
                instruction['required_intake_records'] = required_intake
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
            partial_repair = None
            missing_intake = []
            out['draft_ms']=0;out['evidence_review_ms']=0
            out['draft_generation_ms']=0;out['draft_prefill_ms']=0;out['review_generation_ms']=0
            primary_generation_error=None
            for attempt in range(2):
                correction=attempt==1
                if correction:out['correction_attempted']=True
                out['validation_stage']='correction_generation' if correction else 'draft_generation'
                draft_started=perf_counter();generation_before=out['generation_ms'] or 0;prefill_before=out['prefill_ms'] or 0
                prompt=(DRAFT_INSTRUCTION if not correction else DRAFT_INSTRUCTION+'\n이전 초안은 일부 사실 또는 근거 검사를 통과하지 못했습니다. 검증 가능한 핵심만 남겨 다시 작성하세요. 같은 오류를 반복하지 마세요.')
                if nutrition_question(question):
                    prompt+='\n식사 질문에는 확인된 날짜와 실제 섭취 내용을 간결하게 답하세요. 섭취표의 수치는 섭취 기록이며 단순 제공 기록과 구분하세요. 피부 등 무관한 내용은 제외하세요. 한두 기록으로 전반적인 식사 상태를 단정하지 말고 판단 한계를 limitation 문장으로 명시하세요.'
                if correction and 'question_not_answered' in guard_reasons:
                    prompt+='\n이전 초안은 질문의 결론에 직접 답하지 못했습니다. 같은 날짜순 사실 목록을 반복하지 마세요. 비교 질문이면 변화 여부를 판단할 수 있는지를 첫 문장에 답하세요. 이전 또는 이후의 실제 값이 기록되지 않았다면 증가·감소를 단정하지 말고 비교할 수 없는 이유를 limitation으로 설명하세요. 제공량과 실제 섭취량을 서로 비교하지 마세요. 확인된 최근 사실은 그 다음에 간결하게 덧붙이세요.'
                if correction and 'resident' in guard_reasons:
                    prompt+='\n대상 혼합 오류를 수정하세요. 이전 문장의 여러 person을 각각 별도 문장 객체로 나누고 각 객체에는 동일 person의 근거만 인용하세요. 사람별 요약 자체가 conclusion이 될 수 있습니다. 근거가 있는 사람별 사실을 버리고 빈 sentences로 바꾸지 마세요.'
                if correction and 'event_relation' in guard_reasons:
                    prompt+='\n별개 사건을 경과로 연결한 오류를 수정하세요. event_group이 다른 원문의 상태를 한 사건 이후의 결과처럼 쓰지 말고 별도의 사실로 나누세요. 특히 식사 뒤 상태를 낙상 뒤 상태로 바꾸지 마세요.'
                if correction and 'incomplete_meal_evidence' in guard_reasons:
                    prompt+='\n섭취표의 최초 섭취와 추가 섭취가 모두 필요합니다. 추가량을 누락하지 마세요. 같은 사실이 별도의 추가 섭취 원문에도 있으면 그 원문만 직접 인용하세요. 다른 event_group의 계획과 연결하지 말고 실제 추가 섭취 사실 자체를 설명하세요.'
                if correction and 'repeated_quantity' in guard_reasons:
                    prompt+='\n한 번의 양과 횟수를 함께 보존하세요. 예를 들어 원문에 두 차례이면 자주 또는 한 번의 양만으로 바꾸지 말고 두 차례를 명시하세요. 합계는 임의 계산하지 마세요.'
                if required_intake:
                    prompt+='\nrequired_intake_records는 원문에서 실제 섭취가 명시된 핵심 근거입니다. 각 id의 섭취량과 횟수를 모두 답변에 보존하세요. 제공량·계획은 섭취량이 아닙니다. 동일한 양의 반복은 횟수를 쓰고 임의로 합산하지 마세요. 최초 관찰과 뒤의 추가 섭취를 구분하며, 한 시점의 일부 섭취를 하루 전체량으로 한정하지 마세요. 행정적인 연락 문구보다 이 사실을 우선하세요.'
                draft_payload=(instruction if not correction else {**instruction,'previous_draft':previous,'validation_feedback':sorted(guard_reasons) or ['semantic_review'], 'missing_intake_records': missing_intake})
                focused_repair = correction and partial_repair and bool(guard_reasons & {'repeated_quantity', 'incomplete_meal_evidence', 'unsupported_conclusion'})
                if focused_repair:
                    repair_records = partial_repair['records']
                    repair_groups = {r.get('event_group') for r in repair_records}
                    repair_intake = [r for r in required_intake if any(
                        candidate['id'] == r['id'] and candidate.get('event_group') in repair_groups
                        for candidate in records)]
                    if repair_intake and all(re.search(r'\d+\s*(?:mL|ml|밀리리터)', row['text']) for row in partial_repair['rejected']):
                        repair_records = [r for r in records if any(required['id'] == r['id'] for required in repair_intake)]
                    draft_payload = {'question': safe_question, 'records': repair_records,
                                     'previous_draft': {'sentences': partial_repair['rejected']},
                                     'validation_feedback': sorted(guard_reasons),
                                     'required_sentences': len(partial_repair['rejected'])}
                    if repair_intake:
                        draft_payload['required_intake_records'] = repair_intake
                    prompt += '\n서버가 나머지 문장을 보존합니다. 탈락한 핵심 문장만 required_sentences 개로 다시 쓰세요. 다른 사실이나 한계 문장을 새로 추가하지 말고 수량·횟수·추가 섭취를 인용 원문대로 완결하세요.'
                    prompt += '\n최초 관찰과 이후 추가 섭취가 구분되면 처음 또는 해당 관찰 시점이라고 명확히 쓰세요. 한 시점의 일부 섭취량을 하루나 오전 전체의 섭취량으로 한정하지 마세요. 문장을 직접 뒷받침하는 최소 근거만 인용하세요.'
                    prompt += '\n서로 다른 event_group의 사실은 이후·뒤·그 결과로 연결하지 마세요. 각 원문의 처음·오전 중·오후 같은 시점을 따로 쓰고 쉼표와 그리고로 독립된 사실을 나란히 설명하세요. 원문에 없는 합계·총량을 계산하지 말고 개별 양과 횟수를 그대로 쓰세요.'
                # Korean text plus citation/role JSON can exceed 256 tokens even
                # for a short answer. The outer timeout still bounds correction.
                response=await chat(prompt,draft_payload,GroundedDraft,
                                    min(512,128+128*len(partial_repair['rejected'])) if focused_repair else 512)
                out['draft_ms']+=round((perf_counter()-draft_started)*1000)
                out['draft_generation_ms']+=(out['generation_ms'] or 0)-generation_before
                out['draft_prefill_ms']+=(out['prefill_ms'] or 0)-prefill_before
                if response.get('done_reason') in {'length','max_tokens'}:
                    if not correction:
                        primary_generation_error='response_truncated';previous={'sentences':[]};guard_reasons.add('response_truncated');continue
                    raise RecordModelError('response_truncated')
                out['validation_stage']='draft_validation'
                try:raw=model_json_object(response['message']['content'])
                except (json.JSONDecodeError,TypeError,KeyError):
                    if not correction:
                        primary_generation_error='response_format_invalid';previous={'sentences':[]};guard_reasons.add('response_format_invalid');continue
                    raise RecordModelError('response_format_invalid')
                previous=raw
                if focused_repair and isinstance(raw, dict) and isinstance(raw.get('sentences'), list):
                    if len(raw['sentences']) != len(partial_repair['rejected']):
                        raise RecordModelError('repair_count_mismatch')
                    replacements = iter(raw['sentences'])
                    raw = {**raw, 'sentences': [next(replacements) if row is None else row for row in partial_repair['layout']]}
                out['draft_sentence_count']=len(raw.get('sentences',[])) if isinstance(raw,dict) else None
                guard_diagnostics={}
                try:sentences,rejected=guarded_sentences(raw,records,safe_question,guard_diagnostics)
                except ValueError:
                    sentences=[];rejected=0;guard_diagnostics={'response_format_invalid':1}
                total_rejected+=rejected;guard_reasons.update(guard_diagnostics)
                out['rejected_sentence_count']=total_rejected
                missing_intake = missing_intake_records(records, [s.model_dump() for s in sentences])
                if missing_intake:
                    guard_reasons.add('incomplete_meal_evidence')
                if not correction and sentences and rejected:
                    rejected_rows = [row for row in raw['sentences']
                                     if not guarded_sentences({'sentences': [row]}, records, safe_question)[0]]
                    tokens = {token for row in rejected_rows for token in row.get('citations', [])}
                    repair_records = [row for row in records if row['id'] in tokens]
                    if rejected_rows and repair_records:
                        layout = []
                        for row in raw['sentences']:
                            accepted_row = guarded_sentences({'sentences': [row]}, records, safe_question)[0]
                            layout.append(accepted_row[0].model_dump() if accepted_row else None)
                        partial_repair = {'layout': layout, 'rejected': rejected_rows, 'records': repair_records}
                if 'repeated_quantity' in guard_diagnostics:
                    # Dropping the rejected amount sentence is not a complete
                    # answer: retry within the existing two-draft budget.
                    if not correction:
                        primary_generation_error='incomplete_quantity_evidence'
                        continue
                    raise RecordModelError('incomplete_quantity_evidence')
                if not sentences:
                    if not correction:
                        if raw != {'sentences':[]}:
                            primary_generation_error='narrative_validation_failed'
                        continue
                    if primary_generation_error:
                        raise RecordModelError(primary_generation_error)
                    if raw=={'sentences':[]}:
                        out['empty_result_verified']=True
                        raise RecordModelError('no_relevant_records')
                    raise RecordModelError('narrative_validation_failed')
                # Review can accept/reject facts but cannot add a conclusion.
                # This draft cannot meet the final answer contract even if all
                # review flags are true; spend the remaining budget on repair.
                if not any(s.role in ('conclusion','limitation') for s in sentences):
                    guard_reasons.add('missing_conclusion')
                    if not correction:
                        primary_generation_error='question_answer_not_supported';continue
                    raise RecordModelError('question_answer_not_supported')
                pending=[{'text':_restore(s.text,aliases),'evidence_ids':[mapping[token]['message_id'] for token in s.citations]} for s in sentences]
                if not complete_meal_evidence(question, facts, pending):
                    guard_reasons.add('incomplete_meal_evidence')
                    if not correction:
                        primary_generation_error='incomplete_meal_evidence'
                        continue
                    raise RecordModelError('incomplete_meal_evidence')
                out['validation_stage']='semantic_review'
                review_started=perf_counter();generation_before=out['generation_ms'] or 0
                review_response=await chat(REVIEW_INSTRUCTION,review_payload(sentences,records,safe_question),GroundingReview,80)
                out['evidence_review_ms']+=round((perf_counter()-review_started)*1000)
                out['review_generation_ms']+=(out['generation_ms'] or 0)-generation_before
                try:review=GroundingReview.model_validate(model_json_object(review_response['message']['content']))
                except ValueError:
                    if not correction:
                        primary_generation_error='review_format_invalid';guard_reasons.add('review_format_invalid');continue
                    raise RecordModelError('review_format_invalid')
                if len(review.supported)>len(sentences):
                    review=review.model_copy(update={'supported':review.supported[:len(sentences)]})
                    guard_reasons.add('review_count_normalized')
                elif len(review.supported)<len(sentences):
                    if not correction:
                        primary_generation_error='question_answer_not_supported';guard_reasons.add('review_count_mismatch');continue
                    raise RecordModelError('question_answer_not_supported')
                total_rejected+=sum(not item for item in review.supported)
                out['rejected_sentence_count']=total_rejected
                if not review.answers_question:guard_reasons.add('question_not_answered')
                if not all(review.supported):guard_reasons.add('unsupported_sentence')
                # Keep actionable, content-free feedback on terminal failures
                # too; the final success-only update used to lose this reason.
                out['guard_rejection_types']=','.join(sorted(guard_reasons)) or None
                rejected_conclusion = any(not supported and sentence.role == 'conclusion'
                                          for sentence, supported in zip(sentences, review.supported))
                if rejected_conclusion:
                    guard_reasons.add('unsupported_conclusion')
                    if correction:
                        raise RecordModelError('question_answer_not_supported')
                    rejected_rows = [s.model_dump() for s, supported in zip(sentences, review.supported) if not supported]
                    tokens = {token for row in rejected_rows for token in row['citations']}
                    partial_repair = {'layout': [s.model_dump() if supported else None for s, supported in zip(sentences, review.supported)],
                                      'rejected': rejected_rows, 'records': [row for row in records if row['id'] in tokens]}
                    primary_generation_error='question_answer_not_supported'
                    continue
                sentences=[sentence for sentence,supported in zip(sentences,review.supported) if supported]
                missing_intake = missing_intake_records(records, [s.model_dump() for s in sentences])
                if missing_intake:
                    guard_reasons.add('incomplete_meal_evidence')
                    if not correction:
                        primary_generation_error='incomplete_meal_evidence'
                        partial_repair = None
                        continue
                    raise RecordModelError('incomplete_meal_evidence')
                if review.answers_question and sentences and any(s.role in ('conclusion','limitation') for s in sentences):break
                if not correction:
                    primary_generation_error='question_answer_not_supported';guard_reasons.add('semantic_review');continue
                raise RecordModelError('question_answer_not_supported')
            out.update(guarded_sentence_count=len(sentences),rejected_sentence_count=total_rejected,
                guard_rejection_types=','.join(sorted(guard_reasons)) or None)
            rendered=[{'text':_restore(s.text,aliases),'evidence_ids':[mapping[token]['message_id'] for token in s.citations]} for s in sentences]
            if not complete_meal_evidence(question, facts, rendered):
                out['validation_stage'] = 'meal_evidence_completeness'
                raise RecordModelError('incomplete_meal_evidence')
            if nutrition_question(question) and not any(s.role=='limitation' for s in sentences):
                rendered.append({'text':LIMIT,'evidence_ids':list(dict.fromkeys(identifier for s in rendered for identifier in s['evidence_ids']))})
            out.update(processing_method='local_ai',generation_verified=True,model_used=selected.model,sentences=rendered,answer=' '.join(s['text'] for s in rendered),evidence_ids=list(dict.fromkeys(identifier for s in rendered for identifier in s['evidence_ids'])),keep_alive_seconds=keep_alive)
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

def run_help_record_answer(*,question,facts,names,all_synthetic,request_key):
    """Use the same grounded prose contract after the help-room access checks."""
    if not facts:
        return {'processing_method':'rules','generation_verified':False,
                'answer':'','evidence_ids':[],'model_used':None,
                'fallback_reason':'no_relevant_records'}
    result=asyncio.run(generate_narrative(question=question,facts=facts,names=names,
        all_synthetic=all_synthetic,request_key=request_key,deadline=perf_counter()+33,
        allow_cold_start=True))
    return {**result,'fallback_reason':result.get('error_type')}


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
