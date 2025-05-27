#!/usr/bin/env python
import os
import sys

# Set up Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "admin.settings")
import django
django.setup()

# Now we can import from our app
from gregory.utils.summariser import summarise_bulk

# Simple test
texts = ["Test 1", "Test 2", ""]
print("Input:", texts)
print("Output:", summarise_bulk(texts, batch_size=2))
print("Done!")
