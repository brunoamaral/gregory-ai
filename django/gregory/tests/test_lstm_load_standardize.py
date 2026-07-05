"""
Regression test for LSTMTrainer.save()/load() round-tripping the
TextVectorization's custom standardize callable.

save() replaces the callable with the string "custom_standardization" so the
vectorizer config can be JSON-serialized. load() must swap that string back
for a real callable before calling TextVectorization.from_config(), or Keras
raises: "Unknown value for `standardize` argument of TextVectorization
... Received: standardize=custom_standardization".
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")

import django

django.setup()

import tempfile
import unittest

import numpy as np

from gregory.ml.lstm_wrapper import LSTMTrainer


TRAIN_TEXTS = [
	"the patient showed improvement after treatment",
	"clinical trial results were positive for the drug",
	"researchers found significant benefit in the study",
	"the therapy reduced symptoms in most patients",
	"new evidence supports the use of this medication",
	"the disease progressed despite the intervention",
	"no significant effect was observed in the trial",
	"the treatment failed to improve patient outcomes",
	"adverse events were reported during the study",
	"the drug showed no benefit over placebo",
]
TRAIN_LABELS = [1, 1, 1, 1, 1, 0, 0, 0, 0, 0]

VAL_TEXTS = [
	"the medication improved patient wellbeing",
	"the study showed no clinical benefit",
]
VAL_LABELS = [1, 0]

SAMPLE_SENTENCE = "The Patient's condition improved, significantly!"


class LSTMLoadStandardizeTest(unittest.TestCase):
	"""save()/load() must round-trip a working standardize callable."""

	def test_load_restores_custom_standardization(self):
		trainer = LSTMTrainer(
			max_tokens=200,
			sequence_length=12,
			embedding_dim=8,
			lstm_units=4,
			bidirectional=False,
		)
		trainer.train(
			train_texts=TRAIN_TEXTS,
			train_labels=TRAIN_LABELS,
			val_texts=VAL_TEXTS,
			val_labels=VAL_LABELS,
			epochs=1,
			batch_size=4,
		)

		# Sanity check: the config saved to disk really does replace the
		# callable with the string, matching production behavior.
		original_output = trainer.vectorizer(np.array([SAMPLE_SENTENCE])).numpy()

		with tempfile.TemporaryDirectory() as tmp_dir:
			trainer.save(tmp_dir)

			loaded_trainer = LSTMTrainer(
				max_tokens=200,
				sequence_length=12,
				embedding_dim=8,
				lstm_units=4,
				bidirectional=False,
			)

			# This must not raise even though the on-disk config has
			# standardize="custom_standardization".
			loaded_trainer.load(tmp_dir)

		loaded_output = loaded_trainer.vectorizer(
			np.array([SAMPLE_SENTENCE])
		).numpy()

		np.testing.assert_array_equal(original_output, loaded_output)


if __name__ == "__main__":
	unittest.main()
