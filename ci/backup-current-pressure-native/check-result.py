"""Retain exact Stage1/13ff checks, add independent fixture coverage/reserves."""
from contextlib import redirect_stdout
from hashlib import sha256
from importlib.util import module_from_spec,spec_from_file_location
from io import StringIO
import json
from pathlib import Path
import re
import sys

HERE=Path(__file__).resolve().parent
OLD=HERE.parent/'backup-capacity-1g-native/check-result.py'
OLD_SHA='b6ec0f03645e517cb6e0d2963bb3ef898d08a46769cdade05242b8c6a3b6298b'
PLAN=json.loads((HERE/'PLAN.json').read_bytes())


def require(value):
    if not value:raise ValueError('invalid_current_pressure_proof')


def pairs(items):
    result={}
    for key,value in items:
        require(key not in result);result[key]=value
    return result


def decode(raw):
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))


def profile(raw):
    require(type(raw) is bytes and 0<len(raw)<=131072 and b'\0' not in raw)
    values=[]
    for line in raw.decode('utf-8').splitlines():
        if line.startswith('BACKUP_CAPACITY_CURRENT_PROFILE '):
            require(len(line.encode())<=32768)
            values.append(decode(line.split(' ',1)[1]))
    require(len(values)==1)
    value=values[0]
    expected={'status','public','storage','public_row_bytes','public_map_bytes',
        'selected_raw_row_reserve_bytes','selected_headroom_bytes','measured_public_summary_sha256',
        'measured_storage_summary_sha256','numeric_shape_coverage_only','production_capture_authority'}
    # Explicit shape rather than permissive unknown numeric fields.
    require(type(value) is dict and set(value)==expected)
    require(value['status']=='PASS' and value['numeric_shape_coverage_only'] is True
        and value['production_capture_authority'] is False)
    require(value['measured_public_summary_sha256']==PLAN['measured']['public_summary']['sha256']
        and value['measured_storage_summary_sha256']==PLAN['measured']['storage_summary']['sha256'])
    require(type(value['selected_raw_row_reserve_bytes']) is int and value['selected_raw_row_reserve_bytes']==8388608
        and type(value['selected_headroom_bytes']) is int and value['selected_headroom_bytes']==134217728)
    for key,items in (('public',PLAN['pressure_profile']),('storage',PLAN['storage_profile'])):
        require(type(value[key]) is list and len(value[key])==len(items))
        for row,item in zip(value[key],items):
            require(type(row) is list and len(row)==11 and all(type(n) is int and 0<=n<=10**12 for n in row))
            n,s,q,m,mq,*bins=row
            require(n<=1000000 and s<=67108864 and sum(bins)==n)
            if n:
                require(2<=m<=s<=n*m and 4<=mq<=q<=n*mq and s+2*n<=q<=6*s+2*n and m+2<=mq<=6*m+2)
                require(s>=m+2*(n-1) and q>=mq+4*(n-1))
                highest=max(i for i,count in enumerate(bins) if count)
                limits=(0,1024,4096,16384,65536,262144,67108864)
                require(limits[highest]<m<=limits[highest+1])
            else:require(s==q==m==mq==0)
            require(all(a>=b for a,b in zip(row,item['minimum'])))
    public=value['public'];storage=value['storage']
    require(sum(row[1] for row in public)==value['public_row_bytes']<=67108864)
    require(type(value['public_row_bytes']) is int and type(value['public_map_bytes']) is int)
    map_bytes=sum(len(json.dumps(name).encode()) for name in PLAN['tables'])+sum(row[2]+2 if row[0] else 4 for row in public)+4*99
    require(value['public_map_bytes']==map_bytes)
    require(public[PLAN['tables'].index('trade_contacts')][:2]==[5633,9317650]
        and public[PLAN['tables'].index('trade_contact_sources')][:2]==[5640,8743189])
    require(public[PLAN['tables'].index('bookings')][0:2]==[256,8388608])
    require(public[PLAN['tables'].index('listing_floor_plans')][0]==2)
    require(storage[0][0]==34315 and storage[1][0]==2)
    require(sum(row[1] for row in storage)<=67108864)
    return value


def legacy(args):
    require(OLD.is_file() and not OLD.is_symlink() and sha256(OLD.read_bytes()).hexdigest()==OLD_SHA)
    spec=spec_from_file_location('immutable_capacity_stage1_checker',OLD)
    module=module_from_spec(spec);spec.loader.exec_module(module)
    previous=sys.argv[:];output=StringIO()
    try:
        sys.argv=[str(OLD),*args]
        with redirect_stdout(output):code=module.main()
    finally:sys.argv=previous
    return code,module,output.getvalue()


def final_gate(value,output):
    rows=[decode(line.split(' ',1)[1]) for line in output.splitlines() if line.startswith('BACKUP_CAPACITY_1G_NATIVE_HEADROOM ')]
    require(len(rows)==1 and type(rows[0]['headroom_bytes']) is int and rows[0]['headroom_bytes']>=134217728)
    native=[decode(line.split(' ',1)[1]) for line in output.splitlines() if line.startswith('BACKUP_TRANSPORT_JSON_NATIVE_RESULT ')]
    require(len(native)==1 and type(native[0]['plaintext_bytes']) is int and native[0]['plaintext_bytes']>=value['public_map_bytes'])
    return rows[0]


def main():
    args=sys.argv[1:]
    require(args and (len(args)>1 and args[1]=='stress' if args[0].startswith('--') else args[0]=='stress'))
    code,module,output=legacy(args)
    if args[0].startswith('--'):
        print(output,end='');return code
    if code!=0:
        module.failure_only(output)
        return 1
    require(len(args)==10)
    value=profile(Path(args[1]).read_bytes())
    headroom=final_gate(value,output)
    # Only now export old buffered success; profile/headroom refusal can never
    # leave a misleading original PASS in the public primary.
    print(output,end='')
    print('BACKUP_CAPACITY_CURRENT_PROFILE '+json.dumps(value,sort_keys=True,separators=(',',':')))
    print('BACKUP_CAPACITY_CURRENT_RESULT '+json.dumps({'status':'PASS','phase':'stress','tables':99,
        'observed_public_row_bytes':value['public_row_bytes'],'observed_public_map_bytes':value['public_map_bytes'],
        'measured_row_bytes':33677713,'measured_map_bytes':35942456,'selected_raw_row_reserve_bytes':8388608,
        'selected_required_headroom_bytes':134217728,'observed_headroom_bytes':headroom['headroom_bytes'],
        'all_numeric_shape_minima_covered':True,'extra_binary_sentinel_rows':2,
        'same_provider_RAM_or_production_safe_claim':False,'production_capture_authority':False},sort_keys=True,separators=(',',':')))
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception:
        print('BACKUP_CAPACITY_CURRENT_REFUSED {"reason":"invalid_or_failed_current_pressure_proof"}')
        raise SystemExit(1) from None
