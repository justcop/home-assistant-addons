"""Snapshot accounting. Currency inputs are decimal strings; calculations use Decimal."""
from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, ROUND_DOWN
import uuid
import re

GROUPS = ('Stocks', 'P2P', 'Cash', 'Crypto')
TYPES = GROUPS + ('Mortgage', 'Credit card', 'Tax liability', 'Receivable')
WRAPPERS = ('None', 'ISA', 'SIPP', 'Lifetime ISA')
ACCESS = ('Accessible', 'Withdrawal dependent', 'Restricted', 'Repayment dependent', 'Liability')
FLOW_FIELDS = ('contribution', 'withdrawal', 'relief', 'interest', 'capital')
ADJUSTMENTS = ('mortgage_interest', 'pension_refund', 'excluded_payments', 'capital_change')
ZERO = Decimal('0')


def money(value):
    if value is None or value == '':
        return ZERO
    if isinstance(value, bool):
        raise ValueError('Enter a monetary amount, not true/false.')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError('Invalid monetary amount.') from None
    if not result.is_finite() or abs(result) > Decimal('1000000000000'):
        raise ValueError('Amount is outside the supported range.')
    return result.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)


def number(value):
    return float(value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP))


def blank_state():
    return {'schema_version': 1, 'accounts': [], 'snapshots': [], 'valuations': [],
            'income_sources': ['Salary', 'Other income'],
            'source_notes': []}


def validate_state(state):
    if not isinstance(state, dict) or state.get('schema_version') != 1:
        raise ValueError('This is not a supported Money Locations export.')
    for key in ('accounts', 'snapshots', 'valuations', 'income_sources', 'source_notes'):
        if not isinstance(state.get(key), list):
            raise ValueError('Missing or invalid ' + key)
    ids = set()
    for a in state['accounts']:
        if not isinstance(a.get('id'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', a['id']) or a['id'] in ids:
            raise ValueError('Account IDs must be unique.')
        ids.add(a['id'])
        if a.get('type') not in TYPES or not isinstance(a.get('name'), str) or not a['name'].strip():
            raise ValueError('Each account needs a name and supported type.')
        if a.get('wrapper', 'None') not in WRAPPERS:
            raise ValueError('Unsupported account wrapper.')
        if a.get('access', 'Accessible') not in ACCESS:
            raise ValueError('Unsupported account access classification.')
        if not isinstance(a.get('active'), bool):
            raise ValueError('Account active flag must be true/false.')
    seen_ids, final_dates = set(), set()
    for s in state['snapshots']:
        if not isinstance(s.get('id'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', s['id']) or s['id'] in seen_ids:
            raise ValueError('Snapshot IDs must be unique.')
        seen_ids.add(s['id'])
        date.fromisoformat(s['date'])
        if s.get('status') not in ('draft', 'final'):
            raise ValueError('Invalid snapshot status.')
        if s['status'] == 'final':
            if s['date'] in final_dates:
                raise ValueError('Only one final snapshot is allowed on a date.')
            final_dates.add(s['date'])
        if not isinstance(s.get('balances'), dict) or not isinstance(s.get('income'), dict):
            raise ValueError('Invalid snapshot balances or income.')
        for aid, b in s['balances'].items():
            if aid not in ids or not isinstance(b, dict):
                raise ValueError('Unknown account in snapshot.')
            for field in ('amount',) + FLOW_FIELDS:
                value = money(b.get(field))
                if field in ('contribution', 'withdrawal', 'relief', 'interest') and value < 0:
                    raise ValueError(field + ' must be zero or positive.')
            a = next(a for a in state['accounts'] if a['id'] == aid)
            if money(b.get('relief')) and a.get('wrapper') not in ('SIPP', 'Lifetime ISA'):
                raise ValueError('Account tax relief/bonus belongs on a SIPP or Lifetime ISA.')
        for v in s['income'].values():
            money(v)
        for field in ADJUSTMENTS:
            value = money(s.get(field))
            if field != 'capital_change' and value < 0:
                raise ValueError(field + ' must be zero or positive.')
        required = s.get('required_accounts', [])
        if not isinstance(required, list) or any(a not in ids for a in required):
            raise ValueError('Invalid required accounts.')
        if 'legacy' in s:
            for group, v in s['legacy'].get('group_contributions', {}).items():
                if group not in GROUPS:
                    raise ValueError('Invalid historical investment group.')
                money(v)
    for v in state['valuations']:
        date.fromisoformat(v['date'])
        if money(v['value']) < 0:
            raise ValueError('Home valuation must be positive.')
    if len({v['date'] for v in state['valuations']}) != len(state['valuations']):
        raise ValueError('One home valuation is allowed per date.')
    return state


def previous_snapshot(state, snapshot):
    eligible = [s for s in state['snapshots'] if s['status'] == 'final' and s['date'] < snapshot['date']]
    return max(eligible, key=lambda s: s['date']) if eligible else None


def new_snapshot(state, day):
    date.fromisoformat(day)
    s = {'id': uuid.uuid4().hex, 'date': day, 'status': 'draft', 'balances': {},
         'income': {k: '' for k in state['income_sources']}, 'notes': '', 'activity_complete': False,
         'required_accounts': [a['id'] for a in state['accounts'] if a['active']]}
    prev = previous_snapshot(state, s)
    if prev:
        for aid, b in prev['balances'].items():
            if money(b.get('amount')) and aid not in s['required_accounts']:
                s['required_accounts'].append(aid)
    for aid in s['required_accounts']:
        s['balances'][aid] = {'amount': '', 'confirmed': False, **{f: '' for f in FLOW_FIELDS}}
    s.update({k: '' for k in ADJUSTMENTS})
    return s


def problems(state, s):
    issues = []
    accounts = {a['id']: a for a in state['accounts']}
    prev = previous_snapshot(state, s)
    if not state['accounts']:
        issues.append('Add or import accounts first.')
    for aid in s.get('required_accounts', []):
        b = s['balances'].get(aid, {})
        if b.get('amount') in (None, ''):
            issues.append(accounts[aid]['name'] + ': balance is missing.')
        elif not b.get('confirmed'):
            issues.append(accounts[aid]['name'] + ': confirm this balance.')
    if prev:
        for aid, b in prev['balances'].items():
            if money(b.get('amount')) and s['balances'].get(aid, {}).get('amount') in (None, ''):
                issues.append(accounts[aid]['name'] + ': previous non-zero balance needs a new balance or explicit zero.')
    if not s.get('activity_complete'):
        issues.append('Confirm that income, flows and adjustments cover the whole snapshot period.')
    return issues


def report(state, s):
    accounts = {a['id']: a for a in state['accounts']}
    prev = previous_snapshot(state, s)
    period_days = (date.fromisoformat(s['date']) - date.fromisoformat(prev['date'])).days if prev else None
    old = prev['balances'] if prev else {}
    current = s['balances']
    amount = lambda rows, aid: money(rows.get(aid, {}).get('amount'))
    total = sum((amount(current, aid) for aid in accounts), ZERO)
    prior_total = sum((amount(old, aid) for aid in accounts), ZERO)
    totals = {g: sum((amount(current, aid) for aid, a in accounts.items() if a['type'] == g), ZERO) for g in TYPES}
    eligible = sum((max(amount(current, aid), ZERO) for aid, a in accounts.items() if a['type'] in ('Stocks', 'P2P')), ZERO)
    cost = money(s.get('mortgage_interest'))
    allocated = ZERO
    allocation = {}
    if eligible:
        recipients = [(aid, max(amount(current, aid), ZERO)) for aid, a in accounts.items()
                      if a['type'] in ('Stocks', 'P2P') and amount(current, aid) > 0]
        exact = {aid: cost * balance / eligible for aid, balance in recipients}
        allocation = {aid: value.quantize(Decimal('.01'), rounding=ROUND_DOWN) for aid, value in exact.items()}
        pennies = int((cost - sum(allocation.values(), ZERO)) * 100)
        ranked = sorted(exact, key=lambda aid: exact[aid] - allocation[aid], reverse=True)
        for aid in ranked[:pennies]:
            allocation[aid] += Decimal('.01')
    rows = []
    groups = {g: {'gross': ZERO, 'cost': ZERO, 'net': ZERO, 'balance': totals[g]} for g in GROUPS}
    relief = ZERO
    account_capital = ZERO
    legacy = s.get('legacy')
    warnings = []
    if legacy:
        warnings.append('Imported estimates: historical flows are recorded by category; account-level returns are unavailable.')
    if not s.get('activity_complete'):
        warnings.append('Activity is incomplete; savings, spending and returns are provisional.')
    for aid, a in accounts.items():
        b = current.get(aid, {})
        bal, prior = amount(current, aid), amount(old, aid)
        bonus, capital = money(b.get('relief')), money(b.get('capital'))
        relief += bonus
        account_capital += capital
        if aid not in current and aid not in old:
            continue
        group = a['type']
        share = allocation.get(aid, ZERO)
        allocated += share
        gross = None
        if group in GROUPS and prev:
            if group == 'Cash':
                gross = money(b.get('interest'))
            else:
                gross = bal - prior - money(b.get('contribution')) + money(b.get('withdrawal')) - bonus - capital
            groups[group]['gross'] += gross
            groups[group]['cost'] += share
            if legacy:
                gross = None
        return_pct = None
        if gross is not None and prev and prior > 0:
            return_pct = gross * Decimal('100') / prior
            if not legacy and group in ('Stocks', 'P2P', 'Crypto') and period_days:
                threshold = max(Decimal('10'), Decimal('20') * Decimal(period_days) / Decimal('30'))
                if abs(return_pct) > threshold:
                    warnings.append(f"{a['name']}: inferred return is {number(return_pct)}% of opening balance over {period_days} days; check contributions and withdrawals.")
        rows.append({'id': aid, 'name': a['name'], 'type': group, 'wrapper': a.get('wrapper', 'None'),
                     'balance': number(bal) if b.get('amount') not in (None, '') else None,
                     'previous': number(prior), 'change': number(bal - prior) if prev else None,
                     'gross': number(gross) if gross is not None else None,
                     'cost': number(share), 'net': number(gross-share) if gross is not None else None,
                     'return_pct': number(return_pct) if return_pct is not None else None,
                     'relief': number(bonus)})
    if legacy and prev:
        for g in GROUPS:
            prior_group = sum((amount(old, aid) for aid,a in accounts.items() if a['type']==g), ZERO)
            bonuses = sum((money(current.get(aid,{}).get('relief')) for aid,a in accounts.items() if a['type']==g), ZERO)
            capital = sum((money(current.get(aid,{}).get('capital')) for aid,a in accounts.items() if a['type']==g), ZERO)
            if g == 'Cash':
                groups[g]['gross'] = money(legacy.get('cash_interest'))
            else:
                groups[g]['gross'] = totals[g] - prior_group - money(legacy.get('group_contributions',{}).get(g)) - bonuses - capital
    for g in GROUPS:
        groups[g]['net'] = groups[g]['gross'] - groups[g]['cost']
        prior_group = sum((amount(old, aid) for aid, a in accounts.items() if a['type'] == g), ZERO)
        groups[g]['return_pct'] = groups[g]['gross'] * Decimal('100') / prior_group if prev and prior_group > 0 else None
    unallocated = cost - allocated
    if cost and not eligible:
        warnings.append('No closing stocks/P2P balance: mortgage interest is shown as an unallocated investment cost.')
    gross_total = sum((g['gross'] for g in groups.values()), ZERO)
    net_return = gross_total - cost
    tax_benefits = relief + money(s.get('pension_refund'))
    capital = account_capital + money(s.get('capital_change'))
    delta = total - prior_total
    savings = delta - net_return - tax_benefits - capital
    income = sum((money(v) for v in s['income'].values()), ZERO)
    excluded = money(s.get('excluded_payments'))
    adjusted_spending = income - excluded - savings
    if prev and adjusted_spending < 0:
        warnings.append('Inferred spending is negative; check income, contributions, withdrawals and exceptional adjustments for missing entries.')
    mortgage_principal = totals['Mortgage'] - sum((amount(old,aid) for aid,a in accounts.items() if a['type']=='Mortgage'), ZERO)
    mortgage_principal -= sum((money(current.get(aid,{}).get('capital')) for aid,a in accounts.items() if a['type']=='Mortgage'), ZERO)
    valuation = max((v for v in state['valuations'] if v['date'] <= s['date']), key=lambda v:v['date'], default=None)
    old_valuation = max((v for v in state['valuations'] if prev and v['date'] <= prev['date']), key=lambda v:v['date'], default=None)
    property_change = money(valuation['value']) - money(old_valuation['value']) if valuation and old_valuation else None
    accessible = sum((max(amount(current,aid),ZERO) for aid,a in accounts.items() if a.get('access') in ('Accessible','Withdrawal dependent')), ZERO)
    restricted = sum((amount(current,aid) for aid,a in accounts.items() if a.get('access')=='Restricted'), ZERO)
    isa = sum((amount(current,aid) for aid,a in accounts.items() if a.get('wrapper') in ('ISA','Lifetime ISA')), ZERO)
    result = {'id':s['id'], 'date':s['date'], 'previous_date':prev['date'] if prev else None,
              'days':period_days,
              'status':s['status'], 'net_worth':number(total), 'accessible':number(accessible),
              'restricted':number(restricted), 'isa_total':number(isa), 'totals':{g:number(v) for g,v in totals.items()},
              'income':number(income), 'adjusted_income':number(income-excluded),
              'excluded_payments':number(excluded), 'tax_benefits':number(tax_benefits),
              'capital_change':number(capital), 'mortgage_interest':number(cost), 'unallocated_cost':number(unallocated),
              'accounts':rows, 'groups':{g:{k:(number(v) if v is not None else None) for k,v in d.items()} for g,d in groups.items()},
              'warnings':warnings, 'issues':problems(state,s), 'legacy':bool(legacy),
              'home_value':number(money(valuation['value'])) if valuation else None,
              'home_value_date':valuation['date'] if valuation else None,
              'with_home':number(total+money(valuation['value'])) if valuation else None,
              'property_change':number(property_change) if property_change is not None else None}
    for k,v in {'balance_change':delta,'gross_return':gross_total,'net_return':net_return,
                'savings':savings,'spending':income-savings,'adjusted_spending':adjusted_spending,
                'mortgage_principal':mortgage_principal,'other_savings':savings-mortgage_principal}.items():
        result[k] = number(v) if prev else None
    if prev and period_days:
        factor = Decimal('30') / Decimal(period_days)
        result['savings_30d'] = number(savings * factor)
        result['net_return_30d'] = number(net_return * factor)
        result['adjusted_spending_30d'] = number(adjusted_spending * factor)
    else:
        result['savings_30d'] = result['net_return_30d'] = result['adjusted_spending_30d'] = None
    return result


def all_reports(state):
    return [report(state,s) for s in sorted(state['snapshots'],key=lambda x:x['date']) if s['status']=='final']
