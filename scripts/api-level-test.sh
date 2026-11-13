#!/usr/bin/env bash
# API-server-level tests of CRD pruning with the 2.6 / 2.7 / superset EDA and AAP CRDs.
# No operators involved: this isolates what the API server does to stored objects.
# Usage: api-level-test.sh <2.6-manifests-dir> <2.7-manifests-dir> <superset-dir>
# Needs kubectl-compatible CLI (oc) pointed at a DISPOSABLE cluster with no AAP installed.
set -uo pipefail
A=$1; B=$2; S=$3
EDA=eda.ansible.com_edas.yaml; AAP=aap.ansible.com_ansibleautomationplatforms.yaml
k() { oc "$@"; }
show() { k get eda t -n crdtest -o json | jq -c '{rv:.metadata.resourceVersion,gen:.metadata.generation,scheduler:.spec.scheduler,event_persistence:.spec.event_persistence,es_secret:.spec.event_stream.database_secret,ingress_type:.spec.ingress_type}'; }
est() { k wait --for=condition=Established crd/edas.eda.ansible.com --timeout=30s >/dev/null; sleep 3; }

k create ns crdtest >/dev/null 2>&1
echo "### T1 read-time pruning vs persisted pruning"
k apply --server-side -f $A/$EDA >/dev/null; est
cat <<Y | k apply -f - >/dev/null
apiVersion: eda.ansible.com/v1alpha1
kind: EDA
metadata: {name: t, namespace: crdtest}
spec:
  automation_server_url: https://example.invalid
  scheduler: {replicas: 1}
Y
echo "2.6 CRD, object created:            $(show)"
k apply --server-side --force-conflicts -f $B/$EDA >/dev/null; est
echo "2.7 CRD applied, no write to object: $(show)"
k apply --server-side --force-conflicts -f $A/$EDA >/dev/null; est
echo "2.6 CRD re-applied, still no write:  $(show)"
k apply --server-side --force-conflicts -f $B/$EDA >/dev/null; est
k patch eda t -n crdtest --subresource=status --type=merge -p '{"status":{"probe":"x"}}' >/dev/null
echo "2.7 CRD, after one STATUS write:     $(show)"
k apply --server-side --force-conflicts -f $A/$EDA >/dev/null; est
echo "2.6 CRD re-applied after that write: $(show)"

echo "### T2 putting a pruned field back under the 2.7 CRD"
k apply --server-side --force-conflicts -f $B/$EDA >/dev/null; est
echo "- default field validation (Warn):"
k patch eda t -n crdtest --type=merge -p '{"spec":{"scheduler":{"replicas":1}}}' 2>&1 | sed 's/^/    /'
echo "- strict field validation:"
printf 'apiVersion: eda.ansible.com/v1alpha1\nkind: EDA\nmetadata: {name: t, namespace: crdtest}\nspec:\n  automation_server_url: https://example.invalid\n  scheduler: {replicas: 1}\n' | k apply --validate=strict -f - 2>&1 | grep -o 'unknown field[^,]*' | head -1 | tr -d '\\' | sed 's/^/    rejected: strict decoding error: /'

echo "### T3 superset CRD: neither side's fields are pruned"
k delete eda t -n crdtest >/dev/null
k apply --server-side --force-conflicts -f $S/edas.eda.ansible.com.yaml >/dev/null; est
cat <<Y | k apply -f - >/dev/null
apiVersion: eda.ansible.com/v1alpha1
kind: EDA
metadata: {name: t, namespace: crdtest}
spec:
  automation_server_url: https://example.invalid
  scheduler: {replicas: 1}
  event_persistence: {deploy_db: false}
  event_stream: {database_secret: es-secret}
Y
k patch eda t -n crdtest --subresource=status --type=merge -p '{"status":{"probe":"x"}}' >/dev/null
echo "superset CRD, 2.6 + 2.7 fields, after a status write: $(show)"
echo "- 2.7 CEL rule still enforced under the superset (same secret for both databases must be rejected):"
k patch eda t -n crdtest --type=merge -p '{"spec":{"database":{"database_secret":"es-secret"}}}' 2>&1 | sed 's/^/    /'

echo "### T4 the superset does not survive the next bundle CRD write (what an InstallPlan does)"
k apply --server-side --force-conflicts -f $A/$EDA >/dev/null; est
k patch eda t -n crdtest --subresource=status --type=merge -p '{"status":{"probe":"y"}}' >/dev/null
echo "2.6 bundle CRD written over superset, one status write: $(show)"
k apply --server-side --force-conflicts -f $S/edas.eda.ansible.com.yaml >/dev/null; est
echo "superset re-applied afterwards:                         $(show)"

echo "### T5 parent CR: spec.gateway_timeouts under 2.6 / superset"
k apply --server-side --force-conflicts -f $S/ansibleautomationplatforms.aap.ansible.com.yaml >/dev/null
k wait --for=condition=Established crd/ansibleautomationplatforms.aap.ansible.com --timeout=30s >/dev/null; sleep 3
cat <<Y | k apply -f - >/dev/null
apiVersion: aap.ansible.com/v1alpha1
kind: AnsibleAutomationPlatform
metadata: {name: p, namespace: crdtest}
spec:
  gateway_timeouts: [{gateway_route_name: controller api, request_timeout_seconds: 321}]
  eda: {scheduler: {replicas: 1}, event_persistence: {deploy_db: false}}
Y
k patch ansibleautomationplatform p -n crdtest --subresource=status --type=merge -p '{"status":{"probe":"x"}}' >/dev/null
echo "superset: $(k get ansibleautomationplatform p -n crdtest -o json | jq -c '{gateway_timeouts:.spec.gateway_timeouts,eda:.spec.eda}')"

echo "### T6 hub status.conditions tightening (2.6 -> 2.7), no operator involved"
HUB=automationhub.ansible.com_automationhubs.yaml
k apply --server-side --force-conflicts -f $A/$HUB >/dev/null; k wait --for=condition=Established crd/automationhubs.automationhub.ansible.com --timeout=30s >/dev/null; sleep 3
printf 'apiVersion: automationhub.ansible.com/v1beta1\nkind: AutomationHub\nmetadata: {name: inert, namespace: crdtest}\nspec: {}\n' | k apply -f - >/dev/null
echo "- 2.6 CRD: status condition without lastTransitionTime:"
k patch automationhub inert -n crdtest --subresource=status --type=merge -p '{"status":{"conditions":[{"type":"Successful","status":"True"}]}}' 2>&1 | sed 's/^/    /'
k apply --server-side --force-conflicts -f $B/$HUB >/dev/null; sleep 4
echo "- 2.7 CRD: the same kind of status write:"
k patch automationhub inert -n crdtest --subresource=status --type=merge -p '{"status":{"conditions":[{"type":"Successful","status":"False"}]}}' 2>&1 | sed 's/^/    /'
