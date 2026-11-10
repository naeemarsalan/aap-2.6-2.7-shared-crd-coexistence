#!/usr/bin/env bash
# One line per AAP CRD: generation, sha256 (first 12) of .spec, served versions (storage marked *),
# storedVersions, and which CSVs OLM recorded as "installed alongside".
set -euo pipefail
oc get crd -o json | jq -c '.items[]|select(.spec.group|endswith("ansible.com"))' | while IFS= read -r c; do
  h=$(jq -cS .spec <<<"$c" | sha256sum | cut -c1-12)
  jq -r --arg h "$h" '[.metadata.name, "gen="+(.metadata.generation|tostring), "spec-sha="+$h,
     ([.spec.versions[]|.name+(if .storage then "*" else "" end)]|join(",")),
     "stored="+(.status.storedVersions|join(",")),
     ([.metadata.annotations//{}|to_entries[]|select(.key|startswith("operatorframework.io/installed-alongside"))|.value]|sort|join(" "))]|@tsv' <<<"$c"
done | sort
