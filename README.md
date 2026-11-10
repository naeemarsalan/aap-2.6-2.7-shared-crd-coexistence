# AAP operator 2.6 and 2.7 side by side on one OpenShift cluster: what happens to the shared CRDs

Two namespace-scoped Ansible Automation Platform operators, 2.6 in one namespace and 2.7 in another, on the same OpenShift cluster. The operators are namespaced; their 29 CustomResourceDefinitions are not. This repo records what OLM does with those shared CRDs, tested live, and what it takes to break one side.

## Short answer

It installs and both operators report `Succeeded`, but it is not safe. The 2.6 and 2.7 bundles ship different schemas for 7 of the 29 CRDs under the same version names, with no conversion webhooks, and OLM simply overwrites the cluster-wide CRD with whichever bundle's InstallPlan ran last. There is no version comparison, so 2.7 replaces 2.6's schema and a later 2.6 install or patch update puts the 2.6 schema back.

Each direction damages the other side, and OLM reports nothing:

- **2.7 schema on the cluster:** the API server strips `spec.scheduler` from 2.6 EDA objects. On the next reconcile the 2.6 EDA scheduler Deployment silently reverted to defaults.
- **2.6 schema on the cluster:** the API server strips fields the 2.7 operators depend on. The 2.7 EDA reconcile fails on every run, and user-set 2.7 fields such as `spec.gateway_timeouts` are permanently deleted from the object.

OpenShift's own OLM documentation says different versions of one operator are only supported when they ship the exact same CRD definitions. These two do not.

## Long answer

### What was tested

| | |
|---|---|
| Cluster | OpenShift 4.20.27 (Kubernetes v1.33.12), 3 control plane + 3 workers, disposable |
| Catalog | `redhat-operators`, package `ansible-automation-platform-operator` |
| 2.6 | `aap-operator.v2.6.0-0.1793352877`, channel `stable-2.6` (and `v2.6.0-0.1793137017` for the patch-update test) |
| 2.7 | `aap-operator.v2.7.0-0.1793374941`, channel `stable-2.7` |
| Cluster-scoped | `aap-operator.v2.7.0-0.1793374989`, channel `stable-2.7-cluster-scoped` |
| Instances | One `AnsibleAutomationPlatform` per version with controller and EDA enabled, hub and lightspeed disabled |

Results apply to these builds. Other builds may differ in which fields changed, but the OLM mechanism is the same.

Not covered: automation hub was not deployed (no RWX storage on the test cluster), so the hub findings come from the schema diff and one synthetic API-level test. The removed `ingress_type` defaults were not exercised.

### 1. What the bundles contain

From the bundle images ([`crd-diff/`](crd-diff/), produced by [`scripts/extract-bundles.sh`](scripts/extract-bundles.sh) and [`scripts/crd_diff.py`](scripts/crd_diff.py)):

- Both CSVs own the same 29 CRDs, with 0 required CRDs and no `webhookdefinitions`.
- Every CRD has a single version that is both served and stored, and the version names are identical in 2.6 and 2.7 (`v1alpha1` or `v1beta1` depending on the CRD). No CRD sets `spec.conversion`. There is nothing for the API server to convert between; a field either exists in the current schema or it does not.
- `installModes`: the `stable-2.x` bundles support OwnNamespace, SingleNamespace and MultiNamespace but not AllNamespaces. The `-cluster-scoped` bundles add AllNamespaces. The CRDs in the namespace-scoped and cluster-scoped bundle of the same release are identical.
- Upgrade graph: 2.6 has `olm.skipRange: '>=2.4.0-0.1727257740 <2.6.1'`, 2.7 has `'>=2.6.0-0 <2.7.1'`. Each channel head `replaces` the previous build in its own channel. Nothing in the graph relates a 2.6 install in one namespace to a 2.7 install in another.
- 22 CRDs are schema-identical between 2.6 and 2.7. 7 differ:

| CRD | Difference 2.6 → 2.7 |
|---|---|
| `edas.eda.ansible.com` | `spec.scheduler` removed (43 schema nodes); `spec.event_persistence` and `spec.event_stream.database_secret` added; many `affinity` blocks added; one CEL rule added; `ingress_type` default removed |
| `ansibleautomationplatforms.aap.ansible.com` | `spec.gateway_timeouts` and two status fields added |
| `automationhubs.automationhub.ansible.com` | `spec.secret_key_secret` and `affinity` blocks added; `status.conditions[]` now requires `lastTransitionTime`, `status`, `type`; `ingress_type` default removed |
| `automationhubbackups`, `automationhubrestores` | same `status.conditions[]` tightening |
| `automationcontrollers.automationcontroller.ansible.com` | `ingress_type` default removed |
| `ansiblelightspeeds.lightspeed.ansible.com` | `spec.chatbot_extra_settings` added |

One detail decides how much this matters: the `spec` of the component CRDs (EDA, controller, hub) is a closed structural schema, so unknown fields are pruned by the API server. On the parent `AnsibleAutomationPlatform` CRD, `spec.eda`, `spec.controller` and similar are `x-kubernetes-preserve-unknown-fields`, so the parent object keeps a setting that the child object loses.

### 2. How OLM handles a CRD two CSVs both own

Read from the OLM source shipped in OpenShift 4.20 (`openshift/operator-framework-olm`, branch `release-4.20`, `staging/operator-lifecycle-manager`) and confirmed by the live behaviour below.

- **The InstallPlan CRD step is an overwrite.** If the CRD exists, OLM copies its `resourceVersion` onto the bundle's manifest and calls `Update` with the whole object (`pkg/controller/operators/catalog/step.go`).
- **Two checks gate it, and neither is a version check.**
  - Existing CRs, listed cluster-wide across every namespace, must validate against the incoming OpenAPI schema. Failure message: `error validating existing CRs against new CRD's schema`.
  - Every name in `status.storedVersions` must still exist in the new CRD. Failure message: `risk of data loss updating`.
  - Removed fields, removed defaults and pruning are not checked. OLM does not compare bundle versions, so an older bundle overwrites a newer one.
- **The resolver does not see the conflict.** Resolution is per namespace. The OperatorGroup API conflict check only fires when the groups' target namespaces intersect, which two OwnNamespace groups never do.
- **The CSV requirement check is shallow.** It needs the CRD to exist, be Established, and serve the version name the CSV lists. Since both releases use the same version names, both CSVs stay `Succeeded` whichever schema is installed.
- **OLM never deletes CRDs** when a CSV or Subscription is removed, and it never rolls a CRD back.
- OLM records each installing CSV on the CRD as an `operatorframework.io/installed-alongside-<hash>` annotation. It is bookkeeping for upgrade chains inside one namespace and does not arbitrate between namespaces.

### 3. Live reproduction

Evidence files are in [`evidence/`](evidence/), numbered in the order things happened.

**Step 1. 2.6 in namespace A.** All 29 CRDs created at generation 1. The instance was deployed with EDA scheduler settings (`replicas: 1`, a `node_selector`, custom resource requests), which the running `aap26-eda-scheduler` Deployment reflected. (`01`, `02`)

**Step 2. 2.7 in namespace B.** OLM did not block or warn. The InstallPlan completed, both CSVs `Succeeded`. Exactly the 7 CRDs listed above moved to generation 2 with the 2.7 schema; the other 22 were untouched. (`03`)

**Step 3. Effect on the 2.6 instance.** (`04`, `05`, `06`)

- Immediately, with no write by anyone, `spec.scheduler` disappeared from the 2.6 EDA object (`aap26-eda`). The parent `AnsibleAutomationPlatform` object still showed it under `spec.eda.scheduler`.
- The running Deployment kept its settings until the 2.6 EDA operator next reconciled. After its pod restarted, the Deployment went from `replicas: 1`, the custom node selector and `111m / 211Mi` requests to `replicas: 2`, no node selector and `50m / 256Mi`.
- Every CR still reported `Successful=True`. Nothing in the CSV, the CR conditions or the events indicates that configuration was dropped.
- Putting the field back fails quietly: `oc patch` returns `Warning: unknown field "spec.scheduler"` and `patched (no change)`.

**Step 4. A 2.6 InstallPlan runs after 2.7.** A second 2.6 operator was installed in a third namespace, one patch build behind the channel head, then updated to the head. This models a new 2.6 tenant or a routine 2.6 patch update. (`08`, `09`, `10`, `11`)

- The first 2.6 InstallPlan moved all 7 CRDs to generation 3 with spec hashes identical to step 1. The 2.7 schema was replaced by 2.6's. All three CSVs stayed `Succeeded`.
- On the 2.7 parent object, `spec.gateway_timeouts` was gone.
- On the 2.7 EDA object, `spec.event_persistence` and `spec.event_stream.database_secret` were gone. The second one is set by the 2.7 platform operator on every default install, not by the user.
- The 2.7 EDA operator then failed every reconcile:

  ```
  TASK [postgres : Validate event stream configuration for external database]
  fatal: [localhost]: FAILED! => {"changed": false, "msg": "When using an external database, you must
  provide event stream database credentials. Specify 'event_stream.database_secret' in your EDA CR ..."}
  ```

  The EDA object went to `Failure=True`, `Successful=False`. Existing pods kept running, but the operator could no longer apply any change to that EDA.

**Step 5. Putting the 2.7 CRDs back.** OLM only rewrites CRDs when an InstallPlan runs, so the 7 CRDs were re-applied by hand from the 2.7 bundle. (`13`, `14`)

- The 2.7 EDA recovered by itself in about a minute: the platform operator rewrote the child object from the parent and `Failure` cleared.
- `spec.gateway_timeouts` on the parent did not come back. The pruned object had been persisted, so a user-authored field on a top-level CR is lost until someone re-applies it.
- The restored 2.7 schema is the same one that stripped `spec.scheduler` from the 2.6 EDA object in step 3. With these two builds no single schema serves both operators.

### 4. What breaks, and what it takes

| Trigger | Result | Observed |
|---|---|---|
| Any 2.7 InstallPlan (install or update) while 2.6 instances exist | 2.6 EDA objects lose `spec.scheduler`; scheduler Deployment reverts to defaults on the next 2.6 reconcile. Silent. | Live |
| Any 2.6 InstallPlan (new tenant or patch update) after 2.7 wrote the CRDs | CRDs revert to the 2.6 schema. 2.7 EDA reconcile fails on every run. User-set 2.7 fields are deleted from stored objects. | Live |
| `installPlanApproval: Automatic` on either subscription | The two cases above happen whenever a new build lands in either channel, at a time nobody chose. | Follows from the two rows above; not run with Automatic |
| An existing object anywhere on the cluster that fails the incoming schema | The other tenant's InstallPlan goes `Failed` and its CSV stays `Pending`. | Live, synthetic object (`15`) |
| A cluster-scoped install next to namespace-scoped ones | CSV fails with `InterOperatorGroupOwnerConflict`, but its InstallPlan had already rewritten the shared CRDs. | Live (`16`) |
| Uninstalling one operator (delete Subscription and CSV) | CRDs stay, at whatever schema was written last. That tenant's CRs and workloads stay, unmanaged. The surviving operator keeps running against the other version's schema. | Live (`17`) |
| Deleting a CRD to "finish" a tenant's cleanup | Every tenant's objects of that kind are deleted. | Live (`18`) |

Detail on the last three rows:

- **InstallPlan gate (`15`).** An inert `AutomationHub` object in a namespace no operator watches, with a hand-written status condition lacking `lastTransitionTime`, is valid under the 2.6 CRD and invalid under 2.7. A 2.7 install in a different namespace then failed with `error validating existing CRs against new CRD's schema for "automationhubs.automationhub.ansible.com" ... updated validation is too restrictive: [].status.conditions[0].lastTransitionTime: Required value`. No naturally occurring object hit this between the two tested builds; the test shows the gate exists and that one tenant's object can block another tenant's install.
- **Cluster-scoped (`16`).** The CRDs had been set to the 2.6 schema beforehand. After the failed cluster-scoped 2.7 install they were at the 2.7 schema, with zero operator pods running in that namespace.
- **CRD deletion (`18`).** After the 2.7 operator was uninstalled, `edas.eda.ansible.com` was deleted. The healthy 2.6 tenant's EDA object was removed along with all 8 of its EDA pods, and its platform CR went `Failure=True` with `create not allowed while custom resource definition is terminating`. The CRD then hung in `Terminating`, because the orphaned 2.7 EDA object still carried `eda.ansible.com/finalizer` and its operator no longer existed to clear it.

What did not break between these two builds:

- No storage-version change, so OLM's `risk of data loss` gate never fires and no stored-version migration is involved.
- No object failed validation naturally in either direction.
- The controller CRD differs only by a removed default, and both controllers stayed healthy throughout.

### 5. What the documentation says

AAP 2.6 and 2.7, "Installing the Ansible Automation Platform Operator" (same wording in both):

> If you need multiple operator versions in the same cluster, you must use the namespace-scoped operator.

<https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.7/install-assembly_install_aap_operator>

Neither release's install or upgrade pages mention that the CRDs are shared between namespace-scoped installs, or give an ordering rule for coexisting versions.

OpenShift 4.20, Operators, "Limitations for multitenant Operator management":

> Operators are control plane extensions due to their usage of CustomResourceDefinition objects (CRDs), which are global resources in Kubernetes. Different major versions of an Operator often have incompatible CRDs. This makes them incompatible to install simultaneously in different namespaces on a cluster.

> The supported scenarios include the following: Operators of different versions that ship the exact same CRD definition (in case of versioned CRDs, the exact same set of versions) [...] All other scenarios are not supported, because the integrity of the cluster data cannot be guaranteed if there are multiple competing or overlapping CRDs from different Operator versions to be reconciled on the same cluster.

<https://docs.redhat.com/en/documentation/openshift_container_platform/4.20/html/operators/understanding-operators#olm-operatorgroups-limitations>

The AAP guidance is accurate about what OLM will let you install. The OLM limitation is the one that describes what happens afterwards, and AAP 2.6 plus 2.7 falls outside its supported scenarios because 7 CRDs differ.

### 6. Practical consequences

- Namespace-scoped installs of the **same** AAP operator build share identical CRDs and do not have this problem.
- For 2.6 and 2.7 together, the only arrangement that isolates the CRDs is separate clusters.
- If both must share a cluster for a migration window:
  - Set `installPlanApproval: Manual` on every AAP subscription, so no CRD rewrite happens unplanned.
  - Treat every InstallPlan approval in any AAP namespace as a cluster-wide CRD change.
  - Expect the side whose schema is not installed to be degraded: 2.6 loses EDA scheduler customisation under the 2.7 schema, 2.7 EDA cannot reconcile under the 2.6 schema.
  - Keep the CR manifests in version control, because pruned top-level fields are not recoverable from the cluster.
- Never delete AAP CRDs while any AAP instance remains on the cluster.
- Check which schema is live with [`scripts/crd-state.sh`](scripts/crd-state.sh): CRD generations, a hash of each spec, and the CSVs OLM recorded against each CRD.

### Reproducing

Requires cluster-admin on a disposable cluster, plus `oc`, `jq`, `grpcurl`, `python3` with PyYAML.

```bash
# bundle teardown
scripts/extract-bundles.sh /tmp/aap-bundles
python3 scripts/crd_diff.py /tmp/aap-bundles/stable-2.6 /tmp/aap-bundles/stable-2.7 2.6 2.7

# 2.6 in namespace A
oc apply -f manifests/01-ns-a-aap26.yaml && scripts/approve-installplan.sh aap-crdtest-26
oc apply -f manifests/02-aap-cr-26.yaml           # wait for the instance
scripts/crd-state.sh; scripts/snapshot.sh aap-crdtest-26 aap26

# 2.7 in namespace B: CRDs change, 2.6 EDA object loses spec.scheduler
oc apply -f manifests/03-ns-b-aap27.yaml && scripts/approve-installplan.sh aap-crdtest-27
oc apply -f manifests/04-aap-cr-27.yaml
scripts/crd-state.sh; scripts/snapshot.sh aap-crdtest-26 aap26
# restart the 2.6 EDA operator pod, then snapshot again to see the Deployment drift

# a 2.6 InstallPlan after 2.7: CRDs revert, 2.7 EDA reconcile fails
oc apply -f manifests/05-ns-c-aap26-after-27.yaml && scripts/approve-installplan.sh aap-crdtest-26b
scripts/crd-state.sh; scripts/snapshot.sh aap-crdtest-27 aap27
```

`manifests/06` to `08` are the InstallPlan-gate and cluster-scoped tests. Do not run any of this on a cluster with AAP instances you care about: the first 2.7 InstallPlan changes their CRDs.

### Repository layout

| Path | Contents |
|---|---|
| `manifests/` | Namespaces, OperatorGroups, Subscriptions and AAP CRs used in the tests |
| `scripts/` | Bundle extraction, CRD diff, InstallPlan approval, CRD state and instance snapshot helpers |
| `crd-diff/` | Field-level diff of the 2.6 and 2.7 CRDs: a summary and the full listing |
| `evidence/` | Captured output from each step of the live run |
