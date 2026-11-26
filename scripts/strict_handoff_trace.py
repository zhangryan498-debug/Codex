"""Strict, local, case-bound leaf evidence. No I/O, fuzzy matching or permission claims."""
from dataclasses import dataclass
from hashlib import sha256
import json
import math
import re
from urllib.parse import unquote

VERSION='strict-leaf-trace/1.0.1'

class TraceError(ValueError):
    pass

MISSING=object()

def parse_json(raw):
    def pairs(items):
        out={}
        for k,v in items:
            if k in out:raise TraceError('DUPLICATE_JSON_KEY')
            out[k]=v
        return out
    def invalid(value):raise TraceError('NONFINITE_JSON')
    def finite_float(raw):
        value=float(raw)
        if not math.isfinite(value):raise TraceError('NONFINITE_JSON')
        return value
    try:return json.loads(raw,object_pairs_hook=pairs,parse_constant=invalid,parse_float=finite_float)
    except TraceError:raise
    except (UnicodeError,ValueError,RecursionError) as e:raise TraceError('INVALID_JSON') from e

def canonical(data):
    return json.dumps(data,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)

def digest(data):return sha256(canonical(data).encode('utf-8')).hexdigest()

def same(a,b):
    if type(a) is not type(b):return False
    if type(a) is dict:return a.keys()==b.keys() and all(same(v,b[k]) for k,v in a.items())
    if type(a) is list:return len(a)==len(b) and all(same(x,y) for x,y in zip(a,b))
    return a==b

def tokens(pointer):
    if type(pointer) is not str or len(pointer)>2048:raise TraceError('INVALID_POINTER_TYPE_OR_LENGTH')
    if pointer.startswith('#'):
        if re.search(r'%(?![0-9a-fA-F]{2})',pointer):raise TraceError('INVALID_FRAGMENT_ESCAPE')
        pointer=unquote(pointer[1:],errors='strict')
    if pointer=='':return ()
    if not pointer.startswith('/'):raise TraceError('EXTERNAL_OR_INVALID_POINTER')
    out=[]
    for value in pointer[1:].split('/'):
        if re.search(r'~(?![01])',value):raise TraceError('INVALID_TOKEN_ESCAPE')
        out.append(value.replace('~1','/').replace('~0','~'))
    return tuple(out)

def encode_tokens(parts):return ''.join('/'+s.replace('~','~0').replace('/','~1') for s in parts)

def resolve(data,pointer):
    current=data
    for token in tokens(pointer):
        if type(current) is dict:
            if token not in current:return MISSING
            current=current[token]
        elif type(current) is list:
            if not re.fullmatch(r'0|[1-9][0-9]*',token):raise TraceError('INVALID_ARRAY_INDEX')
            index=int(token)
            if index>=len(current):return MISSING
            current=current[index]
        else:raise TraceError('NON_CONTAINER_PATH')
    return current

def walk_leaves(data,path=''):
    if type(data) is dict:
        for key,value in data.items():yield from walk_leaves(value,path+'/'+key.replace('~','~0').replace('/','~1'))
    elif type(data) is list:
        for i,value in enumerate(data):yield from walk_leaves(value,path+'/'+str(i))
    else:yield path,data

@dataclass(frozen=True)
class BoundDocument:
    document_id:str
    document_sha256:str
    case_id:str
    sku:str
    root_pointer:str
    canonical_json:str

    def data(self):
        value=parse_json(self.canonical_json)
        if digest(value)!=self.document_sha256:raise TraceError('DOCUMENT_HASH_MISMATCH')
        return value

def bind_document(document_id,data,case_id,sku,root_pointer=''):
    if not all(type(x) is str and x and len(x)<=200 for x in (document_id,case_id,sku)):
        raise TraceError('INVALID_DOCUMENT_BINDING')
    if type(data) not in (dict,list):raise TraceError('DOCUMENT_CONTAINER_REQUIRED')
    text=canonical(data); snapshot=parse_json(text)
    root=encode_tokens(tokens(root_pointer))
    if resolve(snapshot,root) is MISSING:raise TraceError('MISSING_SCOPE_ROOT')
    return BoundDocument(document_id,sha256(text.encode('utf-8')).hexdigest(),case_id,sku,root,text)

def reference(doc,path):
    if type(doc) is not BoundDocument:raise TraceError('BOUND_DOCUMENT_REQUIRED')
    path=encode_tokens(tokens(path)); prefix=tokens(doc.root_pointer)
    if tokens(path)[:len(prefix)]!=prefix:raise TraceError('PATH_OUTSIDE_CASE_SCOPE')
    value=resolve(doc.data(),path)
    if type(value) in (dict,list):raise TraceError('LEAF_REQUIRED_NOT_PROJECTED_PARENT')
    state='ABSENT' if value is MISSING else 'NULL' if value is None else 'VALUE'
    result={'document_id':doc.document_id,'document_sha256':doc.document_sha256,
            'case_id':doc.case_id,'sku':doc.sku,'path':path,'state':state}
    if state!='ABSENT':result['value']=value
    result['id']='ref_'+digest({k:result[k] for k in ('document_id','document_sha256','case_id','sku','path')})[:32]
    return result

def validate_leaf_reference(ref,documents,case_id,sku):
    try:
        if type(ref) is not dict:raise TraceError('REFERENCE_OBJECT_REQUIRED')
        key=ref.get('document_id');doc=documents.get(key)
        if type(doc) is not BoundDocument:raise TraceError('UNBOUND_DOCUMENT')
        if doc.case_id!=case_id or doc.sku!=sku:raise TraceError('CROSS_CASE_OR_SKU')
        actual=reference(doc,ref.get('path'))
        if not same(actual,ref):raise TraceError('REFERENCE_VALUE_TYPE_STATE_OR_BINDING_MISMATCH')
        return {'valid':True,'resolved':actual,'errors':[]}
    except (TraceError,ValueError,TypeError,KeyError) as error:
        return {'valid':False,'resolved':None,'errors':[str(error)]}

def build_catalog(documents,required_paths=None):
    result={}
    for document_id,doc in documents.items():
        if document_id!=doc.document_id:raise TraceError('DOCUMENT_ID_MISMATCH')
        prefix=tokens(doc.root_pointer)
        paths=[p for p,v in walk_leaves(doc.data()) if tokens(p)[:len(prefix)]==prefix]
        paths.extend((required_paths or {}).get(document_id,[]))
        for path in paths:
            ref=reference(doc,path)
            if ref['id'] in result and not same(ref,result[ref['id']]):raise TraceError('REFERENCE_ID_COLLISION')
            result[ref['id']]=ref
    return result

def validate_reference_ids(ids,catalog,documents,case_id,sku,required_ids=()):
    errors=[];resolved=[]
    if type(ids) is not list or len(ids)>512 or any(type(x) is not str for x in ids):
        return {'valid':False,'errors':['REFERENCE_ID_LIST_REQUIRED'],'resolved':[]}
    if len(ids)!=len(set(ids)):errors.append('DUPLICATE_REFERENCE_ID')
    for key in ids:
        if key not in catalog:errors.append('UNKNOWN_REFERENCE_ID:'+key);continue
        verdict=validate_leaf_reference(catalog[key],documents,case_id,sku)
        errors.extend(verdict['errors'])
        if verdict['valid']:resolved.append(verdict['resolved'])
    missing=set(required_ids)-set(ids)
    if missing:errors.append('REQUIRED_REFERENCE_IDS_MISSING:'+','.join(sorted(missing)))
    return {'valid':not errors,'errors':errors,'resolved':resolved,
            'attribution':'host_resolved_from_frozen_documents_not_model_rewrite'}

def validate_projection(projection,mapping,documents,case_id,sku,expected_sources=None):
    """Each projected leaf needs its own exact source ref, even when many values are null."""
    leaves=dict(walk_leaves(projection));errors=[];resolved=[]
    if type(expected_sources) is not dict or set(expected_sources)!=set(leaves):
        return {'valid':False,'errors':['HOST_PROJECTION_CONTRACT_REQUIRED'],'resolved':[]}
    if type(mapping) is not dict or set(mapping)!=set(leaves):
        return {'valid':False,'errors':['EXACT_PER_LEAF_MAPPING_REQUIRED'],'resolved':[]}
    for projected_path,value in leaves.items():
        ref=mapping[projected_path]
        expected=expected_sources[projected_path]
        if (type(ref) is not dict or type(expected) is not dict or
            ref.get('document_id')!=expected.get('document_id') or ref.get('path')!=expected.get('path')):
            errors.append('PROJECTION_SOURCE_NOT_ALLOWED:'+projected_path)
        verdict=validate_leaf_reference(ref,documents,case_id,sku)
        errors.extend(verdict['errors'])
        if verdict['valid']:
            actual=verdict['resolved']
            if actual['state']=='ABSENT' or not same(actual.get('value'),value):errors.append('PROJECTION_VALUE_MISMATCH:'+projected_path)
            else:resolved.append({'projected_path':projected_path,'source':actual})
    return {'valid':not errors,'errors':errors,'resolved':resolved}

def validate_received_value(value,doc,path,case_id,sku):
    """Raw whole-object snapshots may be literal nodes; projections are never raw snapshots."""
    try:
        if type(doc) is not BoundDocument or doc.case_id!=case_id or doc.sku!=sku:raise TraceError('CROSS_CASE_OR_SKU')
        if tokens(path)[:len(tokens(doc.root_pointer))]!=tokens(doc.root_pointer):raise TraceError('PATH_OUTSIDE_CASE_SCOPE')
        expected=resolve(doc.data(),path)
        if expected is MISSING or not same(expected,value):raise TraceError('RAW_RECEIVED_NODE_MISMATCH')
        return {'valid':True,'errors':[]}
    except (TraceError,ValueError,TypeError) as error:return {'valid':False,'errors':[str(error)]}
