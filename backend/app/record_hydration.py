"""Event-bound water quantities. Never join people, days, or ambiguous offers."""
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import re
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')
WATER = re.compile(r'수분|음수|(?<![가-힣])물(?=$|[^가-힣]|의|은|는|을|이|도|만|로)')
VOLUME = re.compile(r'(\d+(?:\.\d+)?)\s*(?:ml|밀리리터)', re.I)
FULL = re.compile(r'(?:전량|전부|모두)\s*(?:섭취|마셨|마심|드셨|드심)|남(?:은\s*물|긴\s*물).{0,5}없')
NEGATIVE = re.compile(r'않|못|미확인|불확실|예정|계획|권유|확인.{0,5}(?:불가|안|없)|아직|거부|안\s*(?:마시|마셨|마신|드셨|드신|드심|먹|섭취|제공|확인)')
OFFER = re.compile(r'제공(?:했|함|하였|됐|되었|량|받)|드렸|드림')
REMAIN = re.compile(r'잔량|남(?:은|긴|았)')
CONSUME = re.compile(r'섭취|마셨|마심|마신|드셨|드심|드신')
LATEST = re.compile(r'최근|마지막')


def at(value):
    value = datetime.fromisoformat(value) if isinstance(value, str) else value
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value).astimezone(KST)


def hydration_quantity_intent(question):
    # Phrase recognition happens before generic token length/particle filtering.
    if re.search(r'잔량', question):
        return True
    return bool(WATER.search(question) and re.search(r'양|량|얼마나|몇\s*(?:ml|밀리리터)|드신|드셨어|드셨|마신|마셨|섭취', question, re.I))


def latest_hydration_quantity_intent(question):
    return hydration_quantity_intent(question) and bool(LATEST.search(question))


def _number(text):
    values = {Decimal(value) for value in VOLUME.findall(text)}
    return next(iter(values)) if len(values) == 1 else None


def _identity(fact):
    return str(fact.get('resident_id') or fact.get('resident_name') or '')


def _event(fact):
    return _identity(fact), str(fact.get('event_key') or fact['message_id'])


def _amount(value):
    return format(value.normalize(), 'f') if value is not None else None


def hydration_events(facts):
    unique = {}
    for fact in facts:
        key = (fact['message_id'], fact.get('comment_id'), fact.get('attachment_id'), fact['summary'])
        unique[key] = fact
    entries = sorted(unique.values(), key=lambda fact: (at(fact['occurred_at']), str(fact['message_id'])))
    offers = [fact for fact in entries if WATER.search(fact['summary']) and OFFER.search(fact['summary'])
              and not NEGATIVE.search(fact['summary']) and not FULL.search(fact['summary']) and _number(fact['summary']) is not None]
    events = []
    consumed_offers = set()
    for completion in entries:
        text = completion['summary']
        full = bool(FULL.search(text))
        remaining = bool(REMAIN.search(text))
        if NEGATIVE.search(text) or not (full or remaining or CONSUME.search(text)):
            continue
        amount = _number(text)
        if not full and amount is None:
            continue
        candidates = [offer for offer in offers if _identity(offer) == _identity(completion)
                      and at(offer['occurred_at']).date() == at(completion['occurred_at']).date()
                      and at(offer['occurred_at']) < at(completion['occurred_at'])]
        linked = [offer for offer in candidates if _event(offer) == _event(completion)]
        if linked:
            candidates = linked
        elif not (WATER.search(text) and re.search(r'제공한|드린|제공받은', text)):
            candidates = []
        # Full-volume disagreement is a conflict, never an excuse to choose an
        # older matching amount from a different offer in the same thread.
        if len(candidates) != 1:
            continue
        offer = candidates[0]
        provided = _number(offer['summary'])
        if full and amount is not None and amount != provided:
            continue
        if not full and (amount > provided or re.search(r'쏟|흘|버|추가', text)):
            continue
        consumed = provided if full else None if remaining else amount
        residual = Decimal(0) if full else amount if remaining else None
        consumed_offers.add(id(offer))
        events.append(dict(offered=offer, consumed=completion, completed_at=at(completion['occurred_at']),
                           provided_ml=provided, consumed_ml=consumed, remaining_ml=residual,
                           remaining_basis='full_consumption' if full else 'recorded' if remaining else 'unknown',
                           full=full, facts=[offer, completion]))
    # New uncompleted offers remain selectable for a provided-amount question.
    for offer in offers:
        if id(offer) not in consumed_offers:
            events.append(dict(offered=offer, consumed=None, completed_at=at(offer['occurred_at']),
                               provided_ml=_number(offer['summary']), consumed_ml=None, remaining_ml=None,
                               remaining_basis='unknown', full=False, facts=[offer]))
    return events


def hydration_quantity_fact(question, facts):
    if not hydration_quantity_intent(question):
        return None
    # Totals/comparisons/explicit dates must retain their broader search scope.
    if re.search(r'합계|총량|하루|매일|평균|비교|전보다|처음|\d+\s*월|어제|오늘', question):
        return None
    events = hydration_events(facts)
    if not events or len({_identity(event['offered']) for event in events}) != 1:
        return None
    asks_provided = bool(re.search(r'제공|제공받|받은', question)) and not re.search(r'드셨|드신|섭취|마셨|얼마나.*얼마나', question)
    if not asks_provided:
        completed = [event for event in events if event['consumed'] is not None]
        if completed:
            events = completed
    return max(events, key=lambda event: event['completed_at'])


def _time(value, include_date):
    value = at(value)
    return (f'{value.month}월 {value.day}일 ' if include_date else '') + f'{"오전" if value.hour < 12 else "오후"} {value.hour % 12 or 12}시' + (f' {value.minute}분' if value.minute else '')


def hydration_answer(question, facts):
    event = hydration_quantity_fact(question, facts)
    if not event:
        return None
    offer, completion = event['offered'], event['consumed']
    amount = _amount(event['provided_ml'])
    ids = list(dict.fromkeys(fact['message_id'] for fact in event['facts']))
    text = f"가장 최근 기록에서는 {_time(offer['occurred_at'], True)}에 물 {amount}ml를 제공"
    if completion:
        text += f"했고, {_time(completion['occurred_at'], False)}에 "
        if event['full']:
            text += f'{amount}ml 전량 섭취가 확인됐습니다.'
        elif event['remaining_ml'] is not None:
            text += f"잔량 {_amount(event['remaining_ml'])}ml가 기록됐습니다. 실제 섭취량은 이 기록만으로 확정할 수 없습니다."
        else:
            text += f"{_amount(event['consumed_ml'])}ml 섭취가 확인됐습니다. 잔량은 기록에서 확인되지 않습니다."
    else:
        text += '했습니다. 실제 섭취량과 잔량은 기록에서 확인되지 않습니다.'
    if re.search(r'잔량', question) and event['full']:
        text += ' 전량 섭취 확인에 따라 남은 물은 없는 것으로 확인됩니다.'
    structured = dict(kind='hydration', resident_id=offer.get('resident_id'),
                      provided_ml=_amount(event['provided_ml']), consumed_ml=_amount(event['consumed_ml']),
                      remaining_ml=_amount(event['remaining_ml']), remaining_basis=event['remaining_basis'],
                      offered_at=at(offer['occurred_at']).isoformat(),
                      completed_at=at(completion['occurred_at']).isoformat() if completion else None,
                      evidence_ids=ids,
                      unknowns=[key for key in ('consumed_ml', 'remaining_ml') if event[key] is None])
    return {'sentences': [{'text': text, 'evidence_ids': ids}], 'fact': structured}
