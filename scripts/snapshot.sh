#!/usr/bin/env bash
# Snapshot the pieces that show whether the older operator's objects survive a shared-CRD change.
# Usage: snapshot.sh <namespace> <aap-name>
set -uo pipefail
ns=$1; name=$2
echo "## CSV";  oc get csv -n "$ns" -o json | jq -r '.items[]|select(.metadata.name|startswith("aap-operator"))|[.metadata.name,.status.phase,.status.reason]|@tsv'
echo "## AAP CR spec.eda (parent CR; spec.eda is x-kubernetes-preserve-unknown-fields)"
oc get ansibleautomationplatform "$name" -n "$ns" -o json | jq -c '.spec.eda'
echo "## EDA CR: generation, resourceVersion, spec.scheduler, spec keys only present in one CRD revision"
oc get eda "$name-eda" -n "$ns" -o json | jq -c '{generation:.metadata.generation, resourceVersion:.metadata.resourceVersion, scheduler:.spec.scheduler, event_persistence:.spec.event_persistence, specKeyCount:(.spec|keys|length)}'
echo "## EDA scheduler Deployment actually running"
oc get deploy "$name-eda-scheduler" -n "$ns" -o json 2>/dev/null | jq -c '{generation:.metadata.generation, replicas:.spec.replicas, nodeSelector:.spec.template.spec.nodeSelector, resources:.spec.template.spec.containers[0].resources}' || echo "no scheduler deployment"
echo "## CR conditions"
for k in ansibleautomationplatform/"$name" eda/"$name-eda" automationcontroller/"$name-controller"; do
  oc get "$k" -n "$ns" -o json 2>/dev/null | jq -r --arg k "$k" '[$k]+[.status.conditions[]?|select(.type=="Successful" or .type=="Failure" or .type=="Running")|.type+"="+.status+"("+(.reason//"")+")"]|join("  ")'
done
echo "## pods not Running/Completed"; oc get pods -n "$ns" --no-headers | awk '$3!="Running"&&$3!="Completed"' | wc -l
