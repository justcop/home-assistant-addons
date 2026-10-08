"""Convert bounded connector JSON reads into a private Money Locations import.

Usage: python tools/import_sheet_reads.py PRIVATE_SOURCE_DIRECTORY OUTPUT.json
Expected files: accounts.json, history.json, activity.json, oldvalues.json,
oldsummary.json, olddata.json. Each has the Sheets values response object.
Never commit the source directory or generated import: it contains personal data.
"""
import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from model import blank_state, validate_state, report, money, all_reports


def migrate(source):
    source=Path(source)
    read=lambda name:json.loads((source/(name+'.json')).read_text())['values']
    accounts,history,activity,original=map(read,('accounts','history','activity','oldvalues'))
    state=blank_state()
    activity_header=activity[0] if activity else []
    def source_label(index, fallback):
        value=activity_header[index] if len(activity_header)>index else ''
        return str(value).strip() or fallback
    salary_source=source_label(1,'Salary')
    rental_source=source_label(2,'Rental income')
    other_source=source_label(3,'Other income')
    state['income_sources']=[salary_source,rental_source,other_source]
    for row in accounts[1:]:
        row=row+['']*(9-len(row))
        aid,name,holder,kind,wrapper,access,active,notes,code=row
        state['accounts'].append({'id':aid,'name':name,'holder':holder,'type':kind,'wrapper':wrapper,
                                  'access':access,'active':active=='Yes','notes':notes,'legacy_code':code})
    snapshots={}
    for row in history[1:]:
        serial,aid,balance=row[:3]
        day=(date(1899,12,30)+timedelta(days=serial)).isoformat()
        s=snapshots.setdefault(serial,{'id':'import-'+day,'date':day,'status':'final','balances':{},
            'income':{},'required_accounts':[],'activity_complete':False,'notes':'Imported from Money Locations.'})
        s['balances'][aid]={'amount':str(money(balance)) if balance not in (None,'') else None,
                            'confirmed':balance not in (None,''),**{k:None for k in ('contribution','withdrawal','relief','interest','capital')}}
        if balance not in (None,''):
            s['required_accounts'].append(aid)
    for row in activity[1:]:
        row=row+[None]*(16-len(row))
        serial,salary,rental,other,stocks,p2p,transfer,notes,excluded,cash,crypto,capital,complete,relief,refund,sipp=row
        s=snapshots[serial]
        s['income']={salary_source:salary,rental_source:rental,other_source:other}
        s.update(excluded_payments=excluded,pension_refund=refund,capital_change=capital)
        s['legacy']={'group_contributions':{'Stocks':stocks,'P2P':p2p,'Crypto':crypto},
                     'cash_interest':cash,'transfer_adjustment':transfer,'personal_sipp_contribution':sipp}
        if relief not in (None,''):
            sipps=[a for a in state['accounts'] if a['wrapper']=='SIPP']
            if len(sipps)!=1:
                raise ValueError('Cannot assign historic SIPP relief unambiguously.')
            s['balances'][sipps[0]['id']]['relief']=relief
    ordered=sorted(snapshots.items())
    if len(ordered)!=len(original[0])-1:
        raise ValueError('Source snapshot counts differ.')
    for index,(_,s) in enumerate(ordered,1):
        s['mortgage_interest']=str(-money(original[19][index]))
        if money(s['mortgage_interest'])<0:
            raise ValueError('Unexpected negative historical interest cost.')
        s['legacy']['original_summary']={str(i+1):row[index] if len(row)>index else None for i,row in enumerate(original)}
        state['snapshots'].append(s)
    state['source_notes']=[
        f'Imported {len(ordered)} snapshots from the organised workbook, reconciled to the corrected original Summary totals.',
        'Original interest allocation uses closing stocks and P2P balances, excluding cash. Recent stock-profit cells omit their cost allocation; the app applies it consistently.',
        'Historical contribution amounts are category totals. Relief, cash interest and exceptional changes are incomplete, so historical returns and savings remain provisional.',
        'Former account holders’ accounts are inactive; historical balances remain part of the record.',
        'Any inferred account classifications are described in the account notes; review those before entering new data.',
        'Legacy transfer adjustments and original displayed summary values are preserved for audit, not silently converted into capital changes.'
    ]
    validate_state(state)
    results=all_reports(state)
    for index,r in enumerate(results,1):
        expected=money(original[2][index])
        if money(r['net_worth'])!=expected:
            raise ValueError(f"Snapshot {r['date']}: total does not reconcile ({r['net_worth']} versus {expected}).")
    return state,results


if __name__=='__main__':
    state,reports=migrate(sys.argv[1])
    Path(sys.argv[2]).write_text(json.dumps(state,indent=2,ensure_ascii=False))
    print(f"Reconciled {len(reports)} snapshots, {len(state['accounts'])} accounts.")
    print('Latest:',{k:reports[-1][k] for k in ('date','net_worth','net_return','savings','adjusted_spending')})
