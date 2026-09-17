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


def complete_meal_evidence(question, facts, sentences):
    """A surviving sentence cannot stand in for a meal's omitted intake.

    Compare only exact food/quantity pairs, within the cited source, including
    the initial/additional distinction. Unrecognized paraphrases use the safe
    source-bound summary, not a weaker factual/event-relation guard.
    """
    summary = nutrition_answer(question, facts)
    if not summary:
        return True
    tables = {f['message_id'] for f in nutrition_facts(question, facts)
              if intake_table(f['summary'])}

    def items(text):
        extra = re.search(r'추가', text)
        return {(m[1], re.sub(r'\s+', '', m[2]), bool(extra and m.start() > extra.end()))
                for m in QUANTITY.finditer(text)}

    for expected in summary['answer_sentences']:
        for identifier in expected['evidence_ids']:
            if identifier not in tables:
                continue
            actual = set()
            for sentence in sentences:
                if identifier in sentence['evidence_ids']:
                    actual.update(items(sentence['text']))
            if not items(expected['text']).issubset(actual):
                return False
    return True


def nutrition_answer(question, facts):
    if not nutrition_question(question):
        return None
    facts = nutrition_facts(question, facts)
    if not facts:
        return None
    tables = [f for f in facts if intake_table(f['summary'])]
    chosen = tables or facts
    chosen = sorted(chosen, key=lambda f: (str(f['occurred_at']), str(f['message_id'])), reverse=True)
    sentences, seen = [], set()
    for fact in chosen:
        event = (str(fact.get('resident_id')), str(fact.get('event_key') or fact['message_id']))
        if event in seen:
            continue
        seen.add(event)
        when = fact['occurred_at']
        if isinstance(when, str):
            when = datetime.fromisoformat(when)
        when = (when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when).astimezone(ZoneInfo('Asia/Seoul'))
        prefix = f'{when.month}월 {when.day}일'
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
            observation = f'{prefix} 식사 관련 기록에는 “{text}”라고 적혀 있습니다.'
        sentences.append({'text': observation, 'evidence_ids': [fact['message_id']]})
        if len(sentences) == 2:
            break
    refs = list(dict.fromkeys(identifier for sentence in sentences for identifier in sentence['evidence_ids']))
    sentences.append({'text': LIMIT, 'evidence_ids': refs})
    return {'answer': ' '.join(s['text'] for s in sentences), 'answer_sentences': sentences, 'evidence_ids': refs}
