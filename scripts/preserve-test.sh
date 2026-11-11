#!/usr/bin/env bash
# API-level test of the preserve-unknown-fields variant. Usage: preserve-test.sh <preserve-outdir>
set -uo pipefail
P=$1
show() { oc get eda t -n crdtest -o json | jq -c '{rv:.metadata.resourceVersion,scheduler:.spec.scheduler,event_persistence:.spec.event_persistence,es_secret:.spec.event_stream.database_secret}'; }
ap() { oc apply --server-side --force-conflicts -f "$1" >/dev/null; oc wait --for=condition=Established crd/edas.eda.ansible.com --timeout=30s >/dev/null; sleep 3; }
oc create ns crdtest >/dev/null 2>&1; oc delete eda t -n crdtest --ignore-not-found >/dev/null
ap $P/B/edas.eda.ansible.com.yaml
cat <<Y | oc apply -f - >/dev/null
apiVersion: eda.ansible.com/v1alpha1
kind: EDA
metadata: {name: t, namespace: crdtest}
spec:
  automation_server_url: https://example.invalid
  scheduler: {replicas: 1}
  event_persistence: {deploy_db: false}
  event_stream: {database_secret: es-secret}
Y
oc patch eda t -n crdtest --subresource=status --type=merge -p '{"status":{"probe":"1"}}' >/dev/null
echo "patched 2.7 CRD, after a status write: $(show)"
ap $P/A/edas.eda.ansible.com.yaml
oc patch eda t -n crdtest --subresource=status --type=merge -p '{"status":{"probe":"2"}}' >/dev/null
echo "patched 2.6 CRD, after a status write: $(show)"
ap $P/B/edas.eda.ansible.com.yaml
oc patch eda t -n crdtest --subresource=status --type=merge -p '{"status":{"probe":"3"}}' >/dev/null
echo "patched 2.7 CRD again, status write:   $(show)"
