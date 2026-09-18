"""Question-scoped nutrition evidence and conservative, source-bound summaries."""
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

QUESTION = re.compile(r'식사|잘\s*드(?:심|시|신|셨)|섭취|밥|반찬|식욕|음용량')
RECORD = re.compile(r'식사|섭취|밥|반찬|식욕|음용량|점심|아침|저녁|간식|수분|음수|음료|우유|보리차|마셨|마심|(?<![가-힣])물(?:\s|\d|을|은)')
UNRELATED = re.compile(r'피부|상처|팔꿈치|붉은\s*부위')
QUANTITY = re.compile(r'(밥|반찬|죽|물|우유|음료|보리차)\s*(\d+(?:/\d+|[~〜-]\d+)?(?:\s*(?:숟가락|공기|mL|ml|밀리리터|잔|개|g))?)')
LIMIT = '확인된 기록만으로 최근 전반적인 식사 상태를 단정하기는 어렵습니다.'


def nutrition_question(question):
    return bool(QUESTION.search(question)) and not UNRELATED.search(question)


def nutrition_facts(question, facts):
    if not nutrition_question(question):
        return list(facts)
    names = {f.get('resident_name') for f in facts if f.get('resident_name') and f['resident_name'] in question}
    selected = []
    for fact in facts:
        if names and fact.get('resident_name') not in names:
            continue
        clauses = [part.strip() for part in re.split(r'(?<=[.!?])\s+|\n+', fact['summary'])
                   if RECORD.search(part) and not UNRELATED.search(part)]
        if clauses:
            selected.append({**fact, 'summary': ' '.join(clauses)})
    return selected


def intake_table(text):
    """A quantified intake ledger is not the same as a provision-only note."""
    return bool(re.search(r'(?<!미)섭취표\s*[:：]', text) and QUANTITY.search(text)
                and not re.search(r'미확인|미섭취|예정|제공|권유|준비|섭취하지', re.sub(r'추가\s*제공\s*후', '', text)))


def normalized_quantity_text(text):
    foods = r'(밥|반찬|죽|물|우유|음료|보리차)'
    text = re.sub(foods+r'(?:은|는|을|를)\s*', r'\1 ', text)
    text = re.sub(foods+r'\s*(?:절반|반(?!찬))', r'\1 1/2', text)
    text = re.sub(r'(\d+)\s*분의\s*(\d+)', r'\2/\1', text)
    return re.sub(r'두\s*세\s*숟가락', '2~3숟가락', text)


def complete_meal_evidence(question, facts, sentences):
    """A surviving sentence cannot stand in for a meal's omitted intake.

    Compare only exact food/quantity pairs, within the cited source, including
    the initial/additional distinction. Unrecognized paraphrases use the safe
    source-bound summary, not a weaker factual/event-relation guard.
    """
    if not UNRELATED.search(question) and re.search(r'수분|음수|음용|섭취|물|요약|정리|상태', question):
        records = [{'id': str(index), 'text': f['summary'], 'message_id': f['message_id']}
                   for index, f in enumerate(facts)]
        claims = [{'text': s['text'], 'citations': [r['id'] for r in records if r['message_id'] in s['evidence_ids']]}
                  for s in sentences]
        if missing_intake_records(records, claims):
            return False
    scope_question = question
    if not nutrition_question(question) and re.search(r'요약|정리', question) and not UNRELATED.search(question):
        scope_question = '식사'
    summary = nutrition_answer(scope_question, facts)
    if not summary:
        return True
    relevant = nutrition_facts(scope_question, facts)
    tables = {f['message_id']: f for f in relevant if intake_table(f['summary'])}
    asked_foods = set(re.findall(r'밥|반찬|죽|물|우유|음료|보리차', question))
    if re.search(r'식사|섭취|상태|요약|정리', question):
        asked_foods = set()

    def items(text):
        # In Korean, 추가로 may follow the food/amount. Preserve that role,
        # without treating the earlier initial-intake clause as additional.
        normalized = normalized_quantity_text(text)
        normalized = re.sub(r'(밥|반찬|죽|물|우유|음료|보리차)\s*(?:다시\s*)?제공받아\s*(?=\d)', r'\1 ', normalized)
        return {(m[1], re.sub(r'\s+', '', m[2]), '추가' in clause)
                for clause in re.split(r'[,;.!?\n]|(?<=드셨고)|(?<=섭취했고)', normalized)
                for m in QUANTITY.finditer(clause) if not asked_foods or m[1] in asked_foods}

    for expected in summary['answer_sentences']:
        for identifier in expected['evidence_ids']:
            if identifier not in tables:
                continue
            table = tables[identifier]
            actual = set()
            for sentence in sentences:
                supported = set()
                for fact in relevant:
                    if (fact['message_id'] in sentence['evidence_ids']
                        and fact.get('resident_id') == table.get('resident_id')
                        and str(fact['occurred_at'])[:10] == str(table['occurred_at'])[:10]):
                        supported.update(items(fact['summary']))
                actual.update(items(sentence['text']) & supported)
            if not items(expected['text']).issubset(actual):
                return False
    return True


def intake_requirements(records):
    # Reuse the existing offer/consumption binding. Deferred import avoids the
    # search -> narrative -> nutrition module dependency during initialization.
    from .search_summary_narrative import _fluid_quantity_actions
    required = []
    for record in records:
        amounts = sorted(value for value, action in _fluid_quantity_actions(record['text']) if action == 'consumed')
        if amounts:
            required.append({'id': record['id'], 'consumed_ml': amounts, 'text': record['text']})
    return required


def missing_intake_records(records, sentences):
    from .search_summary_narrative import _fluid_quantity_actions
    from .record_answer_quality import repeated_intake_supported
    missing = []
    for required in intake_requirements(records):
        actual = set()
        for sentence in sentences:
            if required['id'] not in sentence['citations']:
                continue
            if not repeated_intake_supported(sentence['text'], [required]):
                continue
            actual.update(value for value, action in _fluid_quantity_actions(sentence['text']) if action == 'consumed')
        if not set(required['consumed_ml']) <= actual:
            missing.append(required)
    return missing


def nutrition_answer(question, facts):
    if not nutrition_question(question):
        return None
    facts = nutrition_facts(question, facts)
    if not facts:
        return None
    tables = [f for f in facts if intake_table(f['summary'])]
    table_ids = {f['message_id'] for f in tables}
    # A ledger enriches its own source; it must not erase later intake records.
    # Prefer recorded intake over administrative handover/planning clauses.
    consumed = re.compile(r'드셨|드신|마셨|먹었|섭취(?:했|함|량)|섭취표')
    intake_facts = [f for f in facts if consumed.search(f['summary'])]
    chosen = tables + [f for f in (intake_facts or facts) if f['message_id'] not in table_ids]
    chosen = sorted(chosen, key=lambda f: (str(f['occurred_at']), str(f['message_id'])))
    multiple_people = len({str(f.get('resident_id') or f.get('resident_name')) for f in chosen}) > 1
    sentences, seen = [], set()
    for fact in chosen:
        event = (str(fact.get('resident_id')), str(fact['message_id']), fact['summary'])
        if event in seen:
            continue
        seen.add(event)
        when = fact['occurred_at']
        if isinstance(when, str):
            when = datetime.fromisoformat(when)
        when = (when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when).astimezone(ZoneInfo('Asia/Seoul'))
        prefix = (f"{fact['resident_name']} — " if multiple_people and fact.get('resident_name') else '') + f'{when.month}월 {when.day}일'
        text = fact['summary']
        if intake_table(text):
            meal = next((word for word in ('아침', '점심', '저녁', '간식') if word in text), '섭취')
            initial, additional = [], []
            boundary = re.search(r'추가\s*제공\s*후', text)
            for match in QUANTITY.finditer(text):
                value = f'{match[1]} {match[2]}'
                target = additional if boundary and match.start() > boundary.end() else initial
                if value not in target:
                    target.append(value)
            observation = prefix + f' {meal} 기록에서는 ' + '과 '.join(initial) + '을 드셨습니다.'
            if additional:
                observation = observation.removesuffix('드셨습니다.') + '드셨고, 추가 제공 후 ' + '과 '.join(additional) + '을 더 드셨습니다.'
        else:
            observation = f'{prefix} 확인 내용: {text}'
        sentences.append({'text': observation, 'evidence_ids': [fact['message_id']]})
    refs = list(dict.fromkeys(identifier for sentence in sentences for identifier in sentence['evidence_ids']))
    sentences.append({'text': LIMIT, 'evidence_ids': refs})
    return {'answer': ' '.join(s['text'] for s in sentences), 'answer_sentences': sentences, 'evidence_ids': refs}
