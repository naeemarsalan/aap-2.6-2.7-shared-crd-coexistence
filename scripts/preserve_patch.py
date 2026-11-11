#!/usr/bin/env python3
"""Variant of the superset idea: leave each bundle's CRD as shipped, but set
x-kubernetes-preserve-unknown-fields: true on every schema node whose set of child
properties differs between revision A and revision B, so the API server stops pruning there.

Usage: preserve_patch.py <manifests-dir-A> <manifests-dir-B> <outdir>
Writes <outdir>/A/<crd>.yaml and <outdir>/B/<crd>.yaml (patched copies of each side) and
prints the patched node paths. Validation, defaults and CEL rules are left as each side ships
them. NOT a supported artifact.
"""
import sys, os, yaml, copy

def load(d):
    out = {}
    for f in sorted(os.listdir(d)):
        with open(os.path.join(d, f)) as fh:
            o = yaml.safe_load(fh)
        if isinstance(o, dict) and o.get("kind") == "CustomResourceDefinition":
            out[o["metadata"]["name"]] = o
    return out

def walk(a, b, path, hits):
    """Collect paths where the property key sets differ; recurse into shared children."""
    pa, pb = a.get("properties") or {}, b.get("properties") or {}
    if set(pa) != set(pb):
        hits.append(path or ".")
    for k in set(pa) & set(pb):
        if isinstance(pa[k], dict) and isinstance(pb[k], dict):
            walk(pa[k], pb[k], path + "." + k, hits)
    if isinstance(a.get("items"), dict) and isinstance(b.get("items"), dict):
        walk(a["items"], b["items"], path + "[]", hits)

def node(schema, path):
    cur = schema
    for part in [p for p in path.replace("[]", ".[]").split(".") if p]:
        cur = cur["items"] if part == "[]" else cur["properties"][part]
    return cur

def main():
    A, B, out = load(sys.argv[1]), load(sys.argv[2]), sys.argv[3]
    for side in ("A", "B"):
        os.makedirs(os.path.join(out, side), exist_ok=True)
    for name in sorted(set(A) & set(B)):
        if A[name]["spec"] == B[name]["spec"]:
            continue
        ca, cb = copy.deepcopy(A[name]), copy.deepcopy(B[name])
        vb = {v["name"]: v for v in cb["spec"]["versions"]}
        allhits = []
        for v in ca["spec"]["versions"]:
            if v["name"] not in vb:
                continue
            sa, sb = v["schema"]["openAPIV3Schema"], vb[v["name"]]["schema"]["openAPIV3Schema"]
            hits = []
            walk(sa, sb, "", hits)
            for h in hits:
                node(sa, h)["x-kubernetes-preserve-unknown-fields"] = True
                node(sb, h)["x-kubernetes-preserve-unknown-fields"] = True
            allhits += hits
        if not allhits:
            continue
        print(name + ": " + ", ".join(sorted(allhits)))
        for side, c in (("A", ca), ("B", cb)):
            c = {"apiVersion": c["apiVersion"], "kind": c["kind"], "metadata": {"name": name}, "spec": c["spec"]}
            with open(os.path.join(out, side, name + ".yaml"), "w") as fh:
                yaml.safe_dump(c, fh, sort_keys=False)

if __name__ == "__main__":
    main()
