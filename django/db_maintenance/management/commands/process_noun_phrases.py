from django.core.management.base import BaseCommand
from gregory.models import Articles
import spacy

class Command(BaseCommand):
    help = 'Extracts noun phrases from the titles of Articles and updates the database.'

    def handle(self, *args, **options):
        nlp = spacy.load('en_core_web_sm')
        articles = Articles.objects.filter(noun_phrases__isnull=True)[:10]

        if articles.exists():
            for article in articles:
                self.stdout.write(f"Processing article '{article.title}'...")
                doc = nlp(article.title)
                noun_phrases = [chunk.text for chunk in doc.noun_chunks]
                article.noun_phrases = noun_phrases
                article.save()
                self.stdout.write(f"Updated article '{article.title}' with noun phrases: {noun_phrases}")

        self.stdout.write(self.style.SUCCESS('Successfully processed and updated noun phrases for articles.'))
