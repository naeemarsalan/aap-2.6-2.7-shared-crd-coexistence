#!/usr/bin/env python3
"""Merge two revisions of the same CRDs into "superset" CRDs that prune neither side's fields.

Usage: superset_crd.py <manifests-dir-A> <manifests-dir-B> <outdir>

For every CRD present in both directories whose spec differs, write a merged CRD to <outdir>.
B is treated as the newer revision. Merge rules, applied recursively to the OpenAPI schema:
  properties                  union of both sides
  required                    intersection (a field is required only if both sides require it)
  default                     kept if either side sets one (A's value wins if both do)
  format, enum, pattern, ...  kept only if both sides agree, otherwise dropped (looser)
  x-kubernetes-validations    union of rules, de-duplicated by rule text
  description and the rest    B's value
The result is NOT a supported artifact. It only removes API-server pruning as a failure mode.
"""
import sys, os, yaml

TIGHTENING = ("format", "enum", "pattern", "minimum", "maximum", "minLength", "maxLength",
              "minItems", "maxItems", "minProperties", "maxProperties")

def load(d):
    out = {}
    for f in sorted(os.listdir(d)):
        with open(os.path.join(d, f)) as fh:
            o = yaml.safe_load(fh)
        if isinstance(o, dict) and o.get("kind") == "CustomResourceDefinition":
            out[o["metadata"]["name"]] = o
    return out

def merge(a, b):
    if not isinstance(a, dict) or not isinstance(b, dict):
        return b
    out = dict(b)
    for k in a:
        if k not in b and k not in TIGHTENING and k != "required":
            out[k] = a[k]
    if "properties" in a or "properties" in b:
        pa, pb = a.get("properties") or {}, b.get("properties") or {}
        out["properties"] = {k: (merge(pa[k], pb[k]) if k in pa and k in pb else pa.get(k, pb.get(k)))
                             for k in sorted(set(pa) | set(pb))}
    for k in ("items", "additionalProperties"):
        if isinstance(a.get(k), dict) and isinstance(b.get(k), dict):
            out[k] = merge(a[k], b[k])
    req = [r for r in (a.get("required") or []) if r in (b.get("required") or [])]
    out.pop("required", None)
    if req:
        out["required"] = req
    if "default" in a:
        out["default"] = a["default"]
    for k in TIGHTENING:
        if a.get(k) != b.get(k):
            out.pop(k, None)
    if "x-kubernetes-validations" in a or "x-kubernetes-validations" in b:
        seen, rules = set(), []
        for r in (b.get("x-kubernetes-validations") or []) + (a.get("x-kubernetes-validations") or []):
            if r["rule"] not in seen:
                seen.add(r["rule"]); rules.append(r)
        out["x-kubernetes-validations"] = rules
    return out

def main():
    A, B, outdir = load(sys.argv[1]), load(sys.argv[2]), sys.argv[3]
    os.makedirs(outdir, exist_ok=True)
    for name in sorted(set(A) & set(B)):
        if A[name]["spec"] == B[name]["spec"]:
            continue
        crd = {"apiVersion": B[name]["apiVersion"], "kind": "CustomResourceDefinition",
               "metadata": {"name": name}, "spec": dict(B[name]["spec"])}
        va = {v["name"]: v for v in A[name]["spec"]["versions"]}
        vers = []
        for v in B[name]["spec"]["versions"]:
            v = dict(v)
            if v["name"] in va:
                v["schema"] = {"openAPIV3Schema": merge(va[v["name"]]["schema"]["openAPIV3Schema"],
                                                        v["schema"]["openAPIV3Schema"])}
            vers.append(v)
        crd["spec"]["versions"] = vers
        with open(os.path.join(outdir, name + ".yaml"), "w") as fh:
            yaml.safe_dump(crd, fh, sort_keys=False)
        print("merged", name)

if __name__ == "__main__":
    main()
