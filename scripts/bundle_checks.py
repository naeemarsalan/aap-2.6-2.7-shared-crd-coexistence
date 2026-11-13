#!/usr/bin/env python3
"""Facts about the extracted bundles that the README relies on but crd_diff.py does not print.

Usage: bundle_checks.py <bundles-dir>     (the output directory of extract-bundles.sh)
"""
import sys, os, json, hashlib, yaml

CH = ("stable-2.6", "stable-2.7", "stable-2.6-cluster-scoped", "stable-2.7-cluster-scoped")

def load(d):
    out = {}
    for f in sorted(os.listdir(d)):
        p = os.path.join(d, f)
        if not os.path.isfile(p):
            continue
        with open(p) as fh:
            o = yaml.safe_load(fh)
        if not isinstance(o, dict):
            continue
        if o.get("kind") == "CustomResourceDefinition":
            out[o["metadata"]["name"]] = o
        elif o.get("kind") == "ClusterServiceVersion":
            out["__csv"] = o
    return out

def sha(o):
    return hashlib.sha256(json.dumps(o["spec"], sort_keys=True).encode()).hexdigest()[:12]

def mdir(root, ch):
    for cand in (os.path.join(root, ch, "image", "manifests"), os.path.join(root, ch, "manifests"), os.path.join(root, ch)):
        if os.path.isdir(cand) and any(f.endswith(".yaml") for f in os.listdir(cand)):
            return cand
    raise SystemExit("no manifests for " + ch)

root = sys.argv[1]
B = {c: load(mdir(root, c)) for c in CH}

print("## CSV upgrade-graph fields")
for c in CH:
    csv = B[c]["__csv"]
    print(f"{c}\t{csv['metadata']['name']}\tskipRange={csv['metadata']['annotations'].get('olm.skipRange')}\t"
          f"replaces={csv['spec'].get('replaces')}\tskips={csv['spec'].get('skips')}")

print("\n## Namespace-scoped vs cluster-scoped bundle of the same release: CRD spec identical?")
for r in ("2.6", "2.7"):
    a, b = B[f"stable-{r}"], B[f"stable-{r}-cluster-scoped"]
    names = sorted(n for n in a if n != "__csv")
    same = [n for n in names if n in b and sha(a[n]) == sha(b[n])]
    print(f"{r}: {len(same)} of {len(names)} CRDs identical")

print("\n## sha256[:12] of each CRD spec as shipped in the bundle (key-sorted JSON; not comparable with")
print("## crd-state.sh hashes, which are taken from the live object after API-server defaulting)")
for n in sorted(x for x in B["stable-2.6"] if x != "__csv"):
    print(f"{n}\t2.6={sha(B['stable-2.6'][n])}\t2.7={sha(B['stable-2.7'][n])}")

print("\n## x-kubernetes-preserve-unknown-fields on the main CRDs (None = closed, unknown fields are pruned)")
for n in ("ansibleautomationplatforms.aap.ansible.com", "edas.eda.ansible.com",
          "automationcontrollers.automationcontroller.ansible.com", "automationhubs.automationhub.ansible.com"):
    for r in ("2.6", "2.7"):
        s = B[f"stable-{r}"][n]["spec"]["versions"][0]["schema"]["openAPIV3Schema"]
        sp = s["properties"]["spec"]
        kids = sorted(k for k, v in sp.get("properties", {}).items() if v.get("x-kubernetes-preserve-unknown-fields"))
        print(f"{n}\t{r}\t.spec={sp.get('x-kubernetes-preserve-unknown-fields')}\t"
              f".status={s['properties'].get('status', {}).get('x-kubernetes-preserve-unknown-fields')}\t"
              f"spec children that preserve: {kids}")

print("\n## CEL rules (x-kubernetes-validations) on edas.eda.ansible.com .spec")
for r in ("2.6", "2.7"):
    s = B[f"stable-{r}"]["edas.eda.ansible.com"]["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["properties"]["spec"]
    for v in s.get("x-kubernetes-validations", []):
        print(f"{r}\t{v['rule']}\t# {v['message']}")
