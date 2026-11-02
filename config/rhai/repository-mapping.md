# RHAI component and repository mapping

Source: [Component Mapping Results](https://docs.google.com/document/d/1Hv34Ci58_BIhWmWJXKE34w2ZlQrQWMeg_fjvMsv4Erk/edit).
Snapshot retrieved: 2026-11-01T14:08:28.378782+00:00.
Source revision: `AHj4eMS4Dy5ONxWF49VeIk2PfSgCM-ISEh75GNSwiH9CiredGYRpmzSJZZB9Q7oPgliqBgmoxWqU7yu_QjROTUJbAgy9ZmWaeM-ekQONCv4`.

The source describes downstream product repositories and artifacts. Contribution
approval is recorded separately in the repository inventory and contribution rules.

## Artifacts

## Red Hat OpenShift AI -- Artifact Tree (v3.5-ea.1, downstream)

### RHOAIENG / AI Core Dashboard

#### red-hat-data-services/odh-dashboard

- odh-dashboard-rhel9

- odh-mod-arch-automl-rhel9

- odh-mod-arch-autorag-rhel9 (embedded)

- odh-mod-arch-eval-hub-rhel9

- odh-mod-arch-gen-ai-rhel9

- odh-mod-arch-maas-rhel9

- odh-mod-arch-mlflow-rhel9

- odh-mod-arch-model-registry-rhel9

### RHOAIENG / AI Core Platform

#### red-hat-data-services/kube-auth-proxy

- odh-kube-auth-proxy-rhel9

#### red-hat-data-services/kube-rbac-proxy

- odh-kube-rbac-proxy-rhel9

#### red-hat-data-services/must-gather

- odh-must-gather-rhel9

#### red-hat-data-services/odh-cli

- odh-cli-rhel9

#### red-hat-data-services/rhods-operator

- odh-rhel9-operator

### RHOAIENG / AI Evaluations

#### red-hat-data-services/eval-hub

- odh-eval-hub-rhel9

#### red-hat-data-services/trustyai-explainability

- odh-trustyai-nemo-guardrails-server-rhel9

- odh-trustyai-service-rhel9

#### red-hat-data-services/trustyai-service-operator

- odh-ta-lmes-driver-rhel9

- odh-trustyai-service-operator-rhel9

### RHOAIENG / AI Hub

#### red-hat-data-services/model-metadata-collection

- odh-model-metadata-collection-rhel9

#### red-hat-data-services/model-registry

- odh-model-registry-job-async-upload-rhel9

- odh-model-registry-rhel9

#### red-hat-data-services/model-registry-operator

- odh-model-registry-operator-rhel9

#### red-hat-data-services/models-perf-benchmark-data

- odh-model-performance-data-rhel9

### RHOAIENG / AI Pipelines

#### red-hat-data-services/argo-workflows

- odh-data-science-pipelines-argo-argoexec-rhel9

- odh-data-science-pipelines-argo-workflowcontroller-rhel9

#### red-hat-data-services/data-science-pipelines

- odh-ml-pipelines-api-server-v2-rhel9

- odh-ml-pipelines-driver-rhel9

- odh-ml-pipelines-launcher-rhel9

- odh-ml-pipelines-persistenceagent-v2-rhel9

- odh-ml-pipelines-scheduledworkflow-v2-rhel9

#### red-hat-data-services/data-science-pipelines-operator

- odh-data-science-pipelines-operator-controller-rhel9

#### red-hat-data-services/ml-metadata

- odh-mlmd-grpc-server-rhel9

#### red-hat-data-services/pipelines-components

- odh-autorag-rhel9 (embedded)

- odh-pipelines-components-rhel9

#### red-hat-data-services/pipelines-components-old

- odh-automl-rhel9

### RHOAIENG / AI Safety

#### red-hat-data-services/fms-guardrails-orchestrator

- odh-fms-guardrails-orchestrator-rhel9

#### red-hat-data-services/guardrails-detectors

- odh-built-in-detector-rhel9

- odh-guardrails-detector-huggingface-runtime-rhel9

#### red-hat-data-services/lm-evaluation-harness

- odh-ta-lmes-job-rhel9

### RHOAIENG / Build and Release

#### red-hat-data-services/RHOAI-Build-Config

- odh-operator-bundle

- rhoai-fbc-fragment (embedded)

### RHOAIENG / Feature Store

#### red-hat-data-services/feast

- odh-feast-operator-rhel9

- odh-feature-server-rhel9

### RHOAIENG / Inference Gateway

#### red-hat-data-services/ai-gateway-payload-processing

- odh-ai-gateway-payload-processing-e2e-rhel9

- odh-ai-gateway-payload-processing-rhel9

### RHOAIENG / KubeRay

#### red-hat-data-services/kuberay

- odh-kuberay-operator-controller-rhel9

### RHOAIENG / Kubeflow Spark Operator

#### red-hat-data-services/spark-operator

- odh-spark-operator-rhel9

### RHOAIENG / MLflow

#### red-hat-data-services/mlflow-operator

- odh-mlflow-operator-rhel9

### RHOAIENG / Model Runtimes

#### red-hat-data-services/MLServer

- odh-mlserver-rhel9

#### red-hat-data-services/openvino_model_server

- odh-openvino-model-server-rhel9

### RHOAIENG / Model Serving

#### red-hat-data-services/kserve

- odh-kserve-agent-rhel9

- odh-kserve-controller-rhel9

- odh-kserve-llmisvc-controller-rhel9

- odh-kserve-localmodel-controller-rhel9

- odh-kserve-localmodelnode-agent-rhel9

- odh-kserve-router-rhel9

- odh-kserve-storage-initializer-rhel9 (embedded)

#### red-hat-data-services/odh-model-controller

- odh-model-controller-rhel9

- odh-model-serving-api-rhel9

#### red-hat-data-services/vllm-orchestrator-gateway

- odh-trustyai-vllm-orchestrator-gateway-rhel9

### RHOAIENG / Model as a Service

#### red-hat-data-services/models-as-a-service

- odh-maas-api-rhel9

- odh-maas-controller-rhel9

### RHOAIENG / Notebooks Server

#### red-hat-data-services/kubeflow

- odh-kf-notebook-controller-rhel9

- odh-notebook-controller-rhel9

#### red-hat-data-services/notebooks

- odh-pipeline-runtime-datascience-cpu-py312-rhel9

- odh-pipeline-runtime-minimal-cpu-py312-rhel9

- odh-pipeline-runtime-pytorch-cuda-py312-rhel9

- odh-pipeline-runtime-pytorch-llmcompressor-cuda-py312-rhel9

- odh-pipeline-runtime-pytorch-rocm-py312-rhel9

- odh-pipeline-runtime-tensorflow-cuda-py312-rhel9

- odh-pipeline-runtime-tensorflow-rocm-py312-rhel9

- odh-workbench-codeserver-datascience-cpu-py312-rhel9

- odh-workbench-jupyter-datascience-cpu-py312-rhel9

- odh-workbench-jupyter-minimal-cpu-py312-rhel9

- odh-workbench-jupyter-minimal-cuda-py312-rhel9

- odh-workbench-jupyter-minimal-rocm-py312-rhel9

- odh-workbench-jupyter-pytorch-cuda-py312-rhel9

- odh-workbench-jupyter-pytorch-llmcompressor-cuda-py312-rhel9

- odh-workbench-jupyter-pytorch-rocm-py312-rhel9

- odh-workbench-jupyter-tensorflow-cuda-py312-rhel9

- odh-workbench-jupyter-tensorflow-rocm-py312-rhel9

- odh-workbench-jupyter-trustyai-cpu-py312-rhel9

### RHOAIENG / Training Kubeflow

#### red-hat-data-services/trainer

- odh-trainer-rhel9

#### red-hat-data-services/training-operator

- odh-training-operator-rhel9

### RHOAIENG / Workload Orchestration

#### red-hat-data-services/distributed-workloads

- odh-th06-cpu-torch210-py312-rhel9

- odh-th06-cpu-torch291-py312-rhel9

- odh-th06-cuda130-torch210-py312-rhel9

- odh-th06-rocm64-torch291-py312-rhel9

- odh-training-cuda121-torch24-py311-rhel9

- odh-training-cuda124-torch25-py311-rhel9

- odh-training-cuda128-torch28-py312-rhel9

- odh-training-cuda128-torch29-py312-rhel9

- odh-training-cuda130-torch210-py312-openmpi41-rhel9

- odh-training-rocm62-torch24-py311-rhel9

- odh-training-rocm62-torch25-py311-rhel9

- odh-training-rocm64-torch28-py312-rhel9

- odh-training-rocm64-torch29-py312-rhel9

### Embedded artifacts from other products

These container images are pulled in via embedded references and belong to other Jira projects/products.

#### RHAIENG / AutoML

##### red-hat-data-services/kserve-autogluon-server

- odh-kserve-autogluon-server-rhel9 (embedded)

#### RHAIENG / Llama Stack Core

##### red-hat-data-services/llama-stack-provider-trustyai-garak

- odh-trustyai-garak-lls-provider-dsp-rhel9 (embedded)

#### RHAIENG / llm-d

##### red-hat-data-services/batch-gateway

- odh-llm-d-batch-gateway-apiserver-rhel9 (embedded)

- odh-llm-d-batch-gateway-gc-rhel9 (embedded)

- odh-llm-d-batch-gateway-processor-rhel9 (embedded)

##### red-hat-data-services/llm-d-inference-scheduler

- odh-llm-d-inference-scheduler-rhel9 (embedded)

##### red-hat-data-services/llm-d-kv-cache

- odh-llm-d-kv-cache-rhel9 (embedded)

##### red-hat-data-services/llm-d-routing-sidecar

- odh-llm-d-routing-sidecar-rhel9 (embedded)

##### red-hat-data-services/workload-variant-autoscaler

- odh-workload-variant-autoscaler-controller-rhel9 (embedded)

##### red-hat-data-services/rhaii-cluster-validation

- odh-rhaii-cluster-validator-rhel9 (embedded)

- odh-rhaii-validator-tools-rhel9 (embedded)

#### (No Jira component mapped)

##### red-hat-data-services/ogx-distribution

- odh-llama-stack-core-rhel9 (embedded)

- odh-ogx-core-rhel9 (embedded)

##### red-hat-data-services/ogx-k8s-operator

- odh-llama-stack-k8s-operator-rhel9 (embedded)

- odh-ogx-k8s-operator-rhel9 (embedded)

Summary: 112 container images across 21 Jira components, 49 GitHub repos, spanning RHOAIENG (direct) and RHAIENG (embedded) projects. All artifacts are quay.io/rhoai registry, downstream tier.

## Repos

## Red Hat OpenShift AI - Repositories

Query: scripts/query.py --rh_product "Red Hat OpenShift AI" --find repos

- Product: Red Hat OpenShift AI

- Version: 3.5-ea.1

- Tier: downstream

- Includes embedded products: yes (Red Hat AI Inference Server)

- Total repos: 95

### Direct Repos (RHOAIENG)

#### AI Core Dashboard

| Repository | URL |
| --- | --- |
| red-hat-data-services/odh-dashboard | https://github.com/red-hat-data-services/odh-dashboard |
| red-hat-data-services/odh-dashboard-sync | https://github.com/red-hat-data-services/odh-dashboard-sync |
| red-hat-data-services/red-hat-odh-dashboard | https://github.com/red-hat-data-services/red-hat-odh-dashboard |

#### AI Core Platform

| Repository | URL |
| --- | --- |
| red-hat-data-services/kube-auth-proxy | https://github.com/red-hat-data-services/kube-auth-proxy |
| red-hat-data-services/kube-rbac-proxy | https://github.com/red-hat-data-services/kube-rbac-proxy |
| red-hat-data-services/must-gather | https://github.com/red-hat-data-services/must-gather |
| red-hat-data-services/odh-cli | https://github.com/red-hat-data-services/odh-cli |
| red-hat-data-services/odh-gitops | https://github.com/red-hat-data-services/odh-gitops |
| red-hat-data-services/rhoai-upgrade-helpers | https://github.com/red-hat-data-services/rhoai-upgrade-helpers |
| red-hat-data-services/rhods-operator | https://github.com/red-hat-data-services/rhods-operator |

#### AI Evaluations

| Repository | URL |
| --- | --- |
| red-hat-data-services/eval-hub | https://github.com/red-hat-data-services/eval-hub |
| red-hat-data-services/eval-hub-sobha | https://github.com/red-hat-data-services/eval-hub-sobha |
| red-hat-data-services/trustyai-explainability | https://github.com/red-hat-data-services/trustyai-explainability |
| red-hat-data-services/trustyai-explainability-python | https://github.com/red-hat-data-services/trustyai-explainability-python |
| red-hat-data-services/trustyai-service-operator | https://github.com/red-hat-data-services/trustyai-service-operator |

#### AI Hub

| Repository | URL |
| --- | --- |
| red-hat-data-services/model-metadata-collection | https://github.com/red-hat-data-services/model-metadata-collection |
| red-hat-data-services/model-registry | https://github.com/red-hat-data-services/model-registry |
| red-hat-data-services/model-registry-bf4-kf | https://github.com/red-hat-data-services/model-registry-bf4-kf |
| red-hat-data-services/model-registry-operator | https://github.com/red-hat-data-services/model-registry-operator |
| red-hat-data-services/models-perf-benchmark-data | https://github.com/red-hat-data-services/models-perf-benchmark-data |

#### AI Pipelines

| Repository | URL |
| --- | --- |
| red-hat-data-services/argo-workflows | https://github.com/red-hat-data-services/argo-workflows |
| red-hat-data-services/data-hub-pipelines | https://github.com/red-hat-data-services/data-hub-pipelines |
| red-hat-data-services/data-science-pipelines | https://github.com/red-hat-data-services/data-science-pipelines |
| red-hat-data-services/data-science-pipelines-operator | https://github.com/red-hat-data-services/data-science-pipelines-operator |
| red-hat-data-services/data-science-pipelines-tekton | https://github.com/red-hat-data-services/data-science-pipelines-tekton |
| red-hat-data-services/ml-metadata | https://github.com/red-hat-data-services/ml-metadata |
| red-hat-data-services/mlmd-bazel-dist | https://github.com/red-hat-data-services/mlmd-bazel-dist |
| red-hat-data-services/pipelines-components | https://github.com/red-hat-data-services/pipelines-components |
| red-hat-data-services/pipelines-components-old | https://github.com/red-hat-data-services/pipelines-components-old |

#### AI Safety

| Repository | URL |
| --- | --- |
| red-hat-data-services/fms-guardrails-orchestrator | https://github.com/red-hat-data-services/fms-guardrails-orchestrator |
| red-hat-data-services/guardrails-detectors | https://github.com/red-hat-data-services/guardrails-detectors |
| red-hat-data-services/guardrails-regex-detector | https://github.com/red-hat-data-services/guardrails-regex-detector |
| red-hat-data-services/lm-evaluation-harness | https://github.com/red-hat-data-services/lm-evaluation-harness |
| red-hat-data-services/NeMo-Guardrails | https://github.com/red-hat-data-services/NeMo-Guardrails |

#### Build and Release

| Repository | URL |
| --- | --- |
| red-hat-data-services/composable-pipelines | https://github.com/red-hat-data-services/composable-pipelines |
| red-hat-data-services/conforma-reporter | https://github.com/red-hat-data-services/conforma-reporter |
| red-hat-data-services/devops-runner-images | https://github.com/red-hat-data-services/devops-runner-images |
| red-hat-data-services/gam-poc | https://github.com/red-hat-data-services/gam-poc |
| red-hat-data-services/Gated-Auto-Merger | https://github.com/red-hat-data-services/Gated-Auto-Merger |
| red-hat-data-services/insta-merge | https://github.com/red-hat-data-services/insta-merge |
| red-hat-data-services/instant-merger | https://github.com/red-hat-data-services/instant-merger |
| red-hat-data-services/jira-escalation-notifier | https://github.com/red-hat-data-services/jira-escalation-notifier |
| red-hat-data-services/konflux-central | https://github.com/red-hat-data-services/konflux-central |
| red-hat-data-services/odh-nightlies-disconnected-install-helper | https://github.com/red-hat-data-services/odh-nightlies-disconnected-install-helper |
| red-hat-data-services/product-pages-generator | https://github.com/red-hat-data-services/product-pages-generator |
| red-hat-data-services/pull-request-pipelines | https://github.com/red-hat-data-services/pull-request-pipelines |
| red-hat-data-services/RDRhelper | https://github.com/red-hat-data-services/RDRhelper |
| red-hat-data-services/rhoai-additional-images | https://github.com/red-hat-data-services/rhoai-additional-images |
| red-hat-data-services/RHOAI-Build-Config | https://github.com/red-hat-data-services/RHOAI-Build-Config |
| red-hat-data-services/RHOAI-Build-Config-rm | https://github.com/red-hat-data-services/RHOAI-Build-Config-rm |
| red-hat-data-services/rhoai-component-infra | https://github.com/red-hat-data-services/rhoai-component-infra |
| red-hat-data-services/rhoai-devops-optima | https://github.com/red-hat-data-services/rhoai-devops-optima |
| red-hat-data-services/rhoai-disconnected-install-helper | https://github.com/red-hat-data-services/rhoai-disconnected-install-helper |
| red-hat-data-services/rhoai-gitops | https://github.com/red-hat-data-services/rhoai-gitops |
| red-hat-data-services/RHOAI-Konflux-Automation | https://github.com/red-hat-data-services/RHOAI-Konflux-Automation |
| red-hat-data-services/rhoai-konflux-tasks | https://github.com/red-hat-data-services/rhoai-konflux-tasks |
| red-hat-data-services/rhoai-ops | https://github.com/red-hat-data-services/rhoai-ops |
| red-hat-data-services/rhods-devops-infra | https://github.com/red-hat-data-services/rhods-devops-infra |
| red-hat-data-services/rhods-disconnected-install-helper | https://github.com/red-hat-data-services/rhods-disconnected-install-helper |
| red-hat-data-services/setup-rhoai | https://github.com/red-hat-data-services/setup-rhoai |

#### Feature Store

| Repository | URL |
| --- | --- |
| red-hat-data-services/feast | https://github.com/red-hat-data-services/feast |

#### Fine Tuning

| Repository | URL |
| --- | --- |
| red-hat-data-services/train-experiment-interviews | https://github.com/red-hat-data-services/train-experiment-interviews |

#### Inference Gateway

| Repository | URL |
| --- | --- |
| red-hat-data-services/ai-gateway-payload-processing | https://github.com/red-hat-data-services/ai-gateway-payload-processing |
| red-hat-data-services/gateway-api-inference-extension | https://github.com/red-hat-data-services/gateway-api-inference-extension |

#### KubeRay

| Repository | URL |
| --- | --- |
| red-hat-data-services/kuberay | https://github.com/red-hat-data-services/kuberay |

#### Kubeflow Spark Operator

| Repository | URL |
| --- | --- |
| red-hat-data-services/spark-operator | https://github.com/red-hat-data-services/spark-operator |

#### MLflow

| Repository | URL |
| --- | --- |
| red-hat-data-services/mlflow | https://github.com/red-hat-data-services/mlflow |
| red-hat-data-services/mlflow-operator | https://github.com/red-hat-data-services/mlflow-operator |

#### Model Runtimes

| Repository | URL |
| --- | --- |
| red-hat-data-services/caikit-nlp | https://github.com/red-hat-data-services/caikit-nlp |
| red-hat-data-services/caikit-tgis-serving | https://github.com/red-hat-data-services/caikit-tgis-serving |
| red-hat-data-services/MLServer | https://github.com/red-hat-data-services/MLServer |
| red-hat-data-services/openvino | https://github.com/red-hat-data-services/openvino |
| red-hat-data-services/openvino_contrib | https://github.com/red-hat-data-services/openvino_contrib |
| red-hat-data-services/openvino_model_server | https://github.com/red-hat-data-services/openvino_model_server |
| red-hat-data-services/text-generation-inference | https://github.com/red-hat-data-services/text-generation-inference |
| red-hat-data-services/vllm | https://github.com/red-hat-data-services/vllm |
| red-hat-data-services/vllm-cpu | https://github.com/red-hat-data-services/vllm-cpu |
| red-hat-data-services/vllm-gaudi | https://github.com/red-hat-data-services/vllm-gaudi |
| red-hat-data-services/vllm-rocm | https://github.com/red-hat-data-services/vllm-rocm |

#### Model Serving

| Repository | URL |
| --- | --- |
| red-hat-data-services/kserve | https://github.com/red-hat-data-services/kserve |
| red-hat-data-services/minio-model-storage | https://github.com/red-hat-data-services/minio-model-storage |
| red-hat-data-services/model-serving-devops | https://github.com/red-hat-data-services/model-serving-devops |
| red-hat-data-services/modelmesh | https://github.com/red-hat-data-services/modelmesh |
| red-hat-data-services/modelmesh-runtime-adapter | https://github.com/red-hat-data-services/modelmesh-runtime-adapter |
| red-hat-data-services/modelmesh-serving | https://github.com/red-hat-data-services/modelmesh-serving |
| red-hat-data-services/odh-model-controller | https://github.com/red-hat-data-services/odh-model-controller |
| red-hat-data-services/rest-proxy | https://github.com/red-hat-data-services/rest-proxy |
| red-hat-data-services/vllm-orchestrator-gateway | https://github.com/red-hat-data-services/vllm-orchestrator-gateway |

#### Model as a Service

| Repository | URL |
| --- | --- |
| red-hat-data-services/models-as-a-service | https://github.com/red-hat-data-services/models-as-a-service |

#### Monitoring

| Repository | URL |
| --- | --- |
| red-hat-data-services/blackbox_exporter | https://github.com/red-hat-data-services/blackbox_exporter |

#### Notebooks Server

| Repository | URL |
| --- | --- |
| red-hat-data-services/jupyterhub-odh | https://github.com/red-hat-data-services/jupyterhub-odh |
| red-hat-data-services/jupyterhub-quickstart | https://github.com/red-hat-data-services/jupyterhub-quickstart |
| red-hat-data-services/jupyterhub-singleuser-profiles | https://github.com/red-hat-data-services/jupyterhub-singleuser-profiles |
| red-hat-data-services/kubeflow | https://github.com/red-hat-data-services/kubeflow |
| red-hat-data-services/notebook-benchmarks | https://github.com/red-hat-data-services/notebook-benchmarks |
| red-hat-data-services/notebooks | https://github.com/red-hat-data-services/notebooks |
| red-hat-data-services/notebooks-downstream | https://github.com/red-hat-data-services/notebooks-downstream |
| red-hat-data-services/notebooks-downstream-z-test | https://github.com/red-hat-data-services/notebooks-downstream-z-test |

#### Training Kubeflow

| Repository | URL |
| --- | --- |
| red-hat-data-services/trainer | https://github.com/red-hat-data-services/trainer |
| red-hat-data-services/training-operator | https://github.com/red-hat-data-services/training-operator |

#### Workload Orchestration

| Repository | URL |
| --- | --- |
| red-hat-data-services/codeflare-operator | https://github.com/red-hat-data-services/codeflare-operator |
| red-hat-data-services/codeflare-sdk | https://github.com/red-hat-data-services/codeflare-sdk |
| red-hat-data-services/distributed-workloads | https://github.com/red-hat-data-services/distributed-workloads |
| red-hat-data-services/kueue | https://github.com/red-hat-data-services/kueue |

### Embedded Repos (RHAIENG - Red Hat AI Inference Server)

#### AgentDev

| Repository | URL |
| --- | --- |
| red-hat-data-services/agentic-starter-kits | https://github.com/red-hat-data-services/agentic-starter-kits |

#### AutoML

| Repository | URL |
| --- | --- |
| red-hat-data-services/autox-ci | https://github.com/red-hat-data-services/autox-ci |
| red-hat-data-services/kserve-autogluon-server | https://github.com/red-hat-data-services/kserve-autogluon-server |

#### Llama Stack Core

| Repository | URL |
| --- | --- |
| red-hat-data-services/llama-stack | https://github.com/red-hat-data-services/llama-stack |
| red-hat-data-services/llama-stack-distribution | https://github.com/red-hat-data-services/llama-stack-distribution |
| red-hat-data-services/llama-stack-k8s-operator | https://github.com/red-hat-data-services/llama-stack-k8s-operator |
| red-hat-data-services/llama-stack-provider-ragas | https://github.com/red-hat-data-services/llama-stack-provider-ragas |
| red-hat-data-services/llama-stack-provider-trustyai-garak | https://github.com/red-hat-data-services/llama-stack-provider-trustyai-garak |
| red-hat-data-services/rhds-llama-stack-distribution | https://github.com/red-hat-data-services/rhds-llama-stack-distribution |

#### llm-d

| Repository | URL |
| --- | --- |
| red-hat-data-services/batch-gateway | https://github.com/red-hat-data-services/batch-gateway |
| red-hat-data-services/llm-d-inference-scheduler | https://github.com/red-hat-data-services/llm-d-inference-scheduler |
| red-hat-data-services/llm-d-kv-cache | https://github.com/red-hat-data-services/llm-d-kv-cache |
| red-hat-data-services/llm-d-routing-sidecar | https://github.com/red-hat-data-services/llm-d-routing-sidecar |
| red-hat-data-services/rhaii-cluster-validation | https://github.com/red-hat-data-services/rhaii-cluster-validation |
| red-hat-data-services/workload-variant-autoscaler | https://github.com/red-hat-data-services/workload-variant-autoscaler |

### Unmapped Repos

These repos exist in tracked GitHub orgs but are not mapped to a Jira component.

| Repository | URL | Tier |
| --- | --- | --- |
| opendatahub-io/autofix-skills | https://github.com/opendatahub-io/autofix-skills | midstream |
| opendatahub-io/knowledge-skills | https://github.com/opendatahub-io/knowledge-skills | midstream |
| opendatahub-io/odh-test-gen | https://github.com/opendatahub-io/odh-test-gen | midstream |
| opendatahub-io/ogx | https://github.com/opendatahub-io/ogx | midstream |
| opendatahub-io/ogx-distribution | https://github.com/opendatahub-io/ogx-distribution | midstream |
| opendatahub-io/ogx-k8s-operator | https://github.com/opendatahub-io/ogx-k8s-operator | midstream |
| opendatahub-io/operator-chaos | https://github.com/opendatahub-io/operator-chaos | midstream |
| red-hat-data-services/devops-images | https://github.com/red-hat-data-services/devops-images | downstream |
| red-hat-data-services/ogx | https://github.com/red-hat-data-services/ogx | downstream |
| red-hat-data-services/ogx-distribution | https://github.com/red-hat-data-services/ogx-distribution | downstream |
| red-hat-data-services/ogx-k8s-operator | https://github.com/red-hat-data-services/ogx-k8s-operator | downstream |
