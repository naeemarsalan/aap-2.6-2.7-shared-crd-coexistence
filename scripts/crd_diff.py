import sys, os, json, yaml
def load(d):
    out={}
    for f in sorted(os.listdir(d)):
        with open(os.path.join(d,f)) as fh: o=yaml.safe_load(fh)
        if o.get('kind')=='CustomResourceDefinition': out[o['metadata']['name']]=o
        elif o.get('kind')=='ClusterServiceVersion': out['__csv']=o
    return out
def flat(s,p='',acc=None):
    acc={} if acc is None else acc
    if not isinstance(s,dict): return acc
    meta={k:v for k,v in s.items() if k not in('properties','items','description','additionalProperties')}
    acc[p or '.']=meta
    for k,v in (s.get('properties') or {}).items(): flat(v,p+'.'+k,acc)
    if isinstance(s.get('items'),dict): flat(s['items'],p+'[]',acc)
    if isinstance(s.get('additionalProperties'),dict): flat(s['additionalProperties'],p+'{}',acc)
    return acc
a=load(sys.argv[1]); b=load(sys.argv[2]); la,lb=sys.argv[3],sys.argv[4]
for side,l in((a,la),(b,lb)):
    c=side['__csv']; s=c['spec']
    print(f"## CSV {l}: {c['metadata']['name']}")
    print("  installModes:",{m['type']:m['supported'] for m in s['installModes']})
    print("  olm.skipRange:",c['metadata']['annotations'].get('olm.skipRange'),"| replaces:",s.get('replaces'),"| skips:",s.get('skips'))
    print("  owned CRDs:",len(s['customresourcedefinitions'].get('owned',[])),"required:",len(s['customresourcedefinitions'].get('required',[]) or []))
    print("  webhookdefinitions:",[ (w.get('type'),w.get('generateName')) for w in s.get('webhookdefinitions',[]) or []])
    print("  deployments:",[d['name'] for d in s['install']['spec']['deployments']])
    print("  annotations:",{k:v for k,v in c['metadata']['annotations'].items() if k.startswith('operators.openshift.io/') or k in('operatorframework.io/suggested-namespace','olm.properties')})
names=sorted((set(a)|set(b))-{'__csv'})
print(f"\n## CRDs: {la}={len(a)-1} {lb}={len(b)-1}")
for n in names:
    if n not in a: print(f"ONLY in {lb}: {n}"); continue
    if n not in b: print(f"ONLY in {la}: {n}"); continue
    A,B=a[n]['spec'],b[n]['spec']
    va={v['name']:v for v in A['versions']}; vb={v['name']:v for v in B['versions']}
    def vs(vv): return ','.join(k+('*' if v.get('storage') else '')+('' if v.get('served') else '(unserved)') for k,v in vv.items())
    line=f"{n}: versions {la}=[{vs(va)}] {lb}=[{vs(vb)}] conv {la}={(A.get('conversion') or {}).get('strategy','None(default)')} {lb}={(B.get('conversion') or {}).get('strategy','None(default)')} scope={A['scope']}/{B['scope']}"
    diffs=[]
    for ver in sorted(set(va)|set(vb)):
        if ver not in va or ver not in vb: diffs.append(f"  version {ver} only in {'B' if ver not in va else 'A'}"); continue
        fa=flat((va[ver].get('schema') or {}).get('openAPIV3Schema') or {}); fb=flat((vb[ver].get('schema') or {}).get('openAPIV3Schema') or {})
        for p in sorted(set(fa)-set(fb)): diffs.append(f"  [{ver}] REMOVED in {lb}: {p} {json.dumps(fa[p],sort_keys=True)[:160]}")
        for p in sorted(set(fb)-set(fa)): diffs.append(f"  [{ver}] ADDED in {lb}: {p} {json.dumps(fb[p],sort_keys=True)[:160]}")
        for p in sorted(set(fa)&set(fb)):
            if fa[p]!=fb[p]: diffs.append(f"  [{ver}] CHANGED {p}: {json.dumps(fa[p],sort_keys=True)[:200]} -> {json.dumps(fb[p],sort_keys=True)[:200]}")
        sa,sb=va[ver].get('subresources'),vb[ver].get('subresources')
        if sa!=sb: diffs.append(f"  [{ver}] subresources {sa} -> {sb}")
    print(line+("  IDENTICAL-SCHEMA" if not diffs else f"  ({len(diffs)} schema diffs)"))
    for d in diffs: print(d)
