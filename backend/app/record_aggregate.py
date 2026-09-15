"""Deterministic counts of authorized record groups, never inferred clinical frequency.

The caller must supply the complete permission-filtered scope BEFORE retrieval
ranking. An incomplete scope, uncertain attribution or unresolved duplicate is a
normal insufficiency result, not a model failure or an invented global ranking.
"""
from collections import defaultdict
from datetime import date
import re
from zoneinfo import ZoneInfo
from .attachment_review_policy import attachment_evidence_text

KST=ZoneInfo('Asia/Seoul')
CRITERIA={
    '낙상':r'낙상|넘어짐', '배회':r'배회', '식사 거부':r'식사\s*거부|식사를?\s*거부',
    '복약 거부':r'복약\s*거부|투약\s*거부|약\s*복용을?\s*거부',
    '구토':r'구토', '발열':r'발열', '기침':r'기침', '통증':r'통증',
    '설사':r'설사', '욕설':r'욕설', '폭언':r'폭언', '폭행':r'폭행',
}
AGGREGATE=re.compile(r'최다|횟수|몇\s*(?:번|회|건)|(?:가장|제일)\s*(?:많|자주)|많은\s*(?:어르신|사람)|(?:건수|횟수).*비교|비교.*(?:건수|횟수)')
UNCERTAIN=re.compile(r'의심|여부|가능성|확실하지|불확실')
PLANNED=re.compile(r'예정|계획|예방|위험|교육|관찰\s*필요|주의')
NEGATIVE=re.compile(r'없|않|아니|아닌|아님|미발생|부인|관찰되지|발생하지')
CORRECTION=re.compile(r'정정|취소|오기|잘못\s*(?:된|기록|작성)')
POSITIVE=re.compile(r'발생|관찰|보였|보임|했|하였|함|있었|있음|확인|\d+\s*(?:회|번)|호소|지속|후')


def is_aggregate_question(question):
    return bool(AGGREGATE.search(question))


def has_later_record_correction(question,sources,end_date):
    patterns=[pattern for pattern in CRITERIA.values() if re.search(pattern,question)]
    if len(patterns)!=1:return False
    return any(comment.created_at.astimezone(KST).date()>end_date and CORRECTION.search(comment.body)
        and (re.search(patterns[0],comment.body) or re.search(r'이전\s*기록|위\s*기록|해당\s*기록|오기',comment.body))
        for source in sources for comment in source.comments)


def has_unattributed_source(question, sources, *, start_date, end_date, split_resident_text):
    """Detect relevant text lost by conservative multi-resident attribution.

    Inspect all named sections even for a single-resident query, so intentionally
    excluding another resident's attributed section is not mistaken for loss.
    Never repair missing attribution by guessing who an anonymous sentence names.
    """
    patterns = [pattern for pattern in CRITERIA.values() if re.search(pattern, question)]
    if len(patterns) != 1:
        return False
    relevant = re.compile(f'(?:{patterns[0]})|(?:{CORRECTION.pattern})')
    for source in sources:
        message = source.message
        residents = {}
        if message.resident is not None:
            residents[message.resident.id] = message.resident.display_name
        for link in message.resident_links:
            if link.status == 'confirmed':
                residents[link.resident.id] = link.resident.display_name
        if len(residents) <= 1:
            continue
        names = list(residents.values())
        parts = [(message.created_at, message.body)]
        parts.extend((comment.created_at, comment.body) for comment in source.comments)
        parts.extend((message.created_at, attachment_evidence_text(attachment))
                     for attachment in message.attachments)
        for at, text in parts:
            if not start_date <= at.astimezone(KST).date() <= end_date:
                continue
            expected = len(relevant.findall(text))
            attributed = sum(len(relevant.findall(split_resident_text(
                text, target_name=name, resident_names=names))) for name in names)
            if expected != attributed:
                return True
    return False


def has_unread_aggregate_attachments(sources, *, start_date, end_date):
    """Missing reviewed evidence is unknown coverage, never a zero occurrence.

    This does not read files or trigger extraction. It uses the same authorized
    evidence boundary as the care-topic builder and reports a normal limitation.
    """
    for source in sources:
        message = source.message
        if not start_date <= message.created_at.astimezone(KST).date() <= end_date:
            continue
        for attachment in message.attachments:
            extraction = attachment.text_extraction
            if (extraction is None or extraction.status not in {'completed', 'reviewed'}
                    or not attachment_evidence_text(attachment)):
                return True
    return False


def _result(category,status,reason_codes,answer,*,counts=(),leaders=(),evidence=()):
    return {
        'answer':answer,'processing_method':'rules' if status=='complete' else 'clarification',
        'generation_verified':False,'error_type':None,'generator':'authorized-record-groups-v1',
        'evidence_ids':list(dict.fromkeys(evidence)), 'answer_sentences':[],
        'matched_count':sum(row['record_count'] for row in counts),
        'limitation':'선택 기간의 기록 작성일과 현재 접근 권한을 기준으로 원문·연결 답글을 묶은 기록 건수입니다. 실제 발생 횟수나 기관 전체 발생 순위를 뜻하지 않습니다. 실제 횟수는 사건별 원문 확인이 필요합니다.',
        'aggregate':{'metric':'record_event_groups','category':category,'status':status,
            'scope_complete':status=='complete','counts':list(counts),
            'leaders':list(leaders),'reason_codes':list(dict.fromkeys(reason_codes))},
    }


def _state(text,pattern):
    """Interpret only the named criterion, scoped away from other symptoms."""
    states=[]
    # Other criterion words terminate the current term's suffix, preventing
    # e.g. '낙상 발생, 통증 없음' from being classified as no fall.
    all_terms=re.compile('|'.join(f'(?:{value})' for value in CRITERIA.values()))
    for clause in re.split(r'[.!?\n,;]+|(?:으나|지만)\s*',text):
        terms=list(all_terms.finditer(clause))
        for i,match in enumerate(terms):
            if not re.fullmatch(pattern,match.group()):continue
            segment=clause[match.start():terms[i+1].start() if i+1<len(terms) else len(clause)]
            if UNCERTAIN.search(segment):states.append('uncertain')
            elif PLANNED.search(segment):states.append('planned')
            elif NEGATIVE.search(segment):
                after=re.search(r'(?:이후|후)\s',segment)
                if after and not NEGATIVE.search(segment[:after.end()]):states.append('positive')
                else:states.append('negative')
            elif POSITIVE.search(segment):states.append('positive')
            else:states.append('uncertain')
    return states


def aggregate_question(question,facts,*,start_date:date,end_date:date,scope_complete:bool,
                       scope_reason_codes=()):
    if not is_aggregate_question(question):return None
    labels=[label for label,pattern in CRITERIA.items() if re.search(pattern,question)]
    if len(labels)!=1:
        return _result(None,'clarification',['criterion_unspecified'],
            '집계할 행동이나 증상의 기준이 명확하지 않습니다. 배회·식사 거부·낙상처럼 한 가지 기준과 조회 기간을 지정해 주세요. 일부 기록만으로 어르신의 전체 순위를 정하지 않습니다.')
    category=labels[0];pattern=CRITERIA[category]
    if re.search(r'시간대|날짜별|요일별|어느\s*날|어느\s*시간|어느\s*방|방별',question):
        return _result(category,'clarification',['dimension_unspecified'],
            '현재 집계는 선택 기간 안의 어르신별 기록 묶음 수를 비교합니다. 시간대·날짜·방별 순위는 이 결과로 판단할 수 없습니다. 비교할 어르신과 기간을 지정해 주세요.')
    reasons=list(scope_reason_codes)
    if not scope_complete:reasons.append('scope_incomplete')
    rows=[f for f in facts if start_date<=f['occurred_at'].astimezone(KST).date()<=end_date]
    names={str(f['resident_id']):f['resident_name'] for f in rows if f.get('resident_id') is not None}
    mentioned={rid for rid,name in names.items() if re.search(
        r'(?<![가-힣A-Za-z0-9])'+re.escape(name)+r'(?=$|[\s,.!?]|어르신|님|은|는|이|가|의|와|과)',question)}
    if mentioned:rows=[f for f in rows if str(f.get('resident_id')) in mentioned]
    grouped=defaultdict(list)
    for row in rows:
        grouped[(str(row.get('resident_id')),str(row.get('event_key') or row['message_id']))].append(row)
    counted=defaultdict(list);duplicates={}
    for (resident,key),items in grouped.items():
        positive=False;relevant=[];signatures=[]
        actions_at=defaultdict(set)
        for row in items:
            if 'positive' in _state(row['summary'],pattern):actions_at[row['occurred_at']].add('positive')
            if CORRECTION.search(row['summary']):actions_at[row['occurred_at']].add('correction')
        if any({'positive','correction'}<=actions for actions in actions_at.values()):
            reasons.append('source_order_ambiguous')
        for row in sorted(items,key=lambda f:(f['occurred_at'],str(f['message_id']))):
            text=row['summary'];states=_state(text,pattern)
            # Explicit source corrections may omit the original criterion.
            correction=bool(CORRECTION.search(text))
            refers_to_record=bool(re.search(r'이전\s*기록|위\s*기록|해당\s*기록|오기',text))
            if correction and (states or refers_to_record):
                if 'negative' in states or re.search(r'취소|오기',text):
                    positive=False;relevant=[];signatures=[]
                    continue
                reasons.append('correction_unresolved')
            if 'uncertain' in states:reasons.append('occurrence_uncertain')
            if 'positive' in states:
                positive=True
                signatures.append((row['occurred_at'].astimezone(KST).date(),re.sub(r'\s+','',text)))
            if states or (positive and row.get('kind') in {'followup','different','conflict'}):
                relevant.append(row['message_id'])
        if not positive:continue
        if resident=='None':reasons.append('resident_unassigned');continue
        for day,signature in signatures:
            prior=duplicates.setdefault((resident,day,signature),key)
            if prior!=key:reasons.append('duplicate_unlinked_records')
        counted[resident].append(list(dict.fromkeys(relevant)))
    if reasons:
        explanations={
            'scope_incomplete':'조회 제한 또는 기간 불일치로 전체 대상 기록을 확보하지 못했습니다.',
            'duplicate_unlinked_records':'별도 메시지에 동일한 기록이 있어 같은 사건의 중복인지 확인해야 합니다.',
            'resident_unassigned':'집계 대상 기록 중 어르신이 지정되지 않은 기록이 있습니다.',
            'occurrence_uncertain':'발생 여부가 명확하지 않은 기록이 있어 확인이 필요합니다.',
            'correction_unresolved':'정정 내용이 명확하지 않아 사건별 원문 확인이 필요합니다.',
            'event_link_incomplete':'답글과 원문의 연결 범위를 완전히 확인하지 못했습니다.',
            'source_order_ambiguous':'같은 시각의 발생 기록과 정정 기록이 충돌하여 적용 순서를 확인해야 합니다.',
            'correction_outside_period':'선택 기간 이후의 정정·취소 답글이 있어 기간을 넓혀 원문과 함께 확인해야 합니다.',
            'source_attribution_incomplete':'여러 어르신이 연결된 기록 중 누구의 내용인지 구분되지 않은 본문·답글·첨부 내용이 있습니다.',
            'attachment_scope_incomplete':'선택 기간에 읽기 또는 직원 확인이 완료되지 않았거나 집계 가능한 텍스트가 없는 첨부자료가 있어 전체 내용을 확인하지 못했습니다.',
        }
        detail=' '.join(explanations.get(code,'집계 범위의 추가 확인이 필요합니다.') for code in dict.fromkeys(reasons))
        return _result(category,'insufficient',reasons,detail+' 현재 자료만으로 최다 여부나 정확한 건수를 판정하지 않겠습니다. 조회 기간·대상을 조정하거나 원문을 확인해 주세요.')
    counts=[{'resident_id':rid,'resident_name':names[rid],'record_count':len(groups),
             'evidence_ids':list(dict.fromkeys(mid for group in groups for mid in group))}
            for rid,groups in counted.items()]
    counts.sort(key=lambda r:(-r['record_count'],r['resident_name'],r['resident_id']))
    maximum=counts[0]['record_count'] if counts else 0
    leaders=[row['resident_id'] for row in counts if row['record_count']==maximum]
    if not counts:
        answer=f'선택한 접근 가능 기록에서 {category}의 긍정 발생 기록 묶음은 0건입니다. 실제로 발생하지 않았다는 뜻은 아닙니다.'
    else:
        answer=f'{category} 기록 묶음: '+', '.join(f"{r['resident_name']} {r['record_count']}건" for r in counts)+'.'
        if len(leaders)>1:answer+=' 기록 묶음 수 기준 공동 최다입니다.'
        elif re.search(r'가장|제일|최다|많은',question):answer+=' 기록 묶음 수 기준 최다는 '+counts[0]['resident_name']+'입니다.'
    return _result(category,'complete',[],answer,counts=counts,leaders=leaders,
        evidence=[mid for row in counts for mid in row['evidence_ids']])
