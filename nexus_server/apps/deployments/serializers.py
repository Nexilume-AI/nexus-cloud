"""Legacy Source serializer imports use the explicitly selected distribution."""
from .source_inputs import (
    CanonicalModelSerializer,
    CanonicalModelMergeSerializer,
    DeploymentHealthCheckSerializer,
    ModelSourceNewPoolSerializer,
    ModelSourceBatchItemSerializer,
    DeploymentUpdateSerializer,
    DeploymentVisibilitySerializer,
    DeploymentPricingSerializer,
    ModelGroupRoutingSerializer,
    ModelGroupSourceRoutingSerializer,
)
from .presentation import configured_serializer

DeploymentSerializer = configured_serializer("DeploymentSerializer")
DeploymentCreateSerializer = configured_serializer("DeploymentCreateSerializer")
ModelSourceBatchSerializer = configured_serializer("ModelSourceBatchSerializer")
ModelGroupDeploymentSerializer = configured_serializer("ModelGroupDeploymentSerializer")
ModelGroupSerializer = configured_serializer("ModelGroupSerializer")
