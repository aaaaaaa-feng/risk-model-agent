from __future__ import annotations

import importlib
import importlib.util
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence


ModelBuilder = Callable[[Any, Sequence[str], int, int], Any]


def _module_available(name: str) -> bool:
    """Probe a dependency without importing the model library itself."""

    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, AttributeError, ValueError):
        return False


@dataclass(frozen=True)
class ModelAdapterSpec:
    identifier: str
    label: str
    backend: str
    dependencies: tuple[str, ...]
    builder_path: str

    @property
    def available(self) -> bool:
        return all(_module_available(name) for name in self.dependencies)

    def load_builder(self) -> ModelBuilder:
        module_name, separator, attribute = self.builder_path.partition(":")
        if not separator or not module_name or not attribute:
            raise RuntimeError("MODEL_ADAPTER_BUILDER_PATH_INVALID")
        module = importlib.import_module(module_name)
        builder = getattr(module, attribute, None)
        if not callable(builder):
            raise RuntimeError("MODEL_ADAPTER_BUILDER_NOT_CALLABLE")
        return builder


class ModelAdapterRegistry:
    """Typed model factory boundary with dependency probes and lazy builders."""

    def __init__(self, adapters: Mapping[str, ModelAdapterSpec]):
        self._adapters = dict(adapters)
        for identifier, adapter in self._adapters.items():
            if identifier != adapter.identifier:
                raise ValueError("MODEL_ADAPTER_IDENTIFIER_MISMATCH")

    @property
    def identifiers(self) -> tuple[str, ...]:
        return tuple(self._adapters)

    def get(self, identifier: str) -> ModelAdapterSpec:
        try:
            return self._adapters[identifier]
        except KeyError as exc:
            raise ValueError(f"UNSUPPORTED_MODEL: {identifier}") from exc

    def availability(self) -> dict[str, bool]:
        return {identifier: adapter.available for identifier, adapter in self._adapters.items()}

    def capabilities(self) -> list[dict[str, Any]]:
        return [
            {
                "id": adapter.identifier,
                "label": adapter.label,
                "backend": adapter.backend,
                "available": adapter.available,
                "dependencies": list(adapter.dependencies),
            }
            for adapter in self._adapters.values()
        ]

    def build(
        self,
        identifier: str,
        frame: Any,
        features: Sequence[str],
        positive: int,
        negative: int,
    ) -> Any:
        adapter = self.get(identifier)
        if not adapter.available:
            raise ValueError(f"MODEL_DEPENDENCY_UNAVAILABLE: {identifier}")
        return adapter.load_builder()(frame, features, positive, negative)


_BUILDERS = "app.workers.model_builders"
MODEL_ADAPTERS: dict[str, ModelAdapterSpec] = {
    "dummy": ModelAdapterSpec(
        "dummy", "Dummy 基线", "scikit-learn", ("sklearn",), f"{_BUILDERS}:build_dummy"
    ),
    "scorecard": ModelAdapterSpec(
        "scorecard",
        "WOE 逻辑回归评分卡",
        "scikit-learn",
        ("sklearn",),
        f"{_BUILDERS}:build_scorecard",
    ),
    "regularized_logistic": ModelAdapterSpec(
        "regularized_logistic",
        "正则化逻辑回归",
        "scikit-learn",
        ("sklearn",),
        f"{_BUILDERS}:build_regularized_logistic",
    ),
    "random_forest": ModelAdapterSpec(
        "random_forest",
        "随机森林",
        "scikit-learn",
        ("sklearn",),
        f"{_BUILDERS}:build_random_forest",
    ),
    "extra_trees": ModelAdapterSpec(
        "extra_trees",
        "极端随机树",
        "scikit-learn",
        ("sklearn",),
        f"{_BUILDERS}:build_extra_trees",
    ),
    "xgboost": ModelAdapterSpec(
        "xgboost",
        "XGBoost",
        "xgboost",
        ("sklearn", "xgboost"),
        f"{_BUILDERS}:build_xgboost",
    ),
    "lightgbm": ModelAdapterSpec(
        "lightgbm",
        "LightGBM",
        "lightgbm",
        ("sklearn", "lightgbm"),
        f"{_BUILDERS}:build_lightgbm",
    ),
    "catboost": ModelAdapterSpec(
        "catboost",
        "CatBoost",
        "catboost",
        ("sklearn", "catboost"),
        f"{_BUILDERS}:build_catboost",
    ),
}

MODEL_REGISTRY = ModelAdapterRegistry(MODEL_ADAPTERS)


def available_models() -> dict[str, bool]:
    return MODEL_REGISTRY.availability()


def model_capabilities() -> list[dict[str, Any]]:
    return MODEL_REGISTRY.capabilities()
