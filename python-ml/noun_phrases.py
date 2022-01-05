#!/usr/bin/python3
import json
import spacy 
import sys

nlp = spacy.load('en_core_web_sm')

text = sys.argv[1]

doc=nlp(str(text))
# Analyze syntax
noun_phrases = [chunk.text for chunk in doc.noun_chunks]

print(json.dumps(noun_phrases))

