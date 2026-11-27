"""Fixture-data-only stress adapter around exact13ff; no capture code replaces it."""
from contextlib import closing
from hashlib import sha256
from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
import re
import sys

TOOLS = Path('/current-tools')
JSON_WORKER = Path('/new-tools/verify-native.py')
JSON_SHA = 'a835e9b41edf7e29822ecca4bb67254b7cbf11701960bb702f1efd3b5995b78f'
PLAN = json.loads((TOOLS/'PLAN.json').read_bytes())
OBSERVED = None
NATIVE = None


class PressureRefused(RuntimeError):
    pass


def require(value, reason):
    if not value:
        raise PressureRefused(reason)


def lengths(groups):
    result = []
    for group in groups:
        require(type(group) is dict and set(group)=={'rows','bytes'} and type(group['rows']) is int
            and type(group['bytes']) is int and 0<group['rows']<=1000000 and 2<=group['bytes']<=524288,
            'finite_numeric_fixture_groups_required')
        result.extend([group['bytes']]*group['rows'])
    require(len(result)<=1000000,'bounded_fixture_rows_required')
    return result


def pad(native,database,table,groups,ratio,field=None):
    """Fill one synthetic string per row; never read/import current row values."""
    values = lengths(groups)
    require(table in PLAN['tables'] or table in ('storage.objects','storage.buckets'),
            'finite_fixture_table_required')
    numerator,denominator = ratio
    require(type(numerator) is int and type(denominator) is int and 1<=denominator<=10**9
        and denominator<=numerator<=2*denominator,'finite_fixture_quote_ratio_required')
    relation = native.sql.Identifier('storage',table.split('.')[1]) if table.startswith('storage.') else native.sql.Identifier('public',table)
    if field:
        require((table,field) in {('trade_contacts','details'),('trade_contact_sources','evidence'),
             ('storage.objects','metadata'),('storage.buckets','metadata')},'finite_fixture_JSON_field_required')
        assignment = native.sql.SQL("{}=pg_catalog.jsonb_set(t.{},'{{pressure_padding}}',pg_catalog.to_jsonb(p.value))").format(
            native.sql.Identifier(field),native.sql.Identifier(field))
    else:
        assignment = native.sql.SQL('pressure_padding=p.value')
    key = native.sql.SQL('t.id') if table!='storage.objects' else native.sql.SQL("t.bucket_id||':'||t.name")
    join = native.sql.SQL('t.id=p.id') if table!='storage.objects' else native.sql.SQL("t.bucket_id||':'||t.name=p.id")
    expression = native.sql.SQL("jsonb_build_object('bucket',t.bucket_id,'name',t.name,'metadata',t.metadata,'updated_at',t.updated_at)::text") if table=='storage.objects' else native.sql.SQL('to_jsonb(t)::text')
    # Only known synthetic fixtures have been seeded here. Each row has an
    # independent bounded desired length; a single filler is at most512KiB.
    # Distinct MD5 blocks avoid one repeated compressible padding string.
    query = native.sql.SQL("""WITH desired AS (
      SELECT value::bigint AS wanted,number::bigint AS n
      FROM unnest(%s::bigint[]) WITH ORDINALITY AS d(value,number)
    ), initial AS MATERIALIZED (
      SELECT {key} AS id,row_number() OVER(ORDER BY {key}) AS n,
        octet_length({expression})::bigint AS raw0,
        octet_length(to_json({expression})::text)::bigint AS quoted0
      FROM {relation} t
    ), sized AS (
      SELECT i.*,greatest(d.wanted,i.raw0+128) AS goal
      FROM initial i JOIN desired d ON d.n=i.n
    ), layout AS (
      SELECT *,goal-raw0 AS added,
        ceil(greatest(ceil(goal::numeric*%s/%s)-quoted0-(goal-raw0),0)/2)::bigint AS quotes
      FROM sized
    ), padding AS MATERIALIZED (
      SELECT l.id,repeat(chr(34),l.quotes::integer) || coalesce((SELECT left(
        string_agg(md5(l.id::text||':'||g::text),'' ORDER BY g),(l.added-2*l.quotes)::integer)
        FROM generate_series(1,ceil((l.added-2*l.quotes)::numeric/32)::integer)g),'') AS value
      FROM layout l WHERE 0<=l.quotes AND 2*l.quotes<=l.added AND l.goal<=524288
    ) UPDATE {relation} t SET {assignment} FROM padding p WHERE {join}""").format(
        key=key,relation=relation,assignment=assignment,join=join,expression=expression)
    with closing(native.connect(database)) as db,db.cursor() as c:
        c.execute(query,(values,numerator,denominator))
        require(c.rowcount==len(values),'every_fixture_row_padded_required')
        db.commit()


def seed_public(native,database):
    for item in PLAN['pressure_profile']:
        table = item['table']
        if item['minimum'][0]==0 or table in ('alembic_version','listing_floor_plans'):
            continue
        n = sum(group['rows'] for group in item['groups'])
        relation = native.sql.Identifier('public',table)
        field = None
        with closing(native.connect(database)) as db,db.cursor() as c:
            if table=='trade_contacts':
                c.execute("UPDATE public.trade_contacts SET details=jsonb_build_object('synthetic',true,'pressure_padding','')")
                field='details'
            elif table=='trade_contact_sources':
                c.execute("UPDATE public.trade_contact_sources SET evidence=jsonb_build_object('synthetic',true,'pressure_padding','')")
                field='evidence'
            else:
                c.execute(native.sql.SQL("ALTER TABLE {} ADD COLUMN pressure_padding text NOT NULL DEFAULT ''").format(relation))
                c.execute(native.sql.SQL('TRUNCATE TABLE {}').format(relation))
                if table=='professional_profiles':
                    c.execute("INSERT INTO public.professional_profiles SELECT g,NULL,NULL,'hidden','' FROM generate_series(1,%s)g",(n,))
                elif table=='activity_log':
                    c.execute('INSERT INTO public.activity_log SELECT g,\'\' FROM generate_series(1,%s)g',(n,))
                else:
                    c.execute(native.sql.SQL('INSERT INTO {} SELECT g,NULL,NULL,NULL,\'\' FROM generate_series(1,%s)g').format(relation),(n,))
            db.commit()
        pad(native,database,table,item['groups'],item['quote_ratio'],field)
    # This declared8MiB raw-row reserve is synthetic stress, not a measurement
    # of catalog/maps/envelope/allocator or provider background memory.
    reserve = PLAN['selected_budgets']['synthetic_raw_row_reserve']
    require(reserve=={'table':'bookings','rows':256,'row_bytes':32768,'bytes':8388608},
            'exact_selected_reserve_required')
    with closing(native.connect(database)) as db,db.cursor() as c:
        c.execute("ALTER TABLE public.bookings ADD COLUMN pressure_padding text NOT NULL DEFAULT ''")
        c.execute('TRUNCATE TABLE public.bookings')
        c.execute("INSERT INTO public.bookings SELECT g,NULL,NULL,NULL,'' FROM generate_series(1,256)g")
        db.commit()
    pad(native,database,'bookings',[{'rows':256,'bytes':32768}],[1,1])


def seed_inventory(native,database):
    with closing(native.connect(database)) as db,db.cursor() as c:
        c.execute("UPDATE storage.objects SET metadata=jsonb_build_object('mimetype','image/webp','synthetic',true,'pressure_padding','')")
        c.execute("UPDATE storage.buckets SET metadata=jsonb_build_object('synthetic',true,'pressure_padding','')")
        db.commit()
    for item in PLAN['storage_profile']:
        pad(native,database,item['table'],item['groups'],item['quote_ratio'],'metadata')


def measure(native,database,table,expression=None):
    relation = native.sql.Identifier('storage',table.split('.')[1]) if table.startswith('storage.') else native.sql.Identifier('public',table)
    expression = expression or native.sql.SQL('to_jsonb(t)::text')
    query = native.sql.SQL("""WITH sizes AS MATERIALIZED (
      SELECT octet_length({expr})::bigint AS s,octet_length(to_json({expr})::text)::bigint AS q FROM {relation} t
    ) SELECT count(*)::bigint,coalesce(sum(s),0)::bigint,coalesce(sum(q),0)::bigint,
      coalesce(max(s),0)::bigint,coalesce(max(q),0)::bigint,
      count(*) FILTER(WHERE s<=1024)::bigint,count(*) FILTER(WHERE s>1024 AND s<=4096)::bigint,
      count(*) FILTER(WHERE s>4096 AND s<=16384)::bigint,count(*) FILTER(WHERE s>16384 AND s<=65536)::bigint,
      count(*) FILTER(WHERE s>65536 AND s<=262144)::bigint,count(*) FILTER(WHERE s>262144)::bigint FROM sizes""").format(
        expr=expression,relation=relation)
    value = native.rows(database,query)[0]
    require(len(value)==11 and all(type(v) is int and 0<=v<=10**12 for v in value),
            'finite_fixture_numeric_measurement_required')
    return list(value)


def record_profile(native,database):
    global OBSERVED
    require(OBSERVED is None,'single_current_profile_required')
    public=[]
    for item in PLAN['pressure_profile']:
        value=measure(native,database,item['table'])
        want=item['minimum']
        require(len(want)==11 and all(actual>=minimum for actual,minimum in zip(value,want)),
                'every_measured_table_shape_covered_required')
        public.append(value)
    storage=[]
    for item in PLAN['storage_profile']:
        expr = native.sql.SQL("jsonb_build_object('bucket',t.bucket_id,'name',t.name,'metadata',t.metadata,'updated_at',t.updated_at)::text") if item['table']=='storage.objects' else None
        value=measure(native,database,item['table'],expr)
        require(all(actual>=minimum for actual,minimum in zip(value,item['minimum'])),
                'every_measured_storage_shape_covered_required')
        storage.append(value)
    raw_total=sum(v[1] for v in public)
    map_bytes=sum(len(json.dumps(name).encode()) for name in PLAN['tables'])+sum(v[2]+2 if v[0] else 4 for v in public)+4*99
    require(raw_total<=67108864 and public[PLAN['tables'].index('bookings')][1]==8388608,
            'bounded_exact_selected_row_reserve_required')
    require(native.rows(database,'SELECT count(*),sum(octet_length(to_jsonb(t)::text)) FROM public.trade_contacts t')==[(5633,9317650)]
        and native.rows(database,'SELECT count(*),sum(octet_length(to_jsonb(t)::text)) FROM public.trade_contact_sources t')==[(5640,8743189)],
        'inherited_contact_source_exact_row_SUM_required')
    OBSERVED={'status':'PASS','public':public,'storage':storage,'public_row_bytes':raw_total,
        'public_map_bytes':map_bytes,'selected_raw_row_reserve_bytes':8388608,'selected_headroom_bytes':134217728,
        'measured_public_summary_sha256':PLAN['measured']['public_summary']['sha256'],
        'measured_storage_summary_sha256':PLAN['measured']['storage_summary']['sha256'],
        'numeric_shape_coverage_only':True,'production_capture_authority':False}
    print('BACKUP_CAPACITY_CURRENT_PROFILE '+json.dumps(OBSERVED,separators=(',',':'),sort_keys=True),flush=True)


def load_worker():
    require(JSON_WORKER.is_file() and not JSON_WORKER.is_symlink()
        and os.statvfs(JSON_WORKER).f_flag & os.ST_RDONLY and sha256(JSON_WORKER.read_bytes()).hexdigest()==JSON_SHA,
        'exact_readonly_json_worker_required')
    spec=spec_from_file_location('immutable_current_pressure_json',JSON_WORKER)
    module=module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    original_load=module.load_v5
    def load_v5():
        global NATIVE
        native=original_load();NATIVE=native
        original_trade,original_storage=native.seed_trade,native.seed_storage
        def trade(database,contacts,sources,targets=None):
            original_trade(database,contacts,sources,targets)
            if (contacts,sources)==(5633,5640):
                require(targets==(9317650,8743189),'unchanged_inherited_trade_targets_required')
                seed_public(native,database)
        def storage(database,count):
            original_storage(database,count)
            if count==34315:
                seed_inventory(native,database);record_profile(native,database)
        native.seed_trade,native.seed_storage=trade,storage
        return native
    module.load_v5=load_v5
    return module


if __name__=='__main__':
    module=None
    try:
        require(len(sys.argv)==2 and sys.argv[1]=='stress','stress_only_current_profile_required')
        require(TOOLS.resolve(strict=True)==TOOLS and os.statvfs(TOOLS).f_flag & os.ST_RDONLY,
                'readonly_current_fixture_tools_required')
        module=load_worker()
        result=module.run('stress')
        require(OBSERVED is not None and result['plaintext_bytes']>=OBSERVED['public_map_bytes'],
                'full_snapshot_covers_own_synthetic_map_required')
    except Exception as error:
        if NATIVE is not None:NATIVE.capture_sqlstate(error)
        kind=type(error).__name__
        result={'status':'FAIL','phase':'stress','stage':NATIVE.stage if NATIVE is not None else 'isolation',
            'sqlstate':NATIVE.first_sqlstate if NATIVE is not None else 'unavailable',
            'error_type':kind if re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,62}',kind) else 'UnhandledError',
            'reason':str(error) if isinstance(error,(PressureRefused,
                module.Refused if module is not None else PressureRefused,
                NATIVE.Refused if NATIVE is not None else PressureRefused))
                and re.fullmatch('[a-z_0-9]{1,90}',str(error)) else 'unexpected_failure'}
    print('BACKUP_TRANSPORT_JSON_NATIVE_RESULT '+json.dumps(result,sort_keys=True,separators=(',',':')),flush=True)
    raise SystemExit(0 if result['status']=='PASS' else 1)
