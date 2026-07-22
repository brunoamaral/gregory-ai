"""
Machine Learning package for Gregory AI.

This package contains machine learning algorithms and utilities for the Gregory AI project,
including wrappers for different model architectures, training pipelines, and evaluation tools.
It also provides utilities for pseudo-labeling and semi-supervised learning.
"""

# Public API is loaded lazily (PEP 562): `import gregory.ml` alone must not
# drag in tensorflow/torch/transformers/lightgbm via trainer.py's wrapper
# imports. Those load only when one of these names is actually accessed.
_LAZY_ATTRS = {
	"get_trainer": "gregory.ml.trainer",
	"generate_pseudo_labels": "gregory.ml.pseudo",
	"save_pseudo_csv": "gregory.ml.pseudo",
	"get_pseudo_label_stats": "gregory.ml.pseudo",
	"load_and_filter_pseudo_labels": "gregory.ml.pseudo",
}


def __getattr__(name):
	module_name = _LAZY_ATTRS.get(name)
	if module_name is None:
		raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
	import importlib

	module = importlib.import_module(module_name)
	return getattr(module, name)


def __dir__():
	return sorted(list(globals()) + list(_LAZY_ATTRS))
