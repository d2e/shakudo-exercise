import logging
from typing import Optional
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from kubernetes.config.config_exception import ConfigException
from fastapi import HTTPException, status

from app.models import PodSummary, PodListResponse

logger = logging.getLogger("k8s")
_CORE_V1_API: Optional[client.CoreV1Api] = None


def get_k8s_core_api() -> client.CoreV1Api:
    """Initialize and return a cached Kubernetes CoreV1Api client."""
    global _CORE_V1_API
    if _CORE_V1_API is not None:
        return _CORE_V1_API

    try:
        # 1. Attempt in-cluster config (when running inside a Kubernetes Pod)
        config.load_incluster_config()
        logger.info("Loaded in-cluster Kubernetes ServiceAccount configuration")
    except ConfigException:
        try:
            # 2. Fall back to local kubeconfig (when developing locally or testing)
            config.load_kube_config()
            logger.info("Loaded local ~/.kube/config configuration")
        except Exception as e:
            logger.error(f"Failed to initialize Kubernetes client: {e}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Kubernetes cluster connectivity not configured"
            )

    _CORE_V1_API = client.CoreV1Api()
    return _CORE_V1_API


def list_pods_in_namespace(namespace: str) -> PodListResponse:
    """
    List all pods in the specified namespace using the Kubernetes API.
    Enforces namespace scoping and maps RBAC/connectivity errors.
    """
    api = get_k8s_core_api()
    try:
        pod_list = api.list_namespaced_pod(namespace=namespace, timeout_seconds=10)
        summaries: list[PodSummary] = []

        for pod in pod_list.items:
            # Calculate container readiness ratio
            total_containers = len(pod.spec.containers) if pod.spec and pod.spec.containers else 0
            ready_containers = 0
            restart_count = 0

            if pod.status and pod.status.container_statuses:
                for c_status in pod.status.container_statuses:
                    if c_status.ready:
                        ready_containers += 1
                    restart_count += c_status.restart_count or 0

            ready_str = f"{ready_containers}/{total_containers}" if total_containers > 0 else "0/0"
            created_at_str = pod.metadata.creation_timestamp.isoformat() if pod.metadata and pod.metadata.creation_timestamp else None

            summaries.append(
                PodSummary(
                    name=pod.metadata.name or "unknown",
                    namespace=namespace,
                    phase=pod.status.phase or "Unknown",
                    ready=ready_str,
                    ip=pod.status.pod_ip if pod.status else None,
                    node=pod.spec.node_name if pod.spec else None,
                    restart_count=restart_count,
                    created_at=created_at_str
                )
            )

        return PodListResponse(
            namespace=namespace,
            pod_count=len(summaries),
            pods=summaries
        )

    except ApiException as e:
        logger.warning(f"Kubernetes API error for namespace '{namespace}': status={e.status}, reason={e.reason}")
        if e.status == 403:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"RBAC forbidden: ServiceAccount is not permitted to list pods in namespace '{namespace}'"
            )
        elif e.status == 404:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Namespace '{namespace}' does not exist on cluster"
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Kubernetes API communication failure: {e.reason}"
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error querying Kubernetes API: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal error querying cluster workloads"
        )
