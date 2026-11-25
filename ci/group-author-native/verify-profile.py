"""Retarget only the immutable accepted134 worker's exact source identity."""
from hashlib import sha256
from importlib.util import module_from_spec,spec_from_file_location
import json,os,re
from pathlib import Path
SOURCE='6ef69b249c573eedaea962dad530eaed0a612b24'
TREE='f1ef617e51e0d8a900d9f4bfc15a559bf676276f'
PATH=Path(__file__).resolve().parent.parent/'profile-customization-native/verify.py'
SHA='e31c7120f04467c38205e8cabcf31f7457ffaa8cef538220573a562b2e6ad3cb'
def load():
    if not(PATH.is_file() and not PATH.is_symlink() and os.statvfs(PATH).f_flag&os.ST_RDONLY and sha256(PATH.read_bytes()).hexdigest()==SHA):
        raise RuntimeError('exact_readonly_original134_worker_required')
    spec=spec_from_file_location('group_current_original_profile134',PATH);module=module_from_spec(spec);spec.loader.exec_module(module)
    module.COMMIT,module.TREE=SOURCE,TREE
    return module
if __name__=='__main__':
    module=None
    try:
        module=load();module.run()
    except Exception as error:
        state=getattr(getattr(error,'orig',error),'pgcode',None)
        name=type(error).__name__
        reason=error.args[0] if module is not None and isinstance(error,module.Failure) else 'external_failure'
        print('PROFILE_CUSTOMIZATION_NATIVE_FAILURE '+json.dumps({'stage':module.STAGE if module else 'source',
          'error_type':name if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,62}',name) else 'UnhandledError',
          'reason':reason,'sqlstate':state if isinstance(state,str) and re.fullmatch(r'[0-9A-Z]{5}',state) else 'unavailable'},sort_keys=True),flush=True)
        raise SystemExit(1) from None
