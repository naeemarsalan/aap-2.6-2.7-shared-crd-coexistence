#!/usr/bin/env bash
# Pull the channel-head AAP operator bundles out of the redhat-operators catalog and
# extract their manifests. Requires: oc (logged in as cluster-admin), grpcurl, jq.
# Usage: ./extract-bundles.sh <outdir> [channel ...]
set -euo pipefail
out=${1:?outdir}; shift || true
channels=("$@"); [ ${#channels[@]} -eq 0 ] && channels=(stable-2.6 stable-2.7 stable-2.6-cluster-scoped stable-2.7-cluster-scoped)
mkdir -p "$out"
authfile=$(mktemp); trap 'rm -f "$authfile"; kill %1 2>/dev/null || true' EXIT
oc get secret pull-secret -n openshift-config -o jsonpath='{.data.\.dockerconfigjson}' | base64 -d > "$authfile"
oc port-forward -n openshift-marketplace svc/redhat-operators 50051:50051 >/dev/null 2>&1 &
sleep 4
for ch in "${channels[@]}"; do
  grpcurl -plaintext -d "{\"pkgName\":\"ansible-automation-platform-operator\",\"channelName\":\"$ch\"}" \
    localhost:50051 api.Registry/GetBundleForChannel > "$out/bundle-$ch.json"
  img=$(jq -r .bundlePath "$out/bundle-$ch.json")
  echo "$ch -> $(jq -r .csvName "$out/bundle-$ch.json") skipRange='$(jq -r .skipRange "$out/bundle-$ch.json")' $img"
  mkdir -p "$out/$ch"
  oc image extract -a "$authfile" "$img" --path /manifests/:"$out/$ch" --confirm
done
