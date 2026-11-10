#!/usr/bin/env bash
# Approve the pending InstallPlan in a namespace and wait for the AAP CSV to succeed.
set -euo pipefail
ns=${1:?namespace}
for i in $(seq 1 30); do ip=$(oc get installplan -n "$ns" -o json | jq -r '[.items[]|select(.spec.approved==false)][0].metadata.name // empty'); [ -n "$ip" ] && break; sleep 5; done
echo "approving $ip: $(oc get installplan "$ip" -n "$ns" -o jsonpath='{.spec.clusterServiceVersionNames}')"
oc patch installplan "$ip" -n "$ns" --type merge -p '{"spec":{"approved":true}}' >/dev/null
for i in $(seq 1 60); do
  p=$(oc get csv -n "$ns" -o json | jq -r '[.items[]|select(.metadata.name|startswith("aap-operator"))|.metadata.name+"="+(.status.phase//"")]|join(" ")')
  ipp=$(oc get installplan "$ip" -n "$ns" -o jsonpath='{.status.phase}')
  echo "  installplan=$ipp csv: $p"
  case "$ipp" in Failed) oc get installplan "$ip" -n "$ns" -o json | jq -r '.status.conditions[]|.message' ; exit 1;; esac
  echo "$p" | grep -q "=Succeeded" && ! echo "$p" | grep -q -E "=(Pending|Installing|InstallReady|Replacing)" && break
  sleep 10
done
